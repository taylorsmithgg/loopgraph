"""Adversarial spec-gaming check via a second vendor.

The one cross-model role the evidence supports. Measured findings that shape
this module:
  - ensembling models for detection REDUCES quality (F1 0.365 -> 0.333), so
    this is not a second opinion on correctness;
  - LLM judges over-reject conformant work 35-45%, so this never gates
    anything -- it runs at authoring time and a human decides.

It answers one question: can this criterion's evidence command be satisfied
WITHOUT achieving the criterion's stated intent?
"""

from __future__ import annotations

import re

import json
import os
import shutil
import subprocess
import tempfile

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    # Structured outputs require EVERY property to appear in `required`
    # when additionalProperties is false; a partial list returns HTTP 400.
    "required": ["gameable", "cheat", "explanation", "suggested_check",
                 "demonstrated", "evidence"],
    "properties": {
        "gameable": {"type": "boolean"},
        "cheat": {"type": "string",  # empty when not gameable
                  "description": "shell command or edit that satisfies the check without the intent"},
        "explanation": {"type": "string"},
        "suggested_check": {"type": "string",
                            "description": "a harder evidence command, if one exists"},
        "demonstrated": {"type": "boolean",
                         "description": "true only if the cheat was actually executed and observed to pass"},
        "evidence": {"type": "string",
                     "description": "observed output when demonstrated"},
    },
}

PROMPT = """You are auditing an acceptance check for spec gaming. Do not fix anything.

CRITERION: {statement}
EVIDENCE COMMAND: {cmd}
PASSES WHEN: {expect}

Question: can this command be made to pass WITHOUT achieving the stated
intent? Consider editing the file the check reads, creating a fixture,
hardcoding output, touching a marker, or satisfying the letter of the
expectation while the underlying work remains undone.

If your sandbox permits writing, DO NOT GUESS. Actually perform the cheat in
a scratch directory, run the evidence command against it, and confirm it
passes. Set demonstrated=true only if you ran it and saw it pass, and put the
observed output in `evidence`. Clean up anything you created.

If you cannot execute, reason carefully and set demonstrated=false.

Answer honestly. If the check is genuinely hard to fake, say gameable=false.
Do not invent a cheat that would not actually work."""


def inherited_policy() -> dict:
    """Read the user's codex policy so it can be forwarded explicitly.

    --ignore-user-config is used for startup speed and tool isolation, which
    also drops approval_policy; without forwarding it codex reverts to
    prompting and blocks forever on a pipe.

    sandbox_mode is forwarded too. An elevated sandbox lets the auditor
    EXECUTE a candidate cheat and demonstrate that it passes, which turns
    the verdict from an opinion into evidence. The cost is that the auditor
    can write to the tree it is auditing -- run it on a clean tree, or pass
    --sandbox read-only when that matters more than proof.
    """
    path = os.path.join(os.path.expanduser("~"), ".codex", "config.toml")
    approval, sandbox, model, effort = "never", "read-only", "", ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("["):
                    break                      # top-level table only
                for key, name in (("approval_policy", "approval"),
                                  ("sandbox_mode", "sandbox"),
                                  ("model_reasoning_effort", "effort"),
                                  ("model", "model")):
                    if line.startswith(key + " ") or line.startswith(key + "="):
                        val = line.split("=", 1)[1].strip().strip('"\'')
                        if name == "approval":
                            approval = val
                        elif name == "sandbox":
                            sandbox = val
                        elif name == "effort":
                            effort = val
                        elif name == "model" and not model:
                            model = val
                        break
    except Exception:
        pass
    return {"approval_policy": approval, "sandbox_mode": sandbox,
            "model": model, "model_reasoning_effort": effort}


def codex_available() -> bool:
    return shutil.which("codex") is not None


# Every check audited so far came back gameable, including `uv run pytest -q`
# -- cheated by dropping a conftest.py that skips every test. That is true and
# useless: a verdict that fires on 100% of inputs carries no information, and
# an alarm that is always on gets silenced rather than heeded.
#
# The distinction that does carry information is HOW. Shadowing `loopgraph` on
# PATH or writing a conftest into the repo is sabotage of the environment: an
# agent that will do that can defeat any evidence command ever written, so the
# criterion is not the weak link. A shortcut -- satisfying the letter of the
# check by doing less work, with no tampering -- is a criterion problem, and
# the only kind worth rewriting a check over.
SABOTAGE = re.compile(
    r"(?:\bPATH=|\bexport PATH\b|mktemp -d.*(?:shim|fake|cheat)|"
    r"conftest\.py|\balias \w+=|chmod \+x .*(?:fake|shim|stub)|"
    r"cat\s*>\s*\S*/(?:bin|usr)/|ln -s .*/(?:bin|usr)/|"
    r"sitecustomize|LD_PRELOAD|monkeypatch|--override-ini)", re.I)


def classify_cheat(cheat: str, explanation: str = "") -> str:
    """`sabotage` if the cheat had to subvert the environment, else `shortcut`.

    Only `shortcut` says anything about the criterion itself.
    """
    return "sabotage" if SABOTAGE.search(f"{cheat}\n{explanation}") else "shortcut"


def check_gameable(statement: str, cmd: str, expect: dict, model: str = "",
                   cwd: str | None = None, timeout: int = 300,
                   approval: str = "", sandbox: str = "") -> dict:
    """Run the audit read-only in a second vendor. Never raises."""
    if not codex_available():
        return {"ok": False, "error": "codex not on PATH"}
    with tempfile.TemporaryDirectory() as td:
        schema_p = os.path.join(td, "schema.json")
        out_p = os.path.join(td, "out.json")
        with open(schema_p, "w") as fh:
            json.dump(SCHEMA, fh)
        pol = inherited_policy()
        sb = sandbox or pol["sandbox_mode"]
        argv = ["codex", "exec", "--skip-git-repo-check",
                "-s", sb,
                # skip the user's config: their MCP servers, skills and hooks
                # dominate startup, and an auditor should not hold those tools
                "--ignore-user-config",
                # --ignore-user-config drops the user's approval_policy, and
                # the default prompts -- which blocks forever on a pipe.
                # read-only sandbox already bounds what it can do.
                "-c", f'approval_policy="{approval or pol["approval_policy"]}"',
                # measured on this audit: low 21.8s, medium 32.8s, high 40.0s,
                # all reaching the same verdict; minimal fails outright.
                "-c", 'model_reasoning_effort="low"',
                "--output-schema", schema_p, "-o", out_p]
        eff = model or pol.get("model", "")
        if eff:
            argv += ["-m", eff]                # --ignore-user-config drops the
                                               # user's model; without this it
                                               # silently falls back to codex's
                                               # default, which is far slower
        argv.append(PROMPT.format(statement=statement, cmd=cmd,
                                  expect=json.dumps(expect or {"exit_zero": True})))
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout, cwd=cwd,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"codex timed out after {timeout}s"}
        try:
            with open(out_p) as fh:
                body = fh.read().strip()
            data = json.loads(body)
        except Exception:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
            return {"ok": False, "error": "no parseable verdict",
                    "detail": " | ".join(tail), "returncode": proc.returncode}
        data["ok"] = True
        data["policy"] = {"approval": approval or pol["approval_policy"],
                          "sandbox": sb}
        return data


IMPLEMENT_PROMPT = """Implement the plan below. Do not redesign it.

PLAN
----
{plan}

ACCEPTANCE CRITERIA - your work is judged ONLY by these commands passing:
{criteria}

Rules:
- Stay inside this scope; do not touch anything outside it: {scope}
- Do not edit, weaken or delete the acceptance criteria or their evidence
  commands. Satisfy them by doing the work.
- Run the criteria yourself before you finish and report what passes.
- If the plan is wrong or a criterion is unsatisfiable, stop and say so
  rather than working around it.
"""


def _parse_tokens(*streams: str) -> int:
    """codex reports usage on STDERR, not stdout - stdout carries only the
    final message. It also prints thousands separators ("8,215"), so a bare
    isdigit() check misses it. Both cost a debugging cycle."""
    for stream in streams:
        lines = (stream or "").splitlines()
        for i, line in enumerate(lines):
            if "tokens used" not in line.lower():
                continue
            for nxt in lines[i + 1:i + 3]:
                bare = nxt.strip().replace(",", "").replace("_", "")
                if bare.isdigit():
                    return int(bare)
    return 0


def implement(plan: str, criteria: list[dict], scope: list[str], cwd: str,
              model: str = "", timeout: int = 3600,
              approval: str = "", sandbox: str = "") -> dict:
    """Hand a plan to codex to implement. Evidence backs the split:
    on SE Bench GPT-5.5 scored 62.5/100 executing an Opus-written plan
    versus low-to-mid 40s planning for itself. Codex implements; it does
    not plan."""
    if not codex_available():
        return {"ok": False, "error": "codex not on PATH"}
    pol = inherited_policy()
    sb = sandbox or pol["sandbox_mode"]
    if sb == "read-only":
        return {"ok": False,
                "error": "implementation needs a writable sandbox; "
                         "pass --sandbox workspace-write"}
    crit_txt = "\n".join(
        f"  - {c['id']}: {c['statement']}\n      $ {c['evidence_cmd']}"
        for c in criteria) or "  (none declared)"
    argv = ["codex", "exec", "--skip-git-repo-check", "-s", sb,
            "--ignore-user-config",
            "-c", f'approval_policy="{approval or pol["approval_policy"]}"']
    eff = model or pol.get("model", "")
    if eff:
        argv += ["-m", eff]
    argv.append(IMPLEMENT_PROMPT.format(plan=plan, criteria=crit_txt,
                                        scope=", ".join(scope) or "(unset)"))
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"codex timed out after {timeout}s"}
    tokens = _parse_tokens(proc.stderr, proc.stdout)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "tokens": tokens, "sandbox": sb,
            "stdout_tail": "\n".join((proc.stdout or "").splitlines()[-15:]),
            "stderr_tail": "\n".join((proc.stderr or "").splitlines()[-8:])}
