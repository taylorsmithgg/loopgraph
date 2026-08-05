"""Mine transcripts for memories worth keeping.

3,317 Claude Code transcripts, 2.1 GB, and every trap in them was learned the
expensive way. The value is not the transcripts, it is the handful of facts
they each contain -- so read them once, keep the facts, and stop paying for
the rest.

No model runs here, by necessity and by preference: a nested `claude -p` is
unusable in this environment, and 2 GB of transcript would be absurd to feed
through one anyway. Two signals survive without one:

**Recurrence.** An error string that shows up across many separate sessions is,
by definition, something rediscovered rather than learned. That is what a trap
IS. Counting distinct sessions needs no intelligence at all -- and unlike a
model's judgement, it cannot be talked into a plausible-sounding memory that
nothing actually supports.

**Correction.** A human turn that contradicts, forbids or redirects is the
other high-signal shape, because it is the moment the humans's model of the
world and the agent's diverged and the human won.

Everything here emits *candidates* with provenance, never memories. The
distilling is done by whoever reads them: a candidate that cannot be turned
into a sentence worth remembering was noise, and noise that auto-retains is
how a memory store becomes something people stop reading.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict

# Injected context is not the human talking. Without this the miner "learns"
# its own hook output back, which is the purest possible form of a system
# citing itself as evidence.
INJECTED = re.compile(
    r"<system-reminder>|<command-name>|<local-command|Possibly relevant, from memory|"
    r"loopgraph: |Caveat: The messages below|<user-prompt-submit-hook>|"
    r"# Context from my IDE setup|## Open tabs|<environment_context>|"
    r"<permissions instructions>", re.I)

MAX_CORRECTION_CHARS = 600

# Only unambiguous rebukes. `instead of`, `rather than` and `revert` were in
# here and had to come out: they appear in ordinary instructions far more
# often than in corrections ("revert the earlier approach if it helps"), and a
# correction list that is mostly instructions is one nobody reads twice.
CORRECTION = re.compile(
    r"\b(?:no,|nope|wrong|incorrect|that'?s not|not what i|actually,|"
    r"never (?:do|use|run|touch|write|commit)|don'?t (?:do|use|run|touch|ever)|"
    r"stop (?:doing|using)|i (?:said|told you)|you (?:broke|missed|forgot)|"
    r"undo that)\b", re.I)

# Lines that look like something failing, rather than something logging.
ERROR_LINE = re.compile(
    r"\b(?:error|exception|traceback|fatal|failed|failure|refused|denied|"
    r"not found|cannot|can'?t|unable to|timed? out|unauthorized|forbidden|"
    r"no such|invalid|unrecognized)\b", re.I)

# Tool results are mostly file contents, and source code is *full* of the word
# "error". Unfiltered, the top of every harvest is `except Exception as e:`
# read out of 350 different files -- a miner that has learned to recognise
# Python rather than failure.
FILE_CONTENT = re.compile(r"\A\s*\d+[\t→|]")           # Read's numbered output
DIFF_LINE = re.compile(r"\A\s*(?:[-+]{1,3}\s|@@ )")
CODE_SHAPE = re.compile(
    r"(?:^\s*(?:def|class|import|from|const|let|var|function|export|public|"
    r"private|async|return|if|for|while|try|except|catch|elif|else)\b)|"
    r"(?:=>|::|\{\s*$|\}\s*;?\s*$|\)\s*;\s*$|^\s*[#/*]{1,3}\s)|"
    # Shell scripts being read, not shell failing: `|| die "..."`, guard
    # clauses and redirections are source lines that happen to say "failed".
    r"(?:\|\|\s*die\b|>\s*/dev/null|^\s*command -v\b|^\s*set -[eux])")

# A test summary is a score, not a lesson. "21 failed | 534 passed" recurring
# across sessions says a suite was red a lot, which nobody can act on a year
# later -- unlike "this command does not exist on this platform".
TEST_SUMMARY = re.compile(
    r"(?:\d+\s+(?:failed|passed)\b.*\b(?:passed|failed)\b)|"
    r"^\s*Tests?\s+Suites?:|^\s*(?:Tests|Suites)\s+\d|"
    r"Failed Tests \d|error during collection", re.I)

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\[\d+m")

# Volatile detail, so the same failure in two sessions collapses to one string.
NOISE = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{7,40}\b"), "<hash>"),
    (re.compile(r"/(?:users|home)/[^\s:'\"]+", re.I), "<path>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*"), "<ts>"),
    (re.compile(r"\bline \d+"), "line <n>"),
    (re.compile(r"\b\d+\b"), "<n>"),
    (re.compile(r"\s+"), " "),
]


def normalise(line: str) -> str:
    """Collapse one failure to one key.

    Case-folded: `fatal:` and `Fatal:` are the same trap, and counting them
    apart splits 78 rediscoveries into 47 and 31 -- both halves then looking
    less urgent than the whole. The stored example keeps the original casing
    for a human to read.
    """
    out = line.strip().lower()
    for pat, repl in NOISE:
        out = pat.sub(repl, out)
    return out.strip()[:300]


def _parts(msg) -> list:
    content = (msg or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content if isinstance(content, list) else []


def read_transcript(path: str):
    """Yield (role, kind, text) for one transcript, skipping meta lines.

    Streams: these files reach 13 MB and there are thousands of them.
    """
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue                      # a truncated tail is not fatal
            role = d.get("type")
            if role not in ("user", "assistant"):
                continue
            for p in _parts(d.get("message")):
                kind = p.get("type")
                if kind == "text":
                    yield role, "text", p.get("text") or ""
                elif kind == "tool_result":
                    c = p.get("content")
                    if isinstance(c, str):
                        yield role, "tool_result", c
                    elif isinstance(c, list):
                        for sub in c:
                            if isinstance(sub, dict) and sub.get("type") == "text":
                                yield role, "tool_result", sub.get("text") or ""


def read_codex(path: str):
    """Codex rollouts, yielding the same (role, kind, text) as Claude Code's.

    Different schema, identical signals: `response_item` wraps either a
    message with a role or a `function_call_output` carrying command output.
    The `developer` role is injected policy text, not a person, and mining it
    would teach the same lesson as mining our own hook output.
    """
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("type") != "response_item":
                continue
            p = d.get("payload") or {}
            ptype = p.get("type")
            if ptype == "function_call_output":
                out = p.get("output")
                if isinstance(out, str):
                    yield "user", "tool_result", out
                elif isinstance(out, dict):
                    yield "user", "tool_result", str(out.get("content") or "")
            elif ptype == "message":
                role = p.get("role")
                if role not in ("user", "assistant"):
                    continue                  # `developer` is injected policy
                for part in p.get("content") or []:
                    if isinstance(part, dict) and part.get("type") in (
                            "input_text", "output_text", "text"):
                        yield role, "text", part.get("text") or ""


def read_any(path: str):
    """Pick a reader by looking at the file, not at its name.

    Codex writes `rollout-*.jsonl` today; a naming convention is a weaker
    promise than the first line of the file.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.readline(4096)
    except OSError:
        return iter(())
    if '"session_meta"' in head or '"response_item"' in head:
        return read_codex(path)
    return read_transcript(path)


def transcripts(root: str, since_days: float | None = None) -> list[str]:
    import time
    cutoff = time.time() - since_days * 86400 if since_days else None
    out = []
    for dirpath, _, names in os.walk(root):
        for n in names:
            if not n.endswith(".jsonl"):
                continue
            p = os.path.join(dirpath, n)
            try:
                if cutoff and os.path.getmtime(p) < cutoff:
                    continue
            except OSError:
                continue
            out.append(p)
    return sorted(out)


def mine(
    paths: list[str], min_sessions: int = 3, max_candidates: int = 40,
) -> dict:
    """Read transcripts once; return ranked candidates with provenance."""
    errors: dict[str, set] = defaultdict(set)
    error_example: dict[str, str] = {}
    corrections: list[dict] = []

    for path in paths:
        session = os.path.basename(path).removesuffix(".jsonl")
        seen_here: set[str] = set()
        for role, kind, text in read_any(path):
            if not text:
                continue
            if role == "user" and kind == "text":
                if INJECTED.search(text) or len(text) > MAX_CORRECTION_CHARS:
                    continue              # injected context, or a pasted dump
                # A correction is short and leads with the correction. A long
                # task brief that happens to contain "instead of" three
                # paragraphs down is a specification, not a rebuke.
                if CORRECTION.search(text[:200]):
                    corrections.append({"session": session, "path": path,
                                        "text": text.strip()[:400]})
                continue
            if kind != "tool_result":
                continue
            for raw in text.splitlines():
                raw = ANSI.sub("", raw)
                if len(raw) < 20 or not ERROR_LINE.search(raw):
                    continue
                if (FILE_CONTENT.match(raw) or DIFF_LINE.match(raw)
                        or CODE_SHAPE.search(raw) or TEST_SUMMARY.search(raw)):
                    continue                  # source, or a score, not a lesson
                key = normalise(raw)
                if len(key) < 20 or key in seen_here:
                    continue
                seen_here.add(key)
                errors[key].add(session)
                error_example.setdefault(key, raw.strip()[:300])

    recurring = [
        {"pattern": k, "sessions": len(v), "example": error_example[k]}
        for k, v in errors.items() if len(v) >= min_sessions
    ]
    recurring.sort(key=lambda r: -r["sessions"])
    return {
        "scanned": len(paths),
        "recurring_errors": recurring[:max_candidates],
        "corrections": corrections[-max_candidates:],
    }


def undistilled(conn, candidates: list[str], min_coverage: float = 0.6) -> list[str]:
    """Drop candidates the memory store already answers.

    Re-mining a corpus should get quieter every time it is run. A harvest that
    proposes the same forty things forever is a chore, and a chore gets
    ignored -- which is how the memories stop being read at all.
    """
    from .memory import recall
    out = []
    for text in candidates:
        hits = recall(conn, text, k=1)
        if hits and hits[0]["coverage"] >= min_coverage:
            continue
        out.append(text)
    return out
