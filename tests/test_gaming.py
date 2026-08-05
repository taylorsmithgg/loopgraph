import pytest
import json

from loopgraph import gaming


def test_schema_lists_every_property_as_required():
    """Structured outputs return HTTP 400 unless required covers every
    property when additionalProperties is false. This cost a debug cycle."""
    props = set(gaming.SCHEMA["properties"])
    assert gaming.SCHEMA["additionalProperties"] is False
    assert set(gaming.SCHEMA["required"]) == props


def test_missing_codex_fails_gracefully(monkeypatch):
    monkeypatch.setattr(gaming.shutil, "which", lambda _: None)
    r = gaming.check_gameable("s", "true", {})
    assert r["ok"] is False and "not on PATH" in r["error"]


def test_invocation_is_isolated_and_non_interactive(monkeypatch):
    """Config-isolated, non-blocking, and honouring an explicit sandbox."""
    seen = {}

    class P:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        out = argv[argv.index("-o") + 1]
        with open(out, "w") as fh:
            json.dump({"gameable": True, "cheat": "c",
                       "explanation": "e", "suggested_check": "s"}, fh)
        return P()

    monkeypatch.setattr(gaming.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(gaming.subprocess, "run", fake_run)
    r = gaming.check_gameable("statement", "grep -q done f.txt", {"exit_zero": True},
                              sandbox="read-only")
    a = seen["argv"]
    assert a[:2] == ["codex", "exec"]
    assert a[a.index("-s") + 1] == "read-only"      # explicit override wins
    assert "--ignore-user-config" in a
    assert 'approval_policy="never"' in a
    assert seen["kw"]["stdin"] is gaming.subprocess.DEVNULL
    assert r["ok"] is True and r["gameable"] is True


def test_unparseable_verdict_is_reported_not_raised(monkeypatch):
    class P:
        returncode, stdout, stderr = 1, "", "boom"

    monkeypatch.setattr(gaming.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(gaming.subprocess, "run", lambda *a, **k: P())
    r = gaming.check_gameable("s", "true", {})
    assert r["ok"] is False and "no parseable verdict" in r["error"]


def test_sandbox_is_inherited(tmp_path, monkeypatch):
    """Elevated sandbox is deliberate: it lets the auditor execute a candidate
    cheat and demonstrate it passes, rather than asserting that it would."""
    home = tmp_path
    (home / ".codex").mkdir()
    (home / ".codex" / "config.toml").write_text(
        'model = "x"\napproval_policy = "on-request"\nsandbox_mode = "danger-full-access"\n')
    monkeypatch.setattr(gaming.os.path, "expanduser", lambda p: str(home))
    pol = gaming.inherited_policy()
    assert pol["approval_policy"] == "on-request"
    assert pol["sandbox_mode"] == "danger-full-access"   # forwarded


def test_policy_defaults_to_never_without_config(tmp_path, monkeypatch):
    monkeypatch.setattr(gaming.os.path, "expanduser", lambda p: str(tmp_path))
    pol = gaming.inherited_policy()
    assert pol["approval_policy"] == "never"
    assert pol["sandbox_mode"] == "read-only"      # safe default, not a clamp


def test_explicit_sandbox_overrides_inherited(tmp_path, monkeypatch):
    seen = {}

    class P:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kw):
        seen["argv"] = argv
        with open(argv[argv.index("-o") + 1], "w") as fh:
            json.dump({"gameable": False, "cheat": "", "explanation": "",
                       "suggested_check": "", "demonstrated": False,
                       "evidence": ""}, fh)
        return P()

    monkeypatch.setattr(gaming.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(gaming.subprocess, "run", fake_run)
    monkeypatch.setattr(gaming, "inherited_policy",
                        lambda: {"approval_policy": "never",
                                 "sandbox_mode": "danger-full-access"})
    r = gaming.check_gameable("s", "true", {}, sandbox="read-only")
    assert seen["argv"][seen["argv"].index("-s") + 1] == "read-only"
    assert r["policy"]["sandbox"] == "read-only"


def test_policy_used_is_reported_back(monkeypatch):
    class P:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kw):
        with open(argv[argv.index("-o") + 1], "w") as fh:
            json.dump({"gameable": False, "cheat": "", "explanation": "",
                       "suggested_check": "", "demonstrated": False,
                       "evidence": ""}, fh)
        return P()

    monkeypatch.setattr(gaming.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(gaming.subprocess, "run", fake_run)
    monkeypatch.setattr(gaming, "inherited_policy",
                        lambda: {"approval_policy": "on-request",
                                 "sandbox_mode": "workspace-write"})
    r = gaming.check_gameable("s", "true", {})
    assert r["policy"] == {"approval": "on-request", "sandbox": "workspace-write"}


def test_implement_refuses_a_read_only_sandbox(monkeypatch):
    """Implementation needs to write; failing loudly beats a silent no-op."""
    monkeypatch.setattr(gaming.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(gaming, "inherited_policy",
                        lambda: {"approval_policy": "never",
                                 "sandbox_mode": "read-only"})
    r = gaming.implement("plan", [], ["x"], cwd=".")
    assert r["ok"] is False and "writable sandbox" in r["error"]


def test_implement_prompt_forbids_editing_the_criteria(monkeypatch):
    """The implementer must not be able to satisfy a check by weakening it."""
    seen = {}

    class P:
        returncode, stdout, stderr = 0, "", "tokens used\n1234\n"

    monkeypatch.setattr(gaming.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(gaming, "inherited_policy",
                        lambda: {"approval_policy": "never",
                                 "sandbox_mode": "workspace-write"})
    monkeypatch.setattr(gaming.subprocess, "run",
                        lambda argv, **kw: (seen.update(argv=argv), P())[1])
    crit = [{"id": "C1", "statement": "tests pass", "evidence_cmd": "pytest -q"}]
    r = gaming.implement("do the thing", crit, ["src/"], cwd=".")
    prompt = seen["argv"][-1]
    assert "Do not edit, weaken or delete the acceptance criteria" in prompt
    assert "C1" in prompt and "pytest -q" in prompt
    assert "src/" in prompt
    assert r["ok"] is True and r["tokens"] == 1234


def test_implement_reports_codex_failure(monkeypatch):
    class P:
        returncode, stdout, stderr = 1, "", "boom"

    monkeypatch.setattr(gaming.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(gaming, "inherited_policy",
                        lambda: {"approval_policy": "never",
                                 "sandbox_mode": "workspace-write"})
    monkeypatch.setattr(gaming.subprocess, "run", lambda *a, **k: P())
    r = gaming.implement("p", [], ["x"], cwd=".")
    assert r["ok"] is False and r["returncode"] == 1


def test_token_parsing_handles_thousands_separators(monkeypatch):
    """codex prints '8,215'; isdigit() missed it and reported tokens=0."""
    class P:
        returncode, stdout, stderr = 0, "", "tokens used\n8,215\n"

    monkeypatch.setattr(gaming.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(gaming, "inherited_policy",
                        lambda: {"approval_policy": "never",
                                 "sandbox_mode": "workspace-write"})
    monkeypatch.setattr(gaming.subprocess, "run", lambda *a, **k: P())
    assert gaming.implement("p", [], ["x"], cwd=".")["tokens"] == 8215


def test_tokens_are_parsed_from_stderr_not_stdout():
    """codex reports usage on stderr; stdout holds only the final message."""
    err = "codex\nOK\ntokens used\n4,242\n"
    assert gaming._parse_tokens(err, "OK\n") == 4242
    assert gaming._parse_tokens("", "OK\n") == 0


# --- sabotage vs shortcut ----------------------------------------------------
# Every criterion audited on the real graph came back gameable, including
# `uv run pytest -q` (cheated with a conftest.py that skips everything). A
# verdict that fires on 100% of inputs carries no information.

@pytest.mark.parametrize("cheat", [
    'tmpdir=$(mktemp -d /tmp/loopgraph-cheat-XXXX); printf ... > $tmpdir/loopgraph; '
    'chmod +x $tmpdir/loopgraph; PATH=$tmpdir:$PATH loopgraph mem stats',
    "wrote a root conftest.py with a collection hook that skips every test",
    "alias loopgraph=true",
    "export PATH=/tmp/fake:$PATH",
])
def test_environment_subversion_is_classified_sabotage(cheat):
    from loopgraph.gaming import classify_cheat
    assert classify_cheat(cheat) == "sabotage"


@pytest.mark.parametrize("cheat", [
    "touch done.txt",
    "echo 5 > count.txt so the stdout_int_gte check passes",
    "add a row to the table without running the importer",
    "commit an empty file named report.pdf",
])
def test_doing_less_work_is_classified_shortcut(cheat):
    """The only kind worth rewriting a check over."""
    from loopgraph.gaming import classify_cheat
    assert classify_cheat(cheat) == "shortcut"


def test_status_separates_the_two(tmp_path):
    from loopgraph import coord
    from loopgraph.db import open_db
    from loopgraph.graph import add_criterion
    conn = open_db(str(tmp_path / "g.db"))
    add_criterion(conn, "SHORT", "s", "test -f done.txt", {})
    add_criterion(conn, "SABO", "s", "uv run pytest -q", {})
    coord.record_audit(conn, "SHORT", {"gameable": True, "cheat": "touch done.txt"})
    coord.record_audit(conn, "SABO", {"gameable": True,
                                      "cheat": "add a conftest.py that skips all"})
    state = coord.audit_state(conn)
    assert state["gameable"] == ["SHORT"]
    assert state["sabotage_only"] == ["SABO"]
