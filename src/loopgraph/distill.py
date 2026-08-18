"""Run distillation on a schedule, so it stops depending on being remembered.

Every mechanism this needs already existed: `harvest` mines transcripts for
what recurred across sessions, `reflect` finds experience nobody generalised
from, `undistilled` drops candidates memory already answers. They work. They
had been invoked seven times in the life of the corpus, because each one is a
thing a person has to think of doing, and nobody thinks of it while busy.

So this is not new capability. It is the same capability with the human
removed from the trigger: a scheduled run writes candidates to a file, and a
session reads the file at start. Scanning ~1,500 transcripts takes seconds and
cannot happen in a session's critical path; reading a small JSON file can.

The file records when it ran. A schedule that dies silently would leave every
session reading month-old candidates as though they were current, which is the
failure this whole tool exists to name, so staleness is part of the payload
rather than something a reader has to infer.
"""
from __future__ import annotations

import json
import os
import time

STATE = os.path.join(os.path.expanduser("~"), ".loopgraph", "distill.json")
MAX_RECURRING = 12
MAX_CORRECTIONS = 12
MAX_CLUSTERS = 8


GENERIC = (
    "traceback (most recent call last)",
    "error:", "warning:", "exception:", "fatal:", "usage:",
    "the above exception was the direct cause",
    "during handling of the above exception",
)


def _informative(rows: list[dict]) -> list[dict]:
    """Drop entries that name no failure, and collapse near-duplicates.

    Two OTEL lines differing only in a retry counter arrived as separate
    findings at 123 and 118 sessions -- the same fact, counted twice, taking
    two of the few lines a session start can afford.
    """
    import re
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        text = (r.get("example") or "").strip()
        low = text.lower()
        if len(text) < 25 or any(low.startswith(g) or low == g for g in GENERIC):
            continue
        # Collapse on shape: digits, hex ids and paths removed.
        shape = re.sub(r"0x[0-9a-f]+|\b\d+\b|/[\w./-]+", "#", low)[:90]
        if shape in seen:
            continue
        seen.add(shape)
        out.append(r)
    return out


# What the correction was ABOUT. Mined from 51 real corrections: the counts
# were 16 / 9 / 9 / 2 / 1 in this order, so the ranking is not hypothetical.
# A list of complaints is not usable; the CLASS is, because it names what to
# do differently on the next turn.
CORRECTION_CLASSES = (
    ("asserted without verifying",
     r"incorrect|not true|false|no way|wrong about|"
     r"you must be doing something wrong|seems incorrect|you say this",
     "produce claims about system behaviour by RUNNING the system"),
    ("declared done while still broken",
     r"still (?:not|broken|failing|incorrect)|didn'?t work|not fixed|"
     r"doesn'?t work|not capturing|feels broken",
     "verify the effect, not the status"),
    ("looked in the wrong place",
     r"looking in the wrong|wrong spot|wrong place|you didn'?t (?:look|check)|"
     r"missed|not seeing",
     "widen the query before concluding absence"),
    ("stopped early",
     r"you broke|pausing|gave up|why did you stop|keep going|finish",
     "finish the work; report blockers without stopping on them"),
    ("ignored an instruction",
     r"follow instructions|i (?:said|told you)|as i said|already told",
     "re-read the instruction before the next action"),
)


def _classify(corrections: list[str]) -> list[dict]:
    """Rank correction classes. Empty when there is nothing to say."""
    import re
    tally: dict[str, int] = {}
    advice: dict[str, str] = {}
    for text in corrections:
        for name, pattern, how in CORRECTION_CLASSES:
            if re.search(pattern, text, re.I):
                tally[name] = tally.get(name, 0) + 1
                advice[name] = how
                break
    return [{"class": k, "count": v, "advice": advice[k]}
            for k, v in sorted(tally.items(), key=lambda kv: -kv[1])]


def run(corpus_roots: list[str] | None = None, min_sessions: int = 5,
        since_days: float = 30.0, state_path: str | None = None) -> dict:
    """Mine, reflect, and write the candidates down. Returns what it wrote."""
    from . import memory
    from .harvest import mine, transcripts, undistilled

    roots = corpus_roots or [os.path.join(os.path.expanduser("~"),
                                          ".claude", "projects")]
    paths: list[str] = []
    for root in roots:
        if os.path.isdir(root):
            paths += transcripts(root, since_days=since_days)

    conn = memory.open_memory()
    got = mine(paths, min_sessions=min_sessions) if paths else {
        "scanned": 0, "recurring_errors": [], "corrections": []}

    # Quieter every run: anything memory already answers is not a candidate.
    # A generic prefix is not a finding. "Traceback (most recent call last):"
    # topped the list at 307 sessions and names no failure -- the exception
    # line beneath it is the fact. Entries that carry no identifying content
    # are noise with a big number attached, which is worse than silence
    # because the number makes them look important.
    got["recurring_errors"] = _informative(got.get("recurring_errors", []))
    known = {r["example"] for r in got.get("recurring_errors", [])}
    fresh = set(undistilled(conn, sorted(known))) if known else set()
    recurring = [r for r in got.get("recurring_errors", [])
                 if r["example"] in fresh][:MAX_RECURRING]

    try:
        clusters = memory.reflect(conn)[:MAX_CLUSTERS]
    except Exception:
        clusters = []

    kinds: dict[str, int] = {}
    for row in conn.execute("SELECT id FROM nodes WHERE type = 'memory'"):
        k = memory.mem_meta(conn, row[0]).get("kind", "world")
        kinds[k] = kinds.get(k, 0) + 1

    payload = {
        "ran_at": time.time(),
        "scanned": got.get("scanned", 0),
        "recurring": [{"sessions": r["sessions"], "example": r["example"][:180]}
                      for r in recurring],
        "corrections": [c["text"][:180]
                        for c in got.get("corrections", [])][-MAX_CORRECTIONS:],
        "correction_classes": _classify(
            [c["text"] for c in got.get("corrections", [])]),
        "unconcluded": [{"about": c.get("about", [])[:4],
                         "members": c.get("members", [])[:4]}
                        for c in clusters],
        "kinds": kinds,
        "window_days": since_days,
    }
    path = state_path or STATE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, path)
    return payload


def load(state_path: str | None = None) -> dict:
    """What the last scheduled run found, with its age. {} if never run."""
    path = state_path or STATE
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return {}
    d["age_hours"] = (time.time() - d.get("ran_at", 0)) / 3600.0
    return d


def digest(state_path: str | None = None, max_lines: int = 8) -> str:
    """A few lines for session start. Empty when there is nothing to say.

    Deliberately reads a file and computes nothing: a brief that scans 1,500
    transcripts would be switched off within a week, and a switched-off brief
    is the manual process again wearing a schedule.
    """
    d = load(state_path)
    if not d:
        return ("loopgraph distill: has never run. "
                "`loopgraph distill --run` or install the schedule.")
    age = d.get("age_hours", 1e9)
    lines: list[str] = []
    # Say it is stale BEFORE saying what it found, or the findings read as
    # current. A dead schedule presenting month-old candidates as today's is
    # exactly the failure this file was written against.
    if age > 48:
        lines.append(f"loopgraph distill: last ran {age/24:.0f}d ago -- these "
                     f"candidates are stale and the schedule may be dead.")
    recurring = d.get("recurring") or []
    unconcluded = d.get("unconcluded") or []
    classes = d.get("correction_classes") or []
    if not recurring and not unconcluded and not classes and age <= 48:
        return ""
    # The most valuable thing this job collects, and the easiest to leave on
    # the floor: it was gathered every night and shown to nobody until someone
    # asked what else was being missed. A ranked failure mode is the only
    # output here that describes the agent rather than the estate.
    if classes:
        top = classes[0]
        lines.append(f"corrections in the last {int(d.get('window_days', 30))}d: "
                     f"{sum(c['count'] for c in classes)}. Most common: "
                     f"{top['class']} ({top['count']}) -- {top['advice']}.")
    if recurring:
        lines.append(f"recurring across sessions, not yet in memory "
                     f"({len(recurring)}):")
        for r in recurring[:max(1, (max_lines - 2) // 2)]:
            lines.append(f"  [{r['sessions']:>3} sessions] {r['example'][:96]}")
    # The unconcluded-cluster count is deliberately NOT repeated here: the
    # janitor already reports it, and the same fact arriving twice in one
    # brief with two slightly different numbers is how a reader learns to
    # skim the whole thing.
    return "\n".join(lines)
