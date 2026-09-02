"""Memory on the context graph: retain, recall, reflect.

Hindsight's operations over the graph that is already here, rather than a
second system beside it. The pieces the surveyed options charge infrastructure
for -- Graphiti wants a Neo4j, Hindsight an embedded Postgres, Supermemory its
own store -- are already in `db.py`: typed nodes, typed edges, and an
append-only delta log with a logical clock. What was missing is retrieval and
the memory node type. That is what this adds.

Three deliberate departures from the surveyed designs:

**Extraction runs in the session, not in a subprocess.** Every one of those
systems calls an LLM at retain time. Spawning one here was tried three times
and is unusable in practice (a nested `claude -p` takes minutes or dies on
auth), and it would send memory content somewhere the conversation had not
already been. The agent holding the conversation already knows what happened;
it calls `retain` itself.

**Recall is deterministic.** BM25 over FTS5 with a recency prior, no model in
the path. Recall runs at session start and on demand, so a model call there
would tax every single session for a lookup that ranking already does.

**Nothing is deleted on being superseded.** A memory that turned out wrong is
linked to the one that replaced it and kept. The delta log is the point: what
was believed, when, and what changed it.

Memory is global (`~/.loopgraph/memory.db`), unlike the per-repo criteria
graph -- a trap learned in one repo is worth the most in a different one.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

from .db import SCHEMA, emit_delta, meta_get, meta_set, utcnow
from .graph import get_node, link

KINDS = ("world", "experience", "model")

# The markdown corpus is the writer; this database is an index over it.
#
# Two stores diverged within one session of having both: `MEMORY.md` is what
# Claude Code loads natively at the start of every session, so a memory that
# exists only in sqlite is invisible to the thing that actually primes the
# context, and a memory written as markdown is invisible here until an import.
# Files also inherit the backups, the diffing and the greppability that a
# single sqlite blob in ~/.loopgraph has none of.
#
# So: every retain writes a file first and indexes second, and the index can
# be thrown away and rebuilt from the files at any time.
def _default_corpus() -> str:
    """Where Claude Code keeps this machine's memory files.

    Claude Code names a project directory after the absolute path it was
    started in, with the separators turned into dashes: `/Users/you` becomes
    `-Users-you`. Derived rather than hardcoded so the corpus is found on any
    machine; `LOOPGRAPH_MEMORY_CORPUS` overrides it for a different layout.
    """
    home = os.path.expanduser("~")
    slug = home.replace(os.sep, "-")
    return os.path.join(home, ".claude", "projects", slug, "memory")


DEFAULT_CORPUS = os.environ.get("LOOPGRAPH_MEMORY_CORPUS") or _default_corpus()

KIND_TO_TYPE = {"world": "reference", "experience": "project", "model": "feedback"}

# Recall scope. One memory store is reachable from every harness on the
# machine, and those harnesses do not all run the same vendor's model. A
# memory extracted in a session that already saw the work must not be handed
# to a different vendor just because recall was cheap.
#
# Default-deny: `safe` unless a harness is explicitly trusted with `full`.
SCOPES = ("safe", "full")

# Patterns that identify *anyone's* private work. These ship with the tool.
#
# What they deliberately do not contain is the thing that actually makes an
# operational note identifying: the names of your employer, your clients, your
# clusters. Those are yours, they differ per operator, and a list of them
# committed to a public repository is itself the leak it was written to
# prevent. Put them in `~/.loopgraph/sensitive.toml` -- see
# `load_sensitive_patterns` -- where they stay on your machine.
# `token` is a homonym, and the two senses are not equally private: a bearer
# credential, and the unit model cost is measured in. Folding them together
# filed a memory about output-token accounting as credential material and
# withheld it from every safe-scope harness -- on an operator whose work is
# largely token accounting, that quietly hides a whole subject rather than a
# secret. The measured senses are excluded by name; every other use of the
# word still classifies, so "refresh token", "bearer token" and a bare token
# in a note about where it lives are all still caught. Dropping the trailing
# \w* also drops "tokenizer" and "tokenization", which are never credentials.
_TOKEN_MEASURE_WORDS = ("output", "input", "context", "cache", "prompt",
                        "completion", "cached", "uncached")
_TOKEN_PATTERN = (
    "(?i)"
    # one fixed-width lookbehind each: Python will not take an alternation of
    # differing widths in a single one.
    + "".join(rf"(?<!{w} )" for w in _TOKEN_MEASURE_WORDS)
    # A count is never a credential. "123 tokens", "~50k tokens", "2M tokens":
    # the qualifier form was covered first and this bare one was not, so a
    # memory saying "the brief costs ~123 tokens" was still filed as
    # credential material. Two chars of fixed-width lookbehind is enough --
    # only the digit or magnitude suffix immediately before the space matters.
    + r"(?<![\dkKmMbB] )"
    # Ordered alternation: "tokens" / a bare "token" / "token_url", but never
    # "tokenizer" -- no word boundary after "token" there, and "i" is not a
    # separator, so every branch correctly fails.
    + r"\btoken(?:s\b|\b|[-_]\w+)"
    + r"(?!\s*(?:/|per\b|count\b|budget\b|spent\b|remaining\b))"
)

# Measured 2026-08-17: 101 of 214 memories classified sensitive, and held-out
# recall@5 at scope=safe was HALF what it was at full (8/20 vs 15/20). Every
# harness that is not trusted with client content therefore lost about half
# the corpus. Over-classifying is the right default, but these five were
# matching things that identify nobody -- a version number read as an IP, a
# public vendor endpoint read as an internal one -- and each one costs real
# recall in every other harness. Narrowed with the leak direction, not the
# convenience direction, in mind.
GENERIC_PATTERNS: list[tuple[str, str]] = [
    # A dotted quad is an IP unless it is a version. "macOS 6.2.0.9" and
    # "logstash 8.14.0.1" identify no host.
    (r"(?<![\w.])(?<!v)(?<!version )(?<!release )(?<!Version )"
     r"\b(?:\d{1,3}\.){3}\d{1,3}\b(?!\.\d)", "an IP address"),
    # A bare service prefix in prose ("arn:aws:s3:") names nothing. Require a
    # region/account tail, which is the part that identifies an estate.
    (r"\barn:aws:[a-z0-9-]+:[a-z0-9-]*:[^\s:]+", "an AWS ARN"),
    (r"\b\d{12}\b", "an AWS account id"),
    (r"(?i)\b(?:password|passwd|secret|api[-_ ]?key|credential|private[-_ ]key|"
     r"\.pem\b|easy-rsa|pki/)\w*", "credential material or its location"),
    (_TOKEN_PATTERN, "credential material or its location"),
    # git@ and noreply@ on a public forge are addresses of a service, not of
    # a person, and appear in every clone URL.
    (r"(?i)(?<![\w.+-])(?!git@|noreply@|no-reply@)[\w.+-]+@"
     r"(?!github\.com|gitlab\.com)[\w-]+\.[\w.]+\b", "an email address"),
    # The bare suffix is generic Kubernetes DNS. A service and namespace in
    # front of it is what names somebody's cluster.
    (r"(?i)\b[\w-]+\.[\w-]+\.svc\.cluster\.local\b|\bkubectl\s+-n\s+\S+",
     "internal cluster detail"),
    (r"(?i)\b(?:client|tenant|customer)\b.{0,40}\b(?:rfp|audit|pursuit|onboard)",
     "a named client engagement"),
    # Public vendor endpoints are documentation, not an estate. Everything
    # else on a real TLD still classifies.
    (r"(?i)\bhttps?://(?!(?:docs\.|www\.|login\.|management\.|sts\.)?"
     r"(?:anthropic|github|gitlab|arxiv|python|clickhouse|opensearch|"
     r"microsoft|windows|azure|usgovcloudapi|amazonaws|googleapis|"
     r"kubernetes|elastic)\.)"
     r"[a-z0-9.-]+\.(?:com|net|org|io|us|gov)\b",
     "an internal or client URL"),
]

# (path, mtime_ns, size) -> compiled patterns. Reloaded when the file changes,
# so editing the config takes effect without restarting a long-lived harness.
_SENSITIVE_CACHE: dict[tuple, list[tuple[str, str]]] = {}
_WARNED: set[tuple] = set()


def sensitive_config_path() -> str:
    return os.environ.get("LOOPGRAPH_SENSITIVE_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".loopgraph", "sensitive.toml")


def load_sensitive_patterns(path: str | None = None) -> list[tuple[str, str]]:
    """Operator-supplied terms, from TOML. Missing file means an empty list.

        # ~/.loopgraph/sensitive.toml
        terms = ["acme", "globex", "orion-prod"]
        terms_why = "an internal system or client name"

        [[pattern]]
        regex = '\\b(?:ACME|GLBX)[-_][A-Z0-9-]{2,}\\b'
        why = "a client host or tenant code"

    `terms` are literal words matched case-insensitively on word boundaries --
    an explicit list, not "any three capitals," which would fire on AWS, SQL,
    CPU and every other acronym in the corpus. A classifier that marks
    everything teaches people to run `--scope full` by reflex, which is worse
    than having no classifier at all.

    A config that cannot be read is announced on stderr and skipped, never
    silently ignored: a redactor that quietly stops redacting looks exactly
    like one with nothing to redact.
    """
    import tomllib

    path = path or sensitive_config_path()
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (path, st.st_mtime_ns, st.st_size)
    if key in _SENSITIVE_CACHE:
        return _SENSITIVE_CACHE[key]

    def warn(msg: str) -> None:
        if key not in _WARNED:
            _WARNED.add(key)
            print(f"loopgraph: {path}: {msg}", file=sys.stderr)

    try:
        with open(path, "rb") as fh:
            conf = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        warn(f"unreadable, operator patterns NOT applied ({e})")
        return []

    out: list[tuple[str, str]] = []
    terms = [t for t in conf.get("terms", []) if isinstance(t, str) and t.strip()]
    if terms:
        why = conf.get("terms_why") or "an internal system or client name"
        joined = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
        out.append((rf"(?i)(?<![\w-])(?:{joined})(?![\w-])", why))
    for i, p in enumerate(conf.get("pattern", [])):
        regex, why = p.get("regex"), p.get("why") or "an operator-defined pattern"
        if not regex:
            warn(f"pattern #{i + 1} has no regex, skipped")
            continue
        try:
            re.compile(regex)
        except re.error as e:
            warn(f"pattern #{i + 1} is not a valid regex, skipped ({e})")
            continue
        out.append((regex, why))

    _SENSITIVE_CACHE[key] = out
    return out


def sensitive_patterns() -> list[tuple[str, str]]:
    return GENERIC_PATTERNS + load_sensitive_patterns()


def sensitivity(text: str) -> list[str]:
    """Why this memory should not leave a trusted harness. Empty means safe.

    Deliberately over-inclusive. Under-classifying leaks an identifier to a
    third-party model; over-classifying costs a recall that the operator can
    always unlock with --scope full. Those are not symmetric mistakes.
    """
    return sorted({why for pat, why in sensitive_patterns()
                   if re.search(pat, text)})


def scope_default() -> str:
    s = os.environ.get("LOOPGRAPH_MEM_SCOPE", "").strip().lower()
    return s if s in SCOPES else "safe"

# FTS5 with porter stemming: "logger cutover" should reach "loggers cut over".
# A standalone table rather than an external-content one -- the node rows are
# edited in place by supersede(), and keeping the index self-contained means
# one write path instead of three triggers to get wrong.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    id UNINDEXED, text, tags, tokenize='porter unicode61'
);
"""

RECENCY_HALF_LIFE_DAYS = 120.0


def default_memory_db() -> str:
    """One store for every repo and every harness.

    The criteria graph is per-repo because a specification is about one tree.
    A memory is not: "glab mr merge lies" cost three rediscoveries in one day
    across three different repos.
    """
    d = os.path.join(os.path.expanduser("~"), ".loopgraph")
    os.makedirs(d, exist_ok=True)
    return os.environ.get("LOOPGRAPH_MEMORY_DB") or os.path.join(d, "memory.db")


def open_memory(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or default_memory_db(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.executescript(FTS_SCHEMA)
    return conn


def _slug(text: str, taken: set[str] | None = None) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "memory"
    if taken is None:
        return base
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _next_id(conn: sqlite3.Connection, text: str) -> str:
    base = _slug(text)
    candidate, n = base, 2
    while get_node(conn, candidate) is not None:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def retain(
    conn: sqlite3.Connection,
    text: str,
    kind: str = "world",
    tags: tuple[str, ...] | list[str] = (),
    source: str = "",
    id: str | None = None,
    created_at: str | None = None,
) -> str:
    """Store one memory. Returns its id.

    `kind` follows Hindsight's three pathways: `world` for how things are,
    `experience` for what happened to us, `model` for what we concluded.
    Keeping them apart is what lets recall prefer a conclusion over the
    twenty observations behind it.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("a memory needs text")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    mid = id or _next_id(conn, text)
    # An imported memory keeps the date it was learned. Without this a
    # reindex stamps every memory with the rebuild time -- observed: all 214
    # dated the same day -- which silently turns the recency prior, the
    # second-largest term in the ranking, into an identical constant.
    now = created_at or utcnow()
    tags = tuple(t.strip() for t in tags if t.strip())
    sensitive = sensitivity(text)
    conn.execute(
        "INSERT INTO nodes (id, type, statement, expect_json, timeout_s, "
        "is_goal, created_at, updated_at) "
        "VALUES (?, 'memory', ?, '{}', 120, 0, ?, ?)",
        (mid, text, now, now),
    )
    _set_mem_meta(conn, mid, {"kind": kind, "tags": list(tags), "source": source,
                              "sensitive": sensitive})
    conn.execute("INSERT INTO memory_fts (id, text, tags) VALUES (?, ?, ?)",
                 (mid, text, " ".join(tags)))
    emit_delta(conn, mid, "MEMORY_RETAINED", None, text[:200])
    meta_set(conn, "memories", str(int(meta_get(conn, "memories", "0")) + 1))
    autolink(conn, mid, text)
    return mid


# A link the writer has to remember to type is a link that does not get
# typed: 141 of 214 memories here had none, including three about the same
# host pool retained days apart. The graph was a pile of documents wearing
# the word graph. These are marked auto so a curated `[[link]]` stays
# distinguishable from a guess.
TITLE_WEIGHT = 2.0
AUTOLINK_MAX = 4
AUTOLINK_MIN_COVERAGE = 0.34
AUTOLINK_MAX_DEGREE = 8


def autolink(conn: sqlite3.Connection, mid: str, text: str,
             max_links: int = AUTOLINK_MAX,
             min_coverage: float = AUTOLINK_MIN_COVERAGE) -> list[str]:
    """Connect a new memory to the ones it is about, by its own words.

    Deliberately conservative and lexical: same BM25 that powers recall, with
    a coverage floor so a shared common word is not a relationship. Wrong
    links are worse than missing ones -- they widen every future expansion --
    so this prefers to add nothing.

    Two defences against hubs, which one-directional similarity produces on
    its own. Sampled after the first pass, about half the links were wrong,
    and the pattern was obvious: abstract notes match everybody.
    `feedback_dead_but_looks_alive` -- a note about a failure CLASS, phrased
    in words every incident shares -- had reached degree 33 against a median
    of 4, so it was being dragged into expansions for AMH forwarders and
    noexec mounts alike.

    - Mutual: B is only linked from A if A is also among B's own best hits.
      A hub matches everyone; almost no one is what the hub is about.
    - Degree cap: nothing accumulates auto-links without limit, so one very
      quotable memory cannot become the graph's centre of gravity.
    """
    try:
        hits = recall(conn, text, k=max_links + 1, scope="full")
    except Exception:
        return []
    made = []
    for h in hits:
        if h["id"] == mid or len(made) >= max_links:
            continue
        if h.get("coverage", 0) < min_coverage:
            continue
        if _auto_degree(conn, h["id"]) >= AUTOLINK_MAX_DEGREE:
            continue                      # already a hub; do not feed it
        if not _is_mutual(conn, h["id"], mid, max_links):
            continue
        try:
            relate(conn, mid, h["id"])
            made.append(h["id"])
        except Exception:
            continue
    return made


def _auto_degree(conn: sqlite3.Connection, mid: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM edges WHERE src = ? OR dst = ?", (mid, mid)
    ).fetchone()
    return row[0] if row else 0


def _is_mutual(conn: sqlite3.Connection, other: str, mid: str, k: int) -> bool:
    """Is `mid` among `other`'s own best matches?

    One-directional similarity is what builds hubs: an abstract note is
    close to everything, while almost nothing is close to it. Requiring the
    relationship to hold in both directions removes exactly that asymmetry.
    """
    row = conn.execute(
        "SELECT statement FROM nodes WHERE id = ? AND type = 'memory'",
        (other,)).fetchone()
    if row is None:
        return False
    try:
        back = recall(conn, row["statement"], k=k + 2, scope="full")
    except Exception:
        return False
    return mid in {h["id"] for h in back}


def _ensure_meta_col(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(nodes)")}
    if "meta_json" not in cols:
        conn.execute(
            "ALTER TABLE nodes ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'")


def _set_mem_meta(conn: sqlite3.Connection, mid: str, meta: dict) -> None:
    _ensure_meta_col(conn)
    conn.execute("UPDATE nodes SET meta_json = ?, updated_at = ? WHERE id = ?",
                 (json.dumps(meta, sort_keys=True), utcnow(), mid))


def mem_meta(conn: sqlite3.Connection, mid: str) -> dict:
    _ensure_meta_col(conn)
    row = conn.execute("SELECT meta_json FROM nodes WHERE id = ?", (mid,)).fetchone()
    return json.loads(row["meta_json"]) if row and row["meta_json"] else {}


def _recency(created_at: str, now: datetime) -> float:
    """Newer memories win ties. Half-life, not a cliff: a two-year-old trap
    that still matches every word of the query should still be reachable."""
    try:
        age_days = (now - datetime.fromisoformat(created_at)).total_seconds() / 86400
    except Exception:
        return 0.0
    return 0.5 ** (max(0.0, age_days) / RECENCY_HALF_LIFE_DAYS)


# Not linguistics -- just the words that make "hello can you help me" score
# as high as a real question against long documents.
STOPWORDS = frozenset("""
a an the and or but if then than that this these those is are was were be been
being do does did doing have has had having i you he she it we they me him her
us them my your our their what which who whom when where why how all any both
each few more most other some such no nor not only own same so too very can
will just should now about into over under again once here there please help
need want get got make made use using with without from for of to in on at by
way ways while thing things also even still much many like know think maybe
perhaps really quite might could would let going thanks okay yeah yes hey
""".split())


def _terms(query: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[A-Za-z0-9_./-]+", query)
            if len(t) > 2 and t.lower() not in STOPWORDS]



# Vocabulary bridging, without a model in the path.
#
# BM25 matches words, and a question is asked in the asker's words while the
# corpus is written in the estate's: "customer" for notes that say "tenant",
# "remote desktop" for notes that say AVD, "amazon" for notes that say S3.
# Measured on a held paraphrase set, that gap cost a third of realistic
# queries -- they returned something confidently irrelevant, which is worse
# than returning nothing. Link expansion does not rescue those: when the seed
# hit is already wrong, so is everything one edge from it.
#
# An alias table is the deterministic version of what an embedding would do
# here, and this estate's vocabulary is small and stable enough to write down.
# Operators extend it in ~/.loopgraph/aliases.toml, same as sensitive.toml --
# the shipped list stays generic, anything client-specific stays local.
DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "customer": ("tenant", "client"),
    "client": ("tenant", "customer"),
    "tenant": ("customer", "client"),
    "amazon": ("aws", "s3"),
    "bucket": ("s3",),
    "archive": ("retention", "glacier", "lake"),
    "desktop": ("avd", "vdi", "workspace"),
    "vm": ("instance", "host", "node"),
    "siem": ("sentinel", "opensearch", "splunk"),
    "cost": ("spend", "billing", "price"),
    "price": ("cost", "pricing", "spend"),
    "expensive": ("cost", "spend", "tokens"),
    "restart": ("reboot", "bounce", "cutover"),
    "restarted": ("reboot", "rebooted"),
    "crash": ("oom", "crashloop", "wedge"),
    "disk": ("var", "volume", "filesystem", "storage"),
    "full": ("exhausted", "100%"),
    "limit": ("timeout", "cap", "quota"),
    "bound": ("timeout", "cap", "limit"),
    "slow": ("latency", "lag"),
    "broken": ("failing", "degraded"),
    "silent": ("dead", "no-op", "wallpaper"),
    "context": ("tokens", "window"),
    "turn": ("session", "prompt"),
    "switch": ("cisco", "syslog"),
    "logs": ("logstash", "syslog", "ingest"),
    "search": ("opensearch", "elastic", "clickhouse"),
}

_ALIAS_CACHE: dict[tuple, dict[str, tuple[str, ...]]] = {}


def aliases_config_path() -> str:
    return os.environ.get("LOOPGRAPH_ALIASES_CONFIG") or os.path.join(
        os.path.expanduser("~"), ".loopgraph", "aliases.toml")


def load_aliases(path: str | None = None) -> dict[str, tuple[str, ...]]:
    """Operator-supplied synonyms, merged over the shipped defaults.

        # ~/.loopgraph/aliases.toml
        [alias]
        ncab = ["avd", "hostpool"]
    """
    path = path or aliases_config_path()
    out = dict(DEFAULT_ALIASES)
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return out
    if key in _ALIAS_CACHE:
        return _ALIAS_CACHE[key]
    try:
        import tomllib
        with open(path, "rb") as fh:
            conf = tomllib.load(fh)
        for word, syns in (conf.get("alias") or {}).items():
            if isinstance(syns, list):
                merged = tuple(out.get(word.lower(), ())) + tuple(
                    str(s).lower() for s in syns)
                out[word.lower()] = tuple(dict.fromkeys(merged))
    except Exception:
        pass
    _ALIAS_CACHE[key] = out
    return out


def _expand(terms: list[str]) -> list[str]:
    """Query terms plus their aliases, original order, no duplicates.

    Expansion is OR-ed, so an alias can only ADD candidates -- it never
    displaces a literal match, and coverage still scores on what the caller
    actually typed.
    """
    table = load_aliases()
    out = list(terms)
    for t in terms:
        for syn in table.get(t, ()):
            if syn not in out:
                out.append(syn)
    return out


def _fts_query(query: str) -> str:
    """FTS5 syntax is a minefield of operators. Users type sentences, and a
    stray `-` or `"` turns a lookup into a syntax error, so quote every term
    and OR them together."""
    return " OR ".join(f'"{t}"' for t in _expand(_terms(query)))


def recall(
    conn: sqlite3.Connection,
    query: str,
    k: int = 8,
    kind: str | None = None,
    now: datetime | None = None,
    scope: str | None = None,
) -> list[dict]:
    """Rank memories against a query. BM25, with a recency prior. No model.

    Returns provenance with every hit -- when it was retained, where it came
    from, what superseded it. A memory you cannot date is a rumour.
    """
    match = _fts_query(query)
    if not match:
        return []
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT f.id AS id, bm25(memory_fts) AS bm, n.statement AS text, "
        "       n.created_at AS created_at "
        "FROM memory_fts f JOIN nodes n ON n.id = f.id "
        "WHERE memory_fts MATCH ? AND n.type = 'memory' "
        "ORDER BY bm LIMIT ?",
        (match, max(k * 4, 40)),
    ).fetchall()
    terms = _terms(query)
    scope = scope or scope_default()
    withheld = 0
    out = []
    for r in rows:
        meta = mem_meta(conn, r["id"])
        if kind and meta.get("kind") != kind:
            continue
        if scope != "full" and meta.get("sensitive"):
            withheld += 1
            continue
        # bm25() is negative-is-better in SQLite; flip it so bigger is better.
        score = (-float(r["bm"])) + 2.0 * _recency(r["created_at"], now)
        # How much of what was asked actually appears. BM25 alone rewards a
        # long document for containing common words, so "hello can you help
        # me" scores like a real question; coverage is what tells a caller
        # whether a hit is worth interrupting anyone with.
        body = r["text"].lower()
        # Punctuation-blind second pass. "cutover" must match "cut over", and
        # "web-app-01" must match "WEBAPP01"; without it, coverage is
        # stricter than the search that produced the hit, and drops the exact
        # identifier-shaped terms that make a query specific.
        flat = re.sub(r"[^a-z0-9]+", "", body)
        hit_terms = [t for t in terms
                     if t in body or re.sub(r"[^a-z0-9]+", "", t) in flat]
        out.append({
            "id": r["id"], "text": r["text"], "kind": meta.get("kind", "world"),
            "tags": meta.get("tags", []), "source": meta.get("source", ""),
            "created_at": r["created_at"], "score": round(score, 3),
            "coverage": round(len(hit_terms) / len(terms), 3) if terms else 0.0,
            "matched": hit_terms,
            "superseded_by": superseded_by(conn, r["id"]),
        })
    # Where a term matches matters as much as whether it does. These are long
    # multi-topic notes -- median 1400 chars, one of them 36k -- and an
    # omnibus memory collects query terms by accident: a 19k-char programme
    # note outranked the exact answer while containing none of the subject.
    # The first line states what a memory is ABOUT, so a match there is
    # evidence of topic rather than of length, and a mild length penalty
    # stops the longest document winning ties it has not earned.
    #
    # Held out (20 questions the alias table never saw): @1 7->9, @5 13->15.
    # That is more than the alias table and the link hop contributed
    # together, and it is measured on queries that were not used to build it.
    # Parameters are the middle of the plateau, not its peak -- tuning them
    # against the held-out set would recreate exactly the overfitting this
    # was written to catch.
    for m in out:
        head = (m["text"].splitlines() or [""])[0].lower()
        flat_head = re.sub(r"[^a-z0-9]+", "", head)
        in_head = sum(1 for t in terms
                      if t in head or re.sub(r"[^a-z0-9]+", "", t) in flat_head)
        title_cov = in_head / len(terms) if terms else 0.0
        length_penalty = 1.0 / (1.0 + 0.15 * len(m["text"]) / 1000.0)
        m["score"] = round(
            m["score"] * (1 + TITLE_WEIGHT * title_cov) * length_penalty, 3)
        m["title_coverage"] = round(title_cov, 3)

    # Coverage was computed, reported, and then ignored by the ordering. It
    # is the only term that knows how much of the QUESTION was answered, and
    # without it BM25 hands rank 1 to whatever is longest and most common:
    # measured, a hit covering 29% of the query outranked one covering 57%.
    # Alias expansion made that worse, because an expanded term earns BM25
    # credit that the asker never actually typed.
    for m in out:
        m["score"] = round(m["score"] * (0.4 + m.get("coverage", 0.0)), 3)
    out.sort(key=lambda m: -m["score"])
    # One hop along the graph. A question asked in the asker's vocabulary
    # ("customer" for a corpus that says "tenant") can land on a neighbour of
    # the right memory instead of the memory itself; BM25 alone then reports
    # nothing useful and the answer sits one edge away, already stored.
    # Discounted so a lexical hit always outranks an inferred one, and only
    # ever from hits good enough to trust as a starting point.
    if out:
        have = {m["id"] for m in out}
        seeds = [m for m in out[:3] if m.get("coverage", 0) >= 0.25]
        for seed in seeds:
            for nid in neighbours(conn, seed["id"]):
                if nid in have:
                    continue
                row = conn.execute(
                    "SELECT statement, created_at FROM nodes WHERE id = ? "
                    "AND type = 'memory'", (nid,)).fetchone()
                if row is None:
                    continue
                meta = mem_meta(conn, nid)
                if kind and meta.get("kind") != kind:
                    continue
                if scope != "full" and meta.get("sensitive"):
                    withheld += 1
                    continue
                have.add(nid)
                out.append({
                    "id": nid, "text": row["statement"],
                    "kind": meta.get("kind", "world"),
                    "tags": meta.get("tags", []), "source": meta.get("source", ""),
                    "created_at": row["created_at"],
                    "score": round(seed["score"] * 0.45, 3),
                    "coverage": 0.0, "matched": [],
                    "via": seed["id"],
                    "superseded_by": superseded_by(conn, nid),
                })
    out.sort(key=lambda m: -m["score"])
    out = out[:k]
    if withheld:
        # Withholding silently would let a harness believe nothing is known.
        # The count leaks no content and tells the reader to go and look
        # somewhere trusted.
        out.append({"id": "__withheld__", "text":
                    f"{withheld} matching memories withheld: they contain "
                    "client-identifying detail and this harness is running at "
                    "scope=safe. Recall them from a trusted harness, or "
                    "`mem recall --scope full` deliberately.",
                    "kind": "model", "tags": [], "source": "", "created_at": "",
                    "score": 0.0, "coverage": 0.0, "matched": [],
                    "superseded_by": None, "withheld": withheld})
    return out


def superseded_by(conn: sqlite3.Connection, mid: str) -> str | None:
    row = conn.execute(
        "SELECT src FROM edges WHERE dst = ? AND rel_type = 'supersedes' "
        "ORDER BY created_at DESC LIMIT 1", (mid,)).fetchone()
    return row["src"] if row else None


def neighbours(conn: sqlite3.Connection, mid: str) -> list[str]:
    """Ids one edge away, in either direction."""
    rows = conn.execute(
        "SELECT dst AS o FROM edges WHERE src = ? "
        "UNION SELECT src AS o FROM edges WHERE dst = ?", (mid, mid))
    return [r["o"] for r in rows]


def relate(conn: sqlite3.Connection, a: str, b: str, rel: str = "relates_to") -> None:
    link(conn, a, b, rel)


def supersede(conn: sqlite3.Connection, old_id: str, text: str, **kw) -> str:
    """Replace a memory without destroying the record of having held it.

    Deleting the old one would leave the graph asserting that we always knew
    the new thing, which is the kind of tidy history that makes a memory
    system untrustworthy.
    """
    if get_node(conn, old_id) is None:
        raise ValueError(f"no such memory: {old_id}")
    new_id = retain(conn, text, **kw)
    link(conn, new_id, old_id, "supersedes")
    emit_delta(conn, old_id, "MEMORY_SUPERSEDED", old_id, new_id)
    return new_id


def forget(conn: sqlite3.Connection, mid: str) -> bool:
    node = get_node(conn, mid)
    if node is None or node["type"] != "memory":
        return False
    emit_delta(conn, mid, "MEMORY_FORGOTTEN", node["statement"][:200], None)
    conn.execute("DELETE FROM memory_fts WHERE id = ?", (mid,))
    conn.execute("DELETE FROM nodes WHERE id = ?", (mid,))
    meta_set(conn, "memories", str(max(0, int(meta_get(conn, "memories", "0")) - 1)))
    return True


def history(conn: sqlite3.Connection, mid: str) -> list[dict]:
    """Everything that ever happened to this memory, in logical order."""
    return [dict(r) for r in conn.execute(
        "SELECT change_type, old_value, new_value, wall_time, logical_clock "
        "FROM deltas WHERE entity_id = ? ORDER BY logical_clock", (mid,))]


def stats(conn: sqlite3.Connection) -> dict:
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM nodes WHERE type='memory'").fetchone()["c"]
    by_kind: dict[str, int] = {}
    for r in conn.execute("SELECT id FROM nodes WHERE type='memory'"):
        k = mem_meta(conn, r["id"]).get("kind", "world")
        by_kind[k] = by_kind.get(k, 0) + 1
    edges = conn.execute("SELECT COUNT(*) AS c FROM edges").fetchone()["c"]
    return {"memories": n, "by_kind": by_kind, "edges": edges}


# --- reflect: what has been seen enough times to mean something -------------

def _distinctive(conn, mid: str, text: str, doc_freq: dict) -> set[str]:
    """Terms that pick this memory out, not terms that are simply English."""
    return {t for t in set(_terms(text)) if 1 < doc_freq.get(t, 0) <= 6}


def reflect(conn: sqlite3.Connection, min_cluster: int = 3) -> list[dict]:
    """Find groups of memories that nobody has drawn a conclusion from.

    Hindsight's third operation, done deterministically. The corpus is 28
    experiences to 9 conclusions -- things that happened, recorded, never
    generalised. This will not write the lesson (no model runs here, and a
    generated one would be a plausible sentence with nothing behind it); it
    finds the piles that are big enough to have a lesson in them and have no
    `model` memory sitting on top.

    Clusters come from what the memories actually share: an explicit
    `relates_to` edge, or distinctive vocabulary. A term in half the corpus
    is not a theme, so document frequency bounds both ends.
    """
    rows = [(r["id"], r["statement"]) for r in
            conn.execute("SELECT id, statement FROM nodes WHERE type='memory'")]
    if not rows:
        return []
    doc_freq: dict[str, int] = {}
    for _, text in rows:
        for t in set(_terms(text)):
            doc_freq[t] = doc_freq.get(t, 0) + 1

    kinds = {mid: mem_meta(conn, mid).get("kind", "world") for mid, _ in rows}
    vocab = {mid: _distinctive(conn, mid, text, doc_freq) for mid, text in rows}
    ids = [mid for mid, _ in rows]

    # Neighbours only -- deliberately NOT transitive closure. Union-find over
    # `relates_to` put 72 of 86 memories in one component, and because that
    # blob contained all nine conclusions, every theme looked concluded and
    # reflect reported a clean corpus. A false all-clear is worse than no
    # feature: it answers the question wrongly instead of leaving it open.
    linked: dict[str, set[str]] = {mid: set() for mid in ids}
    for r in conn.execute("SELECT src, dst FROM edges WHERE rel_type='relates_to'"):
        if r["src"] in linked and r["dst"] in linked:
            linked[r["src"]].add(r["dst"])
            linked[r["dst"]].add(r["src"])

    # A theme is a pair of distinctive terms that several memories share.
    # Neighbour-of-a-neighbour grouping still produced a 27-memory "theme"
    # with no vocabulary in common, because `relates_to` in this corpus means
    # "vaguely adjacent". A term pair is bounded, and it names itself: the
    # output can say what the pile is about instead of asserting it is one.
    themes: dict[tuple[str, str], set[str]] = {}
    for mid in ids:
        terms = sorted(vocab[mid])[:10]        # bound the pair explosion
        for i, a in enumerate(terms):
            for b in terms[i + 1:]:
                themes.setdefault((a, b), set()).add(mid)

    seen: set[frozenset] = set()
    out = []
    for (a, b), group in sorted(themes.items(), key=lambda kv: -len(kv[1])):
        if len(group) < min_cluster:
            continue
        key = frozenset(group)
        if key in seen:
            continue
        seen.add(key)
        common = sorted(set.intersection(*(vocab[m] for m in group))) or [a, b]
        # "Concluded" cannot mean "a model memory is inside this pile": a
        # conclusion is usually written *about* a set of experiences and
        # shares their vocabulary without being one of them. Ask whether any
        # model memory covers the theme, not whether one is filed under it.
        if any(kinds[m] == "model" for m in group) or any(
                len(vocab[mm] & set(common)) >= 2
                for mm in ids if kinds[mm] == "model" and mm not in group):
            continue
        # An explicit link between members is corroboration, not the grouping
        # rule -- worth reporting so a reader can see the pile is really one.
        links = sum(1 for m in group for n in group if n in linked[m]) // 2
        # The pair is how the group was found; the intersection is what the
        # group is actually about, and that is what a reader needs to decide
        # whether there is a lesson in it.
        out.append({
            "members": sorted(group),
            "shared": common[:8],
            "links": links,
            "kinds": sorted({kinds[m] for m in group}),
        })
    out.sort(key=lambda g: (-len(g["members"]), g["members"][0]))
    return out


# --- the markdown corpus is the writer; this database is the index ----------

def write_markdown(
    directory: str, mid: str, text: str, kind: str,
    tags=(), source: str = "", title: str = "", created: str = "",
) -> str:
    """Write the memory as a file and put it in MEMORY.md.

    This is the half that stops the two stores drifting: MEMORY.md is what a
    session actually loads, so a memory that never reaches it is a memory the
    next session does not have.
    """
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{mid}.md")
    body = text.strip()
    hook = body.splitlines()[0][:180]
    front = ["---", f"name: {mid}", f"description: {hook}", "metadata:",
             f"  type: {KIND_TO_TYPE.get(kind, 'reference')}",
             f"  created: {created or utcnow()}"]
    if tags:
        front.append(f"  tags: {', '.join(tags)}")
    if source:
        front.append(f"  source: {source}")
    if sensitivity(body):
        front.append("  sensitive: true")
    front.append("---")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(front) + "\n\n" + body + "\n")
    _index_markdown(directory, mid, hook)
    return path


# MEMORY.md is one shared file, and five sessions run against it at once here.
# Both index writers were read-modify-write with nothing in between, so two
# concurrent retains raced and the loser's line vanished: the memory file sat
# on disk unlisted, invisible to every session that loads the index rather
# than the directory. Caught in the act -- a line written at 16:45:58 was
# gone by 16:46, while a second process was repairing two older lines lost
# the same way.
#
# So the whole read-modify-write is serialised on a sidecar lock, and the
# write is a rename over the top rather than a truncate: a crash mid-write
# leaves the old index, never half of one.
def _index_lock_path(directory: str) -> str:
    return os.path.join(directory, ".MEMORY.md.lock")


@contextlib.contextmanager
def _index_lock(directory: str):
    os.makedirs(directory, exist_ok=True)
    fh = open(_index_lock_path(directory), "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _write_atomic(path: str, text: str) -> None:
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".MEMORY.md.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _index_markdown(directory: str, mid: str, hook: str) -> None:
    index = os.path.join(directory, "MEMORY.md")
    line = f"- [{mid}.md]({mid}.md) — {hook}"
    with _index_lock(directory):
        try:
            lines = open(index, encoding="utf-8").read().splitlines()
        except OSError:
            lines = ["# Memory Index", ""]
        for i, existing in enumerate(lines):
            if existing.startswith(f"- [{mid}.md]"):
                lines[i] = line                    # refresh in place
                break
        else:
            lines.append(line)
        _write_atomic(index, "\n".join(lines).rstrip() + "\n")


def remove_markdown(directory: str, mid: str) -> bool:
    path = os.path.join(directory, f"{mid}.md")
    index = os.path.join(directory, "MEMORY.md")
    removed = False
    if os.path.exists(path):
        os.remove(path)
        removed = True
    with _index_lock(directory):
        try:
            lines = open(index, encoding="utf-8").read().splitlines()
            kept = [l for l in lines if not l.startswith(f"- [{mid}.md]")]
            if len(kept) != len(lines):
                _write_atomic(index, "\n".join(kept).rstrip() + "\n")
        except OSError:
            pass
    return removed


def reindex(conn: sqlite3.Connection, directory: str) -> dict:
    """Throw the index away and rebuild it from the files.

    The index being disposable is what makes the files authoritative: if a
    rebuild ever disagrees with what recall was returning, the index had
    drifted and the files were right.
    """
    for r in list(conn.execute("SELECT id FROM nodes WHERE type='memory'")):
        conn.execute("DELETE FROM memory_fts WHERE id = ?", (r["id"],))
        conn.execute("DELETE FROM nodes WHERE id = ?", (r["id"],))
    meta_set(conn, "memories", "0")
    got = import_markdown(conn, directory)

    # Second autolink pass, now that every memory exists. During the import
    # each one can only see the memories inserted before it, so the earliest
    # arrivals link to almost nothing -- isolation came out at 24% rebuilt
    # against 10% for the same corpus linked in one pass. Links are derived
    # data: rebuilding them is exactly what makes the index disposable.
    for r in list(conn.execute(
            "SELECT id, statement FROM nodes WHERE type='memory'")):
        autolink(conn, r["id"], r["statement"])

    # Repair MEMORY.md too. A file that exists but is not indexed is invisible
    # to the session that loads the index -- the same divergence in miniature,
    # and two files were already in that state before this was written.
    repaired = []
    for r in conn.execute("SELECT id, statement FROM nodes WHERE type='memory'"):
        hook = r["statement"].strip().splitlines()[0][:180]
        index = os.path.join(directory, "MEMORY.md")
        try:
            listed = f"- [{r['id']}.md]" in open(index, encoding="utf-8").read()
        except OSError:
            listed = False
        if not listed:
            _index_markdown(directory, r["id"], hook)
            repaired.append(r["id"])
    got["index_repaired"] = sorted(repaired)
    return got


# --- seeding from the markdown memories that already exist -------------------

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def _norm(name: str) -> str:
    """Link targets are written by hand, so `-`, `_` and `.md` all appear for
    the same memory."""
    return re.sub(r"[^a-z0-9]+", "", name.lower().removesuffix(".md"))

# The markdown corpus uses its own vocabulary. `feedback` and `project` are
# things that happened to us and what we concluded from them; `reference` is
# how the world is.
KIND_BY_TYPE = {"user": "model", "feedback": "model", "project": "experience",
                "reference": "world"}


def import_markdown(conn: sqlite3.Connection, directory: str) -> dict:
    """Seed from an existing markdown memory directory.

    Wiki links become `relates_to` edges, so the corpus arrives as a graph
    rather than 76 unrelated strings. Links to memories that do not exist are
    kept as pending, not dropped -- the index file names some that were never
    written, and losing that is losing a to-do.
    """
    imported, skipped, links_seen = [], [], []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".md") or name == "MEMORY.md":
            continue
        path = os.path.join(directory, name)
        try:
            raw = open(path, encoding="utf-8").read()
        except Exception:
            skipped.append(name)
            continue
        meta, body = {}, raw
        m = FRONTMATTER.match(raw)
        if m:
            body = raw[m.end():]
            for line in m.group(1).splitlines():
                if ":" in line and not line.startswith((" ", "\t", "-")):
                    key, _, val = line.partition(":")
                    meta[key.strip()] = val.strip().strip('"')
        # Identity is the filename, never the frontmatter `name:`. Older
        # memories in this corpus put a human title there ("Thorough fixes
        # required") while every wiki link and index entry points at the
        # filename slug, so trusting `name:` orphans exactly the memories
        # that have been around long enough to be linked to most.
        mid = os.path.splitext(name)[0]
        title = meta.get("name", "")
        # Collect links from every file, not just newly-imported ones: on a
        # re-run everything is skipped, and link repair would never happen.
        links_seen += [(mid, t.strip()) for t in WIKILINK.findall(body)]
        if get_node(conn, mid) is not None:
            skipped.append(mid)
            continue
        typ = meta.get("type", "")
        if not typ:
            # `type:` lives under `metadata:` in this corpus, so the flat
            # parse above misses it; fall back to the filename convention.
            typ = name.split("_", 1)[0] if "_" in name else ""
        text = (meta.get("description", "").strip() + "\n\n" + body.strip()).strip()
        # Date order: what the file records, else the file's own mtime.
        # Falling through to "now" is what flattened the corpus.
        created = (meta.get("created") or meta.get("modified") or "").strip()
        if not created:
            try:
                created = datetime.fromtimestamp(
                    os.path.getmtime(path), timezone.utc).isoformat()
            except OSError:
                created = ""
        retain(conn, text, kind=KIND_BY_TYPE.get(typ, "world"),
               tags=(typ,) if typ else (), source=path, id=mid,
               created_at=created or None)
        if title:
            m = mem_meta(conn, mid)
            m["title"] = title
            _set_mem_meta(conn, mid, m)
        imported.append(mid)

    # The corpus writes wiki links both ways -- [[feedback-dead-but-looks-
    # alive]] and [[feedback_dead_but_looks_alive]] name the same memory.
    # Matching literally drops a fifth of the graph and calls the targets
    # unwritten, which reads as a to-do list rather than a resolver bug.
    index = {_norm(r["id"]): r["id"] for r in
             conn.execute("SELECT id FROM nodes WHERE type='memory'")}
    linked, pending = 0, []
    for src, dst in links_seen:
        target = index.get(_norm(dst))
        if target and target != src:
            link(conn, src, target, "relates_to")
            linked += 1
        elif not target:
            pending.append(f"{src} -> {dst}")
    return {"imported": len(imported), "skipped": len(skipped),
            "linked": linked, "pending_links": sorted(set(pending))}
