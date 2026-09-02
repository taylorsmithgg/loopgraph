"""Memory on the context graph.

The failure mode being tested against throughout: a memory store that accepts
everything, returns something plausible, and cannot tell you when it learned
it or what replaced it.
"""

import os

import pytest

from loopgraph.memory import (
    KINDS, forget, history, import_markdown, mem_meta, open_memory, recall,
    reflect, relate, retain, stats, supersede, superseded_by,
)

EDGE = ("EDGE-LOG-01 was silently cut over to EDGE-LOG-02 on the same IP "
        "192.0.2.10; EDGE-LOG-02 nginx never started, so the edge went blind")
GLAB = "glab mr merge reports success on a merge it did not perform"


@pytest.fixture
def mem(tmp_path, monkeypatch):
    # These exercise ranking, not redaction; redaction has its own tests.
    monkeypatch.setenv("LOOPGRAPH_MEM_SCOPE", "full")
    return open_memory(str(tmp_path / "memory.db"))


def test_retained_memory_is_recallable(mem):
    retain(mem, EDGE, kind="experience")
    got = recall(mem, "edge logger cutover blind")
    assert got and "EDGE-LOG-02" in got[0]["text"]


def test_recall_ranks_the_relevant_one_first(mem):
    retain(mem, GLAB)
    retain(mem, EDGE, kind="experience")
    retain(mem, "AWS default region is us-east-2, not us-east-1")
    assert recall(mem, "logger cutover edge blind")[0]["text"].startswith("EDGE-LOG")
    assert "glab" in recall(mem, "glab merge lies about success")[0]["text"]


def test_stemming_reaches_inflected_words(mem):
    retain(mem, "the loggers were cut over without a rollback path")
    assert recall(mem, "logger cutover")


def test_a_sentence_with_punctuation_is_not_a_syntax_error(mem):
    """Users type sentences. FTS5 operators inside them must not blow up."""
    retain(mem, EDGE)
    assert recall(mem, 'why did the edge go blind -- was it the "cutover"?')


def test_recall_of_nothing_is_empty_not_an_error(mem):
    assert recall(mem, "") == []
    assert recall(mem, "zzzz-nothing-matches-this") == []


def test_every_hit_carries_its_provenance(mem):
    mid = retain(mem, EDGE, kind="experience", tags=["nginx"], source="incident")
    hit = recall(mem, "edge blind")[0]
    assert hit["id"] == mid and hit["kind"] == "experience"
    assert hit["tags"] == ["nginx"] and hit["source"] == "incident"
    assert hit["created_at"]


def test_kind_filter_separates_conclusions_from_observations(mem):
    retain(mem, "the nginx pod restarted at 14:02", kind="experience")
    retain(mem, "restart storms are always the exporter sidecar", kind="model")
    assert len(recall(mem, "restart", kind="model")) == 1


def test_recency_breaks_a_tie(mem):
    from datetime import datetime, timedelta, timezone
    old = retain(mem, "the cutover was on host one")
    new = retain(mem, "the cutover was on host two")
    mem.execute("UPDATE nodes SET created_at = ? WHERE id = ?",
                ((datetime.now(timezone.utc) - timedelta(days=900)).isoformat(), old))
    assert recall(mem, "the cutover was on host")[0]["id"] == new


def test_an_unknown_kind_is_refused(mem):
    with pytest.raises(ValueError):
        retain(mem, "x", kind="vibes")
    assert set(KINDS) == {"world", "experience", "model"}


def test_an_empty_memory_is_refused(mem):
    with pytest.raises(ValueError):
        retain(mem, "   ")


def test_ids_do_not_collide(mem):
    a = retain(mem, "the same opening words here, first version")
    b = retain(mem, "the same opening words here, second version")
    assert a != b and len(recall(mem, "same opening words")) == 2


def test_superseding_keeps_the_old_belief_and_links_it(mem):
    old = retain(mem, "the gateway is not in the live path")
    new = supersede(mem, old, "the gateway IS in the live path as of August")
    assert superseded_by(mem, old) == new
    hits = {h["id"]: h for h in recall(mem, "gateway live path")}
    assert hits[old]["superseded_by"] == new      # still findable, marked stale


def test_history_is_the_record_of_what_was_believed_when(mem):
    old = retain(mem, "the gateway is not in the live path")
    supersede(mem, old, "the gateway IS in the live path")
    kinds = [h["change_type"] for h in history(mem, old)]
    assert kinds == ["MEMORY_RETAINED", "MEMORY_SUPERSEDED"]
    assert all(h["logical_clock"] for h in history(mem, old))


def test_forgetting_removes_it_from_recall(mem):
    mid = retain(mem, EDGE)
    assert forget(mem, mid) is True
    assert recall(mem, "edge blind") == []
    assert [h["change_type"] for h in history(mem, mid)][-1] == "MEMORY_FORGOTTEN"


def test_forgetting_something_absent_is_false_not_an_exception(mem):
    assert forget(mem, "never-existed") is False


def test_memories_can_be_related(mem):
    a = retain(mem, "the worker wedges with a live process and a frozen input")
    b = retain(mem, "a rolling restart clears a wedged worker")
    relate(mem, b, a, "relates_to")
    assert mem.execute("SELECT COUNT(*) c FROM edges").fetchone()["c"] == 1


def test_stats_counts_by_kind(mem):
    retain(mem, "a world fact")
    retain(mem, "something that happened", kind="experience")
    s = stats(mem)
    assert s["memories"] == 2 and s["by_kind"]["experience"] == 1


# --- seeding ----------------------------------------------------------------

def _corpus(root):
    root.mkdir(exist_ok=True)
    (root / "reference_glab.md").write_text(
        "---\nname: reference_glab\ndescription: glab mr merge lies\n"
        "metadata:\n  type: reference\n---\n\n"
        "`glab mr merge` reports success. See [[project_edge]] and [[missing_one]].\n")
    (root / "project_edge.md").write_text(
        "---\nname: project_edge\ndescription: the edge went blind after a cutover\n"
        "metadata:\n  type: project\n---\n\nEDGE-LOG-02 nginx never started.\n")
    (root / "MEMORY.md").write_text("- index line, not a memory\n")
    return root


def test_import_seeds_the_corpus(mem, tmp_path):
    got = import_markdown(mem, str(_corpus(tmp_path / "corpus")))
    assert got["imported"] == 2                    # MEMORY.md is not a memory
    assert recall(mem, "glab merge lies")


def test_import_maps_the_corpus_vocabulary_onto_kinds(mem, tmp_path):
    import_markdown(mem, str(_corpus(tmp_path / "corpus")))
    assert mem_meta(mem, "reference_glab")["kind"] == "world"
    assert mem_meta(mem, "project_edge")["kind"] == "experience"


def test_import_turns_wiki_links_into_edges(mem, tmp_path):
    got = import_markdown(mem, str(_corpus(tmp_path / "corpus")))
    assert got["linked"] == 1
    assert got["pending_links"] == ["reference_glab -> missing_one"]


def test_import_is_idempotent(mem, tmp_path):
    root = _corpus(tmp_path / "corpus")
    import_markdown(mem, str(root))
    second = import_markdown(mem, str(root))
    assert second["imported"] == 0 and second["skipped"] == 2
    assert stats(mem)["memories"] == 2


def test_import_keeps_the_source_path_so_a_memory_can_be_traced_home(mem, tmp_path):
    root = _corpus(tmp_path / "corpus")
    import_markdown(mem, str(root))
    assert mem_meta(mem, "project_edge")["source"] == os.path.join(
        str(root), "project_edge.md")


def test_links_resolve_across_naming_conventions(mem, tmp_path):
    """The corpus writes [[a-b]] and [[a_b]] for the same memory. Matching
    literally drops a fifth of the graph and reports the targets as unwritten,
    which reads as a to-do list instead of a resolver bug."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "feedback_dead_but_looks_alive.md").write_text(
        "---\nname: feedback_dead_but_looks_alive\ndescription: guards that lie\n---\n\nx\n")
    (root / "project_x.md").write_text(
        "---\nname: project_x\ndescription: a project\n---\n\n"
        "See [[feedback-dead-but-looks-alive]] and [[feedback_dead_but_looks_alive.md]].\n")
    got = import_markdown(mem, str(root))
    assert got["linked"] == 2 and got["pending_links"] == []


def test_reimport_repairs_links_without_reimporting(mem, tmp_path):
    """On a re-run every file is skipped, so link repair must not be gated
    behind having imported something."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "b.md").write_text("---\nname: b\ndescription: b\n---\n\nsee [[a]]\n")
    first = import_markdown(mem, str(root))
    assert first["pending_links"] == ["b -> a"]
    (root / "a.md").write_text("---\nname: a\ndescription: a\n---\n\nthe target\n")
    second = import_markdown(mem, str(root))
    assert second["imported"] == 1 and second["linked"] == 1
    assert second["pending_links"] == []


def test_a_self_link_is_not_an_edge(mem, tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "a.md").write_text("---\nname: a\ndescription: a\n---\n\nsee [[a]]\n")
    got = import_markdown(mem, str(root))
    assert got["linked"] == 0 and got["pending_links"] == []


def test_identity_is_the_filename_not_the_frontmatter_title(mem, tmp_path):
    """Older memories carry a human title in `name:` while every wiki link
    points at the filename. Trusting `name:` orphans the oldest, most-linked
    memories."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "feedback_thorough_fixes.md").write_text(
        "---\nname: Thorough fixes required\ndescription: no half measures\n"
        "type: feedback\n---\n\nAlways finish the job.\n")
    (root / "project_x.md").write_text(
        "---\nname: project_x\ndescription: a project\n---\n\n"
        "See [[feedback_thorough_fixes]].\n")
    got = import_markdown(mem, str(root))
    assert got["linked"] == 1 and got["pending_links"] == []
    assert mem_meta(mem, "feedback_thorough_fixes")["title"] == "Thorough fixes required"


def test_filler_words_do_not_manufacture_coverage(mem):
    """Observed live: "is there a way we can harvest our memories while
    reducing disk" scored 0.667 coverage on way/while/memories/disk and
    surfaced an unrelated memory. Coverage built out of filler is worse than
    no coverage -- it is confident noise."""
    retain(mem, "Pi is a local-first coding agent against the llm.local "
                "gateway; disk layout and model aliases live in ~/.pi")
    hits = recall(mem, "is there a way we can harvest our memories while reducing disk")
    assert all(h["coverage"] < 0.6 for h in hits)


# --- scope: the mistake this fixes -------------------------------------------
# One store is reachable from every harness on the machine, and those
# harnesses do not all run the same vendor's model. Wiring recall into all of
# them quietly undid the reason extraction was placed where it was.
# Default-deny, and say so rather than looking empty.

PRIVATE = ("EDGE-LOG-01 was cut over to EDGE-LOG-02 on the same IP 192.0.2.10 "
           "and nginx never started")
GENERIC = "porter stemming in FTS5 reaches inflected words like logger/loggers"


def test_private_detail_is_classified_sensitive():
    from loopgraph.memory import sensitivity
    assert sensitivity(PRIVATE) == ["an IP address"]
    assert sensitivity(GENERIC) == []


@pytest.mark.parametrize("text,expected", [
    ("the key is at /home/deploy/openvpn/easy-rsa/pki", True),
    ("arn:aws:iam::123456789012:role/Thing", True),
    ("email someone@example.com about it", True),
    ("account 123456789012 owns the bucket", True),
    ("otel-collector.telemetry.svc.cluster.local:4317", True),
    ("the exhibit package for a large-medical client RFP pursuit", True),
    ("use uv run pytest -q to run the suite", False),
    ("BM25 is negative-is-better in SQLite", False),
])
def test_sensitivity_classifier(text, expected):
    from loopgraph.memory import sensitivity
    assert bool(sensitivity(text)) is expected


# --- operator terms ----------------------------------------------------------
# What makes an operational note identifying is the name of an employer, a
# client, a cluster. Those differ per operator and none of them belong in this
# repository, so they come from a file on the operator's machine.

def _write_config(tmp_path, monkeypatch, body: str) -> str:
    p = tmp_path / "sensitive.toml"
    p.write_text(body)
    monkeypatch.setenv("LOOPGRAPH_SENSITIVE_CONFIG", str(p))
    return str(p)


def test_operator_terms_are_matched(tmp_path, monkeypatch):
    from loopgraph.memory import sensitivity
    _write_config(tmp_path, monkeypatch,
                  'terms = ["orion", "acme-console"]\n'
                  'terms_why = "an internal system or client name"\n')
    assert sensitivity("Orion rescheduled the worker pods") == [
        "an internal system or client name"]
    assert sensitivity("the acme-console ingress is not managed") == [
        "an internal system or client name"]


def test_operator_terms_do_not_fire_inside_longer_words(tmp_path, monkeypatch):
    """A term list is only usable if it is quiet. `orion` must not match
    `orionesque`, and must not turn every document into a withheld one."""
    from loopgraph.memory import sensitivity
    _write_config(tmp_path, monkeypatch, 'terms = ["orion", "sms"]\n')
    assert sensitivity("orionesque naming is a bad habit") == []
    assert sensitivity("the transmission was fine") == []


def test_operator_regex_patterns(tmp_path, monkeypatch):
    from loopgraph.memory import sensitivity
    _write_config(tmp_path, monkeypatch,
                  "[[pattern]]\n"
                  "regex = '\\b(?:ACME|GLBX)[-_][A-Z0-9-]{2,}\\b'\n"
                  'why = "a client host or tenant code"\n')
    assert sensitivity("ACME-LOG-01 went down") == ["a client host or tenant code"]
    assert sensitivity("the log host went down") == []


def test_no_config_means_no_operator_terms(tmp_path, monkeypatch):
    """The default install classifies on generic patterns only, and says
    nothing about anybody's clients."""
    from loopgraph.memory import load_sensitive_patterns, sensitivity
    monkeypatch.setenv("LOOPGRAPH_SENSITIVE_CONFIG", str(tmp_path / "nope.toml"))
    assert load_sensitive_patterns() == []
    assert sensitivity("Orion rescheduled the worker pods") == []


def test_a_broken_config_is_announced_not_silently_ignored(tmp_path, monkeypatch, capsys):
    """A redactor that quietly stops redacting looks exactly like one with
    nothing to redact. It has to say so."""
    from loopgraph.memory import load_sensitive_patterns
    _write_config(tmp_path, monkeypatch, 'terms = ["unterminated\n')
    assert load_sensitive_patterns() == []
    assert "loopgraph:" in capsys.readouterr().err


def test_an_invalid_regex_is_skipped_and_the_rest_survive(tmp_path, monkeypatch, capsys):
    from loopgraph.memory import load_sensitive_patterns, sensitivity
    _write_config(tmp_path, monkeypatch,
                  "[[pattern]]\nregex = '([unclosed'\nwhy = \"broken\"\n\n"
                  "[[pattern]]\nregex = '\\bORION\\b'\nwhy = \"good one\"\n")
    assert [why for _, why in load_sensitive_patterns()] == ["good one"]
    assert "not a valid regex" in capsys.readouterr().err
    assert sensitivity("ORION is down") == ["good one"]


def test_editing_the_config_takes_effect_without_a_restart(tmp_path, monkeypatch):
    """Harnesses are long-lived; a cached pattern list would mean a term
    added after a leak scare does not apply until the next reboot."""
    from loopgraph.memory import sensitivity
    p = _write_config(tmp_path, monkeypatch, 'terms = ["orion"]\n')
    assert sensitivity("vega is fine") == []
    with open(p, "w") as fh:
        fh.write('terms = ["orion", "vega"]\n')
    assert sensitivity("vega is fine") != []


def test_safe_scope_withholds_private_detail(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPGRAPH_MEM_SCOPE", "safe")
    conn = open_memory(str(tmp_path / "m.db"))
    retain(conn, PRIVATE, kind="experience")
    hits = recall(conn, "EDGE-LOG-02 nginx cutover")
    assert [h["id"] for h in hits] == ["__withheld__"]


def test_withholding_is_announced_not_silent(tmp_path, monkeypatch):
    """Silently returning nothing would tell the reader nothing is known."""
    monkeypatch.setenv("LOOPGRAPH_MEM_SCOPE", "safe")
    conn = open_memory(str(tmp_path / "m.db"))
    retain(conn, PRIVATE, kind="experience")
    notice = recall(conn, "EDGE-LOG-02 nginx cutover")[-1]
    assert notice["withheld"] == 1 and "scope=safe" in notice["text"]


def test_full_scope_returns_it(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPGRAPH_MEM_SCOPE", "full")
    conn = open_memory(str(tmp_path / "m.db"))
    mid = retain(conn, PRIVATE, kind="experience")
    assert recall(conn, "EDGE-LOG-02 nginx cutover")[0]["id"] == mid


def test_generic_knowledge_crosses_freely(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPGRAPH_MEM_SCOPE", "safe")
    conn = open_memory(str(tmp_path / "m.db"))
    retain(conn, GENERIC)
    assert recall(conn, "porter stemming inflected")[0]["text"] == GENERIC


def test_an_unknown_scope_falls_back_to_safe(monkeypatch):
    from loopgraph.memory import scope_default
    monkeypatch.setenv("LOOPGRAPH_MEM_SCOPE", "everything")
    assert scope_default() == "safe"
    monkeypatch.delenv("LOOPGRAPH_MEM_SCOPE")
    assert scope_default() == "safe"


# --- the corpus is the writer ------------------------------------------------

def test_retain_writes_a_file_and_indexes_it(tmp_path):
    from loopgraph.memory import write_markdown
    d = tmp_path / "corpus"
    write_markdown(str(d), "reference_thing", "a durable fact\n\nmore detail",
                   "world", tags=("x",))
    text = (d / "reference_thing.md").read_text()
    assert "name: reference_thing" in text and "type: reference" in text
    assert "- [reference_thing.md](reference_thing.md)" in (d / "MEMORY.md").read_text()


def test_the_file_marks_sensitivity_for_a_human_reader(tmp_path):
    from loopgraph.memory import write_markdown
    d = tmp_path / "corpus"
    write_markdown(str(d), "m", PRIVATE, "experience")
    assert "sensitive: true" in (d / "m.md").read_text()


def test_reindexing_the_same_memory_does_not_duplicate_the_index_line(tmp_path):
    from loopgraph.memory import write_markdown
    d = tmp_path / "corpus"
    write_markdown(str(d), "m", "first version of the fact", "world")
    write_markdown(str(d), "m", "second version of the fact", "world")
    lines = [l for l in (d / "MEMORY.md").read_text().splitlines() if l.startswith("- [m.md]")]
    assert len(lines) == 1 and "second version" in lines[0]


def test_forgetting_removes_the_file_and_the_index_line(tmp_path):
    from loopgraph.memory import remove_markdown, write_markdown
    d = tmp_path / "corpus"
    write_markdown(str(d), "m", "a fact", "world")
    assert remove_markdown(str(d), "m") is True
    assert not (d / "m.md").exists()
    assert "- [m.md]" not in (d / "MEMORY.md").read_text()


def _index_writer(directory: str, chunk: list[str]) -> None:
    from loopgraph.memory import write_markdown
    for mid in chunk:
        write_markdown(directory, mid, f"fact {mid}", "world")


# spawn rather than fork: forking a multi-threaded pytest process risks a
# deadlock in the child, and spawn reproduces the race just as reliably --
# measured both ways, with the lock removed this loses most of the 80 lines
# and with it none.
def test_concurrent_index_writes_do_not_lose_lines(tmp_path):
    """MEMORY.md is one shared file and several sessions write it at once.

    Both index writers were read-modify-write with nothing in between, so the
    loser of a race left its memory file on disk unlisted -- present in the
    directory, invisible to every session that loads the index. Observed
    live: a line written at 16:45:58 was gone within the minute, while a
    second process was repairing two older lines lost the same way.
    """
    import multiprocessing as mp
    from loopgraph.memory import write_markdown

    d = str(tmp_path / "corpus")
    ids = [f"mem-{i:03d}" for i in range(80)]
    write_markdown(d, "seed", "seed fact", "world")

    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_index_writer, args=(d, ids[i::4]))
             for i in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert [p.exitcode for p in procs] == [0, 0, 0, 0]

    listed = open(os.path.join(d, "MEMORY.md"), encoding="utf-8").read()
    lost = [m for m in ids if f"- [{m}.md]" not in listed]
    assert lost == [], f"{len(lost)} of {len(ids)} index lines lost to a race"
    assert listed.count("- [seed.md]") == 1


def test_the_index_is_disposable_and_rebuilds_from_the_files(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPGRAPH_MEM_SCOPE", "full")
    from loopgraph.memory import reindex, write_markdown
    d = tmp_path / "corpus"
    conn = open_memory(str(tmp_path / "m.db"))
    write_markdown(str(d), "a", "the first durable fact about parsers", "world")
    write_markdown(str(d), "b", "the second durable fact about parsers", "world")
    assert reindex(conn, str(d))["imported"] == 2
    assert len(recall(conn, "durable fact parsers")) == 2
    assert reindex(conn, str(d))["imported"] == 2       # idempotent, no doubling
    assert stats(conn)["memories"] == 2


@pytest.mark.parametrize("text,expected,why", [
    ("ORION-DB-04 was rebuilt on 198.51.100.7", True, "host and address"),
    ("the acme-console ingress is not managed by the deployer", True,
     "operator term"),
    ("orion is where the runbooks live", True, "operator term"),
    ("the exhibit package for a large-medical client RFP pursuit", True,
     "named engagement"),
    ("otel-collector.telemetry.svc.cluster.local:4317", True, "internal cluster dns"),
    # ...and the generic tooling knowledge that is the whole point of a
    # cross-harness memory must still cross.
    ("macOS has no timeout binary; use gtimeout from coreutils", False, ""),
    ("nested claude -p is unusable as a subprocess: MCP startup dominates", False, ""),
    ("never run parallel agents' git work in a shared checkout", False, ""),
    ("AWS SSO sessions expire mid-work; log in before a long task", False, ""),
])
def test_private_work_is_sensitive_and_tooling_knowledge_is_not(
        text, expected, why, tmp_path, monkeypatch):
    from loopgraph.memory import sensitivity
    _write_config(tmp_path, monkeypatch, 'terms = ["orion", "acme-console"]\n')
    assert bool(sensitivity(text)) is expected, f"{text!r} ({why})"


def test_the_classifier_does_not_fire_on_ordinary_acronyms():
    """Marking everything teaches people to run --scope full by reflex, which
    is worse than having no classifier at all."""
    from loopgraph.memory import sensitivity
    for text in ["the SQL query uses a CTE and hits the CPU hard",
                 "HTTP 429 from the API means back off",
                 "JSON and YAML both accept UTF-8 here"]:
        assert sensitivity(text) == [], text


def test_reindex_repairs_a_file_missing_from_the_index(tmp_path, monkeypatch):
    """A memory file that MEMORY.md does not list is invisible to the session
    that loads the index -- the divergence this whole design exists to stop,
    in miniature. Two real files were already in that state."""
    monkeypatch.setenv("LOOPGRAPH_MEM_SCOPE", "full")
    from loopgraph.memory import reindex, write_markdown
    d = tmp_path / "corpus"
    conn = open_memory(str(tmp_path / "m.db"))
    write_markdown(str(d), "listed", "a fact that made it into the index", "world")
    # A file written by hand, never indexed.
    (d / "unlisted.md").write_text(
        "---\nname: unlisted\ndescription: a fact nobody indexed\n---\n\nbody\n")
    got = reindex(conn, str(d))
    assert got["index_repaired"] == ["unlisted"]
    assert "- [unlisted.md]" in (d / "MEMORY.md").read_text()
    assert reindex(conn, str(d))["index_repaired"] == []      # nothing to redo


# --- reflect: which piles have no lesson on them -----------------------------

def test_reflect_finds_a_pile_with_no_conclusion_on_it(mem):
    for i in range(3):
        retain(mem, f"worker pipeline {i} wedged with a live process and frozen input",
               kind="experience")
    got = reflect(mem)
    assert len(got) == 1 and len(got[0]["members"]) == 3
    assert "wedged" in got[0]["shared"]


def test_reflect_ignores_a_pile_that_already_has_a_conclusion(mem):
    for i in range(3):
        retain(mem, f"worker pipeline {i} wedged with a live process and frozen input",
               kind="experience")
    retain(mem, "a wedged worker always needs a rolling restart, not a config fix",
           kind="model")
    assert reflect(mem) == []


def test_reflect_needs_a_pile_not_a_pair(mem):
    for i in range(2):
        retain(mem, f"worker pipeline {i} wedged with a live process", kind="experience")
    assert reflect(mem) == []


def test_unrelated_memories_are_not_a_cluster(mem):
    retain(mem, "the AWS region here is us-east-2", kind="experience")
    retain(mem, "porter stemming reaches inflected words", kind="experience")
    retain(mem, "gzip gets about three times on transcripts", kind="experience")
    assert reflect(mem) == []


def test_links_corroborate_a_theme_but_do_not_define_one(mem):
    """`relates_to` in the real corpus means "vaguely adjacent" -- 154 edges
    over 86 memories. Grouping by links produced a 27-member "theme" with no
    vocabulary in common. Vocabulary defines the pile; links are reported as
    corroboration."""
    a = retain(mem, "the invoice pdf renders upside down", kind="experience")
    b = retain(mem, "kafka consumers rebalanced twice", kind="experience")
    c = retain(mem, "the office printer jams on tuesdays", kind="experience")
    relate(mem, a, b)
    relate(mem, b, c)
    assert reflect(mem) == []                    # linked, but about nothing

    for i in range(3):
        retain(mem, f"redpanda broker {i} refused decommission at RF three",
               kind="experience")
    got = reflect(mem)
    assert len(got) == 1 and "redpanda" in got[0]["shared"]


def test_reflect_on_an_empty_store_is_empty(mem):
    assert reflect(mem) == []


def test_a_link_hairball_does_not_hide_every_pile(mem):
    """Transitive closure over relates_to put 72 of 86 real memories in one
    component; because that blob held all nine conclusions, reflect reported
    a clean corpus. A false all-clear is worse than no feature."""
    chain = [retain(mem, f"redpanda broker {i} refused to decommission at RF three",
                    kind="experience") for i in range(4)]
    far = retain(mem, "unrelated conclusion about billing", kind="model")
    # One long chain of links from the pile all the way to the conclusion.
    for a, b in zip(chain, chain[1:] + [far]):
        relate(mem, a, b)
    got = reflect(mem)
    assert got, "the redpanda pile still has no conclusion of its own"
    assert "redpanda" in got[0]["shared"]


def test_a_conclusion_on_the_theme_still_silences_it(mem):
    for i in range(4):
        retain(mem, f"redpanda broker {i} refused to decommission at RF three",
               kind="experience")
    retain(mem, "redpanda decommission always blocks at three brokers with RF three",
           kind="model")
    assert reflect(mem) == []


# `token` means two things and only one of them is private. Getting this wrong
# withheld a memory about token accounting as though it held a credential.
@pytest.mark.parametrize("text", [
    "the refresh token is stored in SSM under /mss/soc/key",
    "bearer token in the Authorization header",
    "rotate the token before Friday",
    "token_url points at the gov endpoint",
])
def test_credential_token_still_classifies(text):
    from loopgraph.memory import sensitivity
    assert "credential material or its location" in sensitivity(text)


@pytest.mark.parametrize("text", [
    "the session spent 196.8M output tokens across 298k turns",
    "input tokens are cached for an hour",
    "context tokens per turn rose from 227k to 278k",
    "the tokenizer is the slow part",
    "budget the run at 50k tokens/turn",
    "token count is the wrong metric here",
    "the brief costs ~123 tokens at session start",
    "budget 50k tokens for the sweep",
    "2M tokens across the corpus",
])
def test_measured_tokens_are_not_credentials(text):
    from loopgraph.memory import sensitivity
    assert sensitivity(text) == [], text


def test_retain_links_a_new_memory_to_what_it_is_about(tmp_path):
    """A link the writer must remember to type does not get typed: 141 of 214
    memories here had none, including three about the same host pool."""
    from loopgraph import memory
    conn = memory.open_memory(str(tmp_path / "m.db"))
    memory.retain(conn, "The SMS tenant logstash pipeline drops events on restart")
    b = memory.retain(conn, "logstash pipeline restart on the SMS tenant loses "
                            "queued events until the queue is persistent")
    assert memory.neighbours(conn, b), "a related memory must not land isolated"


def test_autolink_refuses_a_merely_common_word(tmp_path):
    """A wrong link widens every future expansion, so this prefers none."""
    from loopgraph import memory
    conn = memory.open_memory(str(tmp_path / "m.db"))
    memory.retain(conn, "kubernetes ingress annotations for the alb controller")
    b = memory.retain(conn, "the espresso machine in the kitchen needs descaling")
    assert memory.neighbours(conn, b) == []


def test_recall_reaches_a_memory_one_edge_away(tmp_path):
    from loopgraph import memory
    conn = memory.open_memory(str(tmp_path / "m.db"))
    a = memory.retain(conn, "AVD-NCAB-HP session hosts report Available")
    # Deliberately shares no vocabulary with the query, so the ONLY route to
    # it is the edge. Otherwise the test passes on a lexical hit and proves
    # nothing about expansion.
    b = memory.retain(conn, "quarterly badminton fixtures were rescheduled")
    memory.relate(conn, a, b)
    hits = memory.recall(conn, "AVD-NCAB-HP session hosts", k=5, scope="full")
    assert b in [h["id"] for h in hits], "the edge is the only way to reach it"
    assert next(h for h in hits if h["id"] == b)["via"] == a


def test_a_lexical_hit_always_outranks_an_inferred_one(tmp_path):
    from loopgraph import memory
    conn = memory.open_memory(str(tmp_path / "m.db"))
    a = memory.retain(conn, "AVD-NCAB-HP session hosts report Available")
    b = memory.retain(conn, "unrelated wording entirely, only reachable by edge")
    memory.relate(conn, a, b)
    hits = memory.recall(conn, "AVD-NCAB-HP session hosts", k=5, scope="full")
    assert hits[0]["id"] == a


def test_query_aliases_bridge_the_asker_s_vocabulary(tmp_path):
    """The corpus says tenant; the question says customer. Measured, that gap
    cost a third of realistic queries."""
    from loopgraph import memory
    conn = memory.open_memory(str(tmp_path / "m.db"))
    memory.retain(conn, "each tenant gets a z-prod bucket named for its id")
    hits = memory.recall(conn, "which bucket belongs to a customer", k=5, scope="full")
    assert hits, "an alias must bridge customer -> tenant"


def test_aliases_only_add_candidates_never_displace_a_literal(tmp_path):
    from loopgraph import memory
    conn = memory.open_memory(str(tmp_path / "m.db"))
    exact = memory.retain(conn, "tenant isolation on the multipart upload path")
    memory.retain(conn, "customer records live in the CRM")
    hits = memory.recall(conn, "tenant isolation multipart", k=5, scope="full")
    assert hits[0]["id"] == exact


# These narrowings free memories for every harness that is not trusted with
# client content. Both directions are tested because the failure modes are
# not symmetric: a false positive costs recall, a false negative leaks.
@pytest.mark.parametrize("text", [
    "the logger host is 10.24.30.73",
    "arn:aws:s3:us-east-2:198901727629:bucket/x",
    "account 198901727629 owns it",
    "john.fitch@cross-check.com approved it",
    "the refresh token is stored in SSM",
    "https://portal.someclient.com/admin is the console",
    "chi-mss.mss.svc.cluster.local resolves internally",
    "kubectl -n mss get pods",
])
def test_still_classified_after_narrowing(text):
    from loopgraph.memory import sensitivity
    assert sensitivity(text), f"LEAK: {text!r} no longer classifies"


@pytest.mark.parametrize("text", [
    "macOS version 6.2.0.9 changed the default handler",
    "clone with git@gitlab.com:group/repo.git",
    "an arn:aws:ec2: prefix appears in the policy",
    "https://management.usgovcloudapi.net is the gov endpoint",
    "names ending .svc.cluster.local resolve in-cluster",
    "noreply@github.com sent the notification",
])
def test_no_longer_a_false_positive(text):
    from loopgraph.memory import sensitivity
    assert sensitivity(text) == [], text


def test_recall_ranks_a_topical_title_over_a_long_omnibus(tmp_path):
    """A 19k-char programme note outranked the exact answer by collecting
    query terms it never had the subject of. The first line states what a
    memory is about; a match there is evidence of topic, not of length."""
    from loopgraph import memory
    conn = memory.open_memory(str(tmp_path / "m.db"))
    right = memory.retain(conn, "Device health monitoring lives in TimescaleDB\n\n"
                                "the source health tables are here")
    memory.retain(conn, "Alerting overhaul programme\n\n" + (
        "device data sending health monitoring track " * 60))
    hits = memory.recall(conn, "where is device health monitoring tracked",
                         k=3, scope="full")
    assert hits[0]["id"] == right
