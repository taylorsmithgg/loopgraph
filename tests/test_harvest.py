"""Mining transcripts for memory candidates.

The two ways this goes wrong: learning the agent's own injected context back
as if it were the user speaking, and proposing the same forty candidates
forever until nobody reads the harvest at all.
"""

import json

import pytest

from loopgraph.harvest import (
    mine, normalise, read_transcript, transcripts, undistilled,
)
from loopgraph.memory import open_memory, retain

ERR = "Error: could not load FFI Provider from /tmp/jruby-1234/native.so"


def _line(role, parts, **kw):
    return json.dumps({"type": role, "message": {"role": role, "content": parts}, **kw})


def _text(role, text):
    return _line(role, [{"type": "text", "text": text}])


def _result(text):
    return _line("user", [{"type": "tool_result", "content": text}])


def _session(tmp_path, name, lines):
    p = tmp_path / f"{name}.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    for i in range(3):
        _session(root, f"sess{i}", [
            json.dumps({"type": "mode", "mode": "normal"}),          # meta line
            _text("user", "please fix the logger"),
            _result(f"{ERR} (attempt {i})"),
        ])
    _session(root, "sess-correction", [
        _text("user", "no, that's wrong - never run a config test on a live box"),
        _text("assistant", "understood"),
    ])
    return str(root)


def test_meta_lines_and_thinking_are_not_content(tmp_path):
    p = _session(tmp_path, "s", [
        json.dumps({"type": "last-prompt", "leafUuid": "x"}),
        _line("assistant", [{"type": "thinking", "thinking": "hmm"}]),
        _text("user", "hello there"),
    ])
    assert [t for _, _, t in read_transcript(p)] == ["hello there"]


def test_a_truncated_tail_does_not_abort_the_file(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(_text("user", "first line is fine") + "\n{ this is not json")
    assert [t for _, _, t in read_transcript(str(p))] == ["first line is fine"]


def test_recurrence_across_sessions_is_the_signal(corpus):
    got = mine(transcripts(corpus), min_sessions=3)
    assert got["scanned"] == 4
    top = got["recurring_errors"][0]
    assert top["sessions"] == 3 and "FFI" in top["example"]


def test_one_off_errors_are_not_candidates(corpus):
    assert mine(transcripts(corpus), min_sessions=4)["recurring_errors"] == []


def test_volatile_detail_collapses_so_the_same_failure_counts_once():
    a = normalise("Error at /Users/x/p/f.py line 42: connection to 192.0.2.1 refused")
    b = normalise("Error at /Users/y/q/g.py line 7: connection to 192.168.1.9 refused")
    assert a == b


def test_corrections_are_captured(corpus):
    texts = [c["text"] for c in mine(transcripts(corpus))["corrections"]]
    assert any("never run a config test" in t for t in texts)


def test_ordinary_requests_are_not_corrections(corpus):
    texts = [c["text"] for c in mine(transcripts(corpus))["corrections"]]
    assert not any("please fix the logger" == t for t in texts)


def test_injected_context_is_never_mistaken_for_the_user(tmp_path):
    """The hook's own output, read back as a user correction, is a system
    citing itself as evidence."""
    root = tmp_path / "p"
    root.mkdir()
    _session(root, "s", [
        _text("user", "<system-reminder>no, never do that</system-reminder>"),
        _text("user", "Possibly relevant, from memory (ranked): don't use X"),
    ])
    assert mine(transcripts(str(root)))["corrections"] == []


def test_a_pasted_dump_is_not_a_correction(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    _session(root, "s", [_text("user", "no, wrong: " + "x" * 3000)])
    assert mine(transcripts(str(root)))["corrections"] == []


def test_since_days_limits_the_scan(corpus, tmp_path):
    import os, time
    old = os.path.join(corpus, "sess0.jsonl")
    os.utime(old, (time.time() - 90 * 86400,) * 2)
    assert len(transcripts(corpus, since_days=30)) == 3


def test_candidates_already_known_are_dropped(tmp_path):
    conn = open_memory(str(tmp_path / "m.db"))
    retain(conn, "hardened hosts mount /tmp noexec so the JVM dies "
                 "with 'Could not load FFI Provider'; set java.io.tmpdir")
    known = "could not load FFI Provider JVM noexec tmpdir"
    fresh = "redpanda decommission blocks at three brokers with RF three"
    assert undistilled(conn, [known, fresh]) == [fresh]


def test_nothing_known_yet_means_everything_is_a_candidate(tmp_path):
    conn = open_memory(str(tmp_path / "m.db"))
    cands = ["some failure nobody has recorded", "another one entirely"]
    assert undistilled(conn, cands) == cands


def test_source_code_is_not_a_failure(tmp_path):
    """Tool results are mostly file contents, and source is full of the word
    'error'. Unfiltered, the top of every harvest is `except Exception as e:`
    read out of hundreds of files -- a miner that recognises Python, not
    failure."""
    root = tmp_path / "p"
    root.mkdir()
    for i in range(4):
        _session(root, f"s{i}", [_result(
            "    92\t        except Exception as e:  # noqa\n"
            "+  const [error, setError] = useState<Error | null>(null);\n"
            "   130\t        raise RuntimeError('cannot find the thing')\n")])
    assert mine(transcripts(str(root)), min_sessions=3)["recurring_errors"] == []


def test_a_real_failure_beside_source_code_still_lands(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    for i in range(4):
        _session(root, f"s{i}", [_result(
            "    92\t        except Exception as e:\n"
            "fatal: could not read Username for 'https://gitlab.com': "
            "terminal prompts disabled\n")])
    got = mine(transcripts(str(root)), min_sessions=3)["recurring_errors"]
    assert len(got) == 1 and "terminal prompts disabled" in got[0]["example"]


def test_a_long_task_brief_is_not_a_correction(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    _session(root, "s", [_text("user",
        "Execute exactly ONE task from the plan. " + "context " * 100 +
        " use the helper instead of writing a new one")])
    assert mine(transcripts(str(root)))["corrections"] == []


def test_a_correction_must_lead_with_the_correction(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    _session(root, "s", [
        _text("user", "no, that's wrong - the region is us-east-2"),
        _text("user", "build the parser and then, much later in the process, "
                      "consider whether to revert the earlier approach here"),
    ])
    got = [c["text"] for c in mine(transcripts(str(root)))["corrections"]]
    assert got == ["no, that's wrong - the region is us-east-2"]


# --- codex rollouts: same signals, different schema --------------------------

def _codex(*payloads):
    return "\n".join(json.dumps({"timestamp": "2026-08-05T00:00:00Z",
                                 "type": "response_item", "payload": p})
                     for p in payloads)


def test_codex_user_turns_and_tool_output_are_read(tmp_path):
    from loopgraph.harvest import read_any
    p = tmp_path / "rollout-2026-08-05T00-00-00-abc.jsonl"
    p.write_text(_codex(
        {"type": "message", "role": "user",
         "content": [{"type": "input_text", "text": "no, that is wrong"}]},
        {"type": "function_call_output", "output": "fatal: repository not found"},
    ))
    assert list(read_any(str(p))) == [
        ("user", "text", "no, that is wrong"),
        ("user", "tool_result", "fatal: repository not found"),
    ]


def test_codex_developer_policy_text_is_not_a_person(tmp_path):
    """`developer` is injected policy. Mining it is the same mistake as
    mining our own hook output back."""
    from loopgraph.harvest import read_any
    p = tmp_path / "rollout-x.jsonl"
    p.write_text(_codex({"type": "message", "role": "developer",
                         "content": [{"type": "input_text",
                                      "text": "no, never use the network"}]}))
    assert list(read_any(str(p))) == []


def test_the_reader_is_chosen_by_content_not_by_filename(tmp_path):
    """Codex names rollouts `rollout-*.jsonl` today; the first line of the
    file is a stronger promise than a naming convention."""
    from loopgraph.harvest import read_any
    claude = tmp_path / "rollout-looks-like-codex.jsonl"
    claude.write_text(_text("user", "actually this is a claude transcript"))
    assert list(read_any(str(claude))) == [
        ("user", "text", "actually this is a claude transcript")]


def test_codex_and_claude_recurrence_counts_together(tmp_path):
    root = tmp_path / "mixed"
    root.mkdir()
    for i in range(2):
        _session(root, f"claude{i}", [_result(
            "fatal: could not read Username for 'https://gitlab.com'")])
    for i in range(2):
        (root / f"rollout-{i}.jsonl").write_text(_codex(
            {"type": "function_call_output",
             "output": "fatal: could not read Username for 'https://gitlab.com'"}))
    got = mine(transcripts(str(root)), min_sessions=4)["recurring_errors"]
    assert len(got) == 1 and got[0]["sessions"] == 4


@pytest.mark.parametrize("line", [
    'command -v helm >/dev/null    || die "helm not found (needed to render)"',
    'chsql "$F/00.sql" || die "stand-in tables failed to create"',
    "Tests  21 failed | 534 passed (555)",
    "\x1b[1mTest Suites: \x1b[31m1 failed\x1b[39m, 1 total",
    "!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!",
])
def test_shell_source_and_test_scores_are_not_lessons(tmp_path, line):
    """A test summary is a score, not a lesson: "21 failed | 534 passed"
    recurring says a suite was red a lot, which nobody can act on later."""
    root = tmp_path / "p"
    root.mkdir()
    for i in range(4):
        _session(root, f"s{i}", [_result(line)])
    assert mine(transcripts(str(root)), min_sessions=3)["recurring_errors"] == []


def test_ansi_colouring_does_not_split_one_failure_into_many(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    for i, colour in enumerate(["\x1b[31m", "\x1b[1;31m", "", "\x1b[0m"]):
        _session(root, f"s{i}", [_result(
            f"{colour}fatal: could not read Username for 'https://gitlab.com'")])
    got = mine(transcripts(str(root)), min_sessions=4)["recurring_errors"]
    assert len(got) == 1 and got[0]["sessions"] == 4


def test_ide_injected_context_is_not_a_correction(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    _session(root, "s", [_text("user",
        "# Context from my IDE setup:\n## Open tabs:\nno, wrong file")])
    assert mine(transcripts(str(root)))["corrections"] == []


def test_case_does_not_split_one_trap_into_two(tmp_path):
    """`fatal:` and `Fatal:` split 78 rediscoveries into 47 and 31, and both
    halves then look less urgent than the whole."""
    root = tmp_path / "p"
    root.mkdir()
    for i, case in enumerate(["fatal:", "Fatal:", "FATAL:", "fatal:"]):
        _session(root, f"s{i}", [_result(
            f"{case} not a git repository (or any of the parent directories)")])
    got = mine(transcripts(str(root)), min_sessions=4)["recurring_errors"]
    assert len(got) == 1 and got[0]["sessions"] == 4
