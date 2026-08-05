"""Weakness as the selection proxy for evidence commands.

Bennett, *The Optimal Choice of Hypothesis Is the Weakest, Not the Shortest*
(arXiv:2301.12987): among hypotheses that entail the observations, the one
with the **largest extension** generalises best -- 1.1x to 5x the rate of
minimum-description-length selection. Shortest is not merely a different
proxy, it is a worse one.

A criterion is a hypothesis about done-ness, and its extension is the set of
world-states in which its evidence command exits 0. Two consequences, and the
order between them is the whole idea:

1. **Entailment first.** A check that is already green before the work exists
   does not entail the observation "the goal is not yet met" -- it explains
   nothing that is happening. `true`, `echo ok` and `test -f README.md` have
   maximal extension and zero worth. So weakness never ranks a candidate that
   has not first passed `discriminates()`. Maximising weakness without that
   gate selects `true` every time.

2. **Then widest.** Among checks that do fail now, prefer the one admitting
   the most implementations while still failing whenever the goal is unmet.
   `grep -q "queue.persistent = true" config.yml` admits exactly one fix;
   "restart it fifty times and assert nothing was dropped" admits every fix
   that works. The first turns the gate into a leash toward one guessed
   implementation; the second holds the outcome and lets the work find its
   own route.

Brevity survives only as a tie-break, which is precisely the standing the
paper leaves it.
"""

from __future__ import annotations

import re
import subprocess

# Structural estimate of extension size. We cannot enumerate world-states, so
# we read the command for what it pins down. Each pattern carries the reason
# it moves the score, because a number nobody can argue with is a number
# nobody can fix.
NARROWING: list[tuple[str, float, str]] = [
    (r"\b(md5|sha\d*)sum\b|\bcmp\b|\bdiff\b", -0.30,
     "compares exact bytes: one artifact satisfies it"),
    (r"\bgrep\b|\brg\b|\back\b", -0.25,
     "greps for literal text: pins the implementation's wording"),
    (r"(?:\btest\s+-[fesd]\b|\[\s+-[fesd]\s)", -0.25,
     "asserts an artifact exists, not that anything works"),
    (r"-eq\s+\d+|==\s*\d+\b", -0.20,
     "pins an exact number: a valid change to the count breaks it"),
    (r"\bgit\s+(?:show|log)\b.*\b[0-9a-f]{7,40}\b", -0.20,
     "pins a specific commit"),
    (r"\bsed\s+-n\s*['\"]?\d+|head\s+-n\s*\d+|\bawk\b.*NR\s*==\s*\d+", -0.15,
     "pins line numbers"),
]

WIDENING: list[tuple[str, float, str]] = [
    (r"\b(pytest|tox|nox|unittest|jest|vitest|mocha|npm\s+(?:run\s+)?test"
     r"|pnpm\s+(?:run\s+)?test|yarn\s+test|cargo\s+test|go\s+test"
     r"|make\s+(?:test|check)|dotnet\s+test|mvn\s+test|gradle\s+test)\b", 0.35,
     "runs the suite: any implementation that works satisfies it"),
    (r"\bfor\s+\w+\s+in\b|\bseq\b|\bxargs\b|\bwhile\b|--repeat|hypothesis|fuzz",
     0.20, "quantifies over many inputs rather than one"),
    (r"\$\([^)]+\)\s*(?:-eq|-ne|==|!=)\s*\$\([^)]+\)", 0.20,
     "asserts a relation between two measured values, not a constant"),
    (r"\bcurl\b|\bhttp\b|localhost:\d+|\bnc\s+-z\b", 0.15,
     "exercises the running system from outside"),
]

BASE_SCORE = 0.5

# Auto-derived commands get executed. A model that misreads "clean up the
# branch" can write something that is not recoverable, so refuse the shapes
# that destroy state outright rather than trusting the phrasing.
UNSAFE: list[tuple[str, str]] = [
    (r"\brm\s+(?:-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rf]", "removes files"),
    (r"\bgit\s+(?:push|reset\s+--hard|clean\s+-[a-zA-Z]*[fd]|checkout\s+\.)",
     "rewrites or publishes git state"),
    (r"\bmkfs\b|\bdd\s+.*\bof=|\bshred\b|\bfdisk\b", "writes to devices"),
    (r"\bsudo\b|\bdoas\b", "escalates privilege"),
    (r"(?:curl|wget)[^|;&]*\|\s*(?:ba)?sh", "pipes the network into a shell"),
    (r":\(\)\s*\{.*\}\s*;\s*:", "fork bomb"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b", "takes the machine down"),
    (r"\bdrop\s+(?:table|database)\b", "drops a database object"),
    (r">\s*/dev/(?:sd|nvme|disk)", "writes to a raw disk"),
]


def is_safe(cmd: str) -> tuple[bool, str]:
    """Refuse commands that destroy state. Only meaningful for commands we
    did not get from a human -- a person typing `rm` meant it."""
    for pat, why in UNSAFE:
        if re.search(pat, cmd, re.I):
            return False, why
    return True, ""


def weakness(cmd: str) -> dict:
    """Estimate extension size in [0, 1]. Higher is weaker is better.

    Only meaningful for a command that already discriminates -- see the
    module docstring. `true` scores high here and is worthless.
    """
    score = BASE_SCORE
    reasons: list[tuple[float, str]] = []
    for pat, delta, why in NARROWING + WIDENING:
        if re.search(pat, cmd, re.I):
            score += delta
            reasons.append((delta, why))
    score = max(0.0, min(1.0, score))
    reasons.sort(key=lambda r: r[0])
    return {"score": round(score, 3), "reasons": reasons}


def discriminates(cmd: str, cwd: str | None = None, timeout: int = 60) -> dict:
    """The entailment gate: does this check fail in the world as it is now?

    A check that is green before the work explains nothing about the goal
    being unmet, however well-phrased it is. Timeouts and unrunnable
    commands count as non-discriminating: we could not observe the
    distinction, so we do not get to claim it.
    """
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": None, "why": "timed out"}
    except Exception as exc:
        return {"ok": False, "exit_code": None, "why": f"could not run: {exc}"}
    if p.returncode == 0:
        return {"ok": False, "exit_code": 0,
                "why": "already green before the work: it cannot tell done from not-done"}
    return {"ok": True, "exit_code": p.returncode,
            "why": (p.stdout or p.stderr or "").strip().splitlines()[-1:] and
                   (p.stdout or p.stderr).strip().splitlines()[-1] or ""}


def choose_weakest(
    candidates: list[str], cwd: str | None = None, timeout: int = 60,
    require_safe: bool = True,
) -> dict:
    """Bennett's rule, in the order that makes it work.

    Filter to the candidates that entail the observation (red right now),
    then take the widest extension among them. Length breaks ties and
    nothing else, which is the only job the paper leaves it.

    Returns {"best": cmd|None, "considered": [...], "why": str}.
    """
    considered: list[dict] = []
    for cmd in candidates:
        row = {"cmd": cmd, "safe": True, "discriminates": False,
               "weakness": weakness(cmd)["score"], "why": ""}
        if require_safe:
            ok, why = is_safe(cmd)
            if not ok:
                row.update(safe=False, why=f"refused: {why}")
                considered.append(row)
                continue
        d = discriminates(cmd, cwd=cwd, timeout=timeout)
        row["discriminates"] = d["ok"]
        row["why"] = d["why"]
        considered.append(row)

    live = [r for r in considered if r["safe"] and r["discriminates"]]
    if not live:
        return {"best": None, "considered": considered,
                "why": "no candidate failed in the current state, so none of "
                       "them distinguishes done from not-done"}
    # Weakest first; shortest only to break an exact tie.
    live.sort(key=lambda r: (-r["weakness"], len(r["cmd"])))
    return {"best": live[0]["cmd"], "considered": considered,
            "why": f"weakest discriminating candidate ({live[0]['weakness']})"}
