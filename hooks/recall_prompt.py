#!/usr/bin/env python3
"""UserPromptSubmit: surface what is already known before the work starts.

The corpus was never the problem -- 75 well-written memories -- but the index
was loaded wholesale every session and grew without bound, so recall was
"read all of it and hope". This ranks instead.

No model runs here. BM25 over FTS5 plus a recency prior takes milliseconds,
and a model call on every prompt would tax every session for a lookup that
ranking already does.

Silence is the default. Injecting three plausible-looking memories into a
prompt they have nothing to do with is worse than injecting none: it teaches
the reader to skim past the block, and then the one time it matters, it gets
skimmed too.
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

MIN_PROMPT_CHARS = 15
# Coverage, not BM25, decides. A BM25 floor looks principled and is really a
# function of corpus size -- tuned against 75 memories it silences a store
# with five, which is exactly when a new install would be judged and
# discarded. The share of the asked-for terms that actually appear means the
# same thing at any size.
MIN_COVERAGE = 0.6
MIN_TERMS = 2          # ...unless every term matched: one specific word is a query
TOP_K = 3


def _breadcrumb(conn, ev) -> None:
    """Record that this hook ran, and from where.

    A hook installed into a harness whose payload contract was inferred
    rather than tested is indistinguishable from no hook at all -- it just
    never fires and nobody finds out. `mem doctor` reads these.
    """
    try:
        from loopgraph.db import meta_set, utcnow
        harness = (os.environ.get("LOOPGRAPH_HARNESS")
                   or ("codex" if ev.get("codex_home") or os.environ.get("CODEX_HOME")
                       else "claude-code"))
        meta_set(conn, f"hook_seen:{harness}", utcnow())
    except Exception:
        pass


def main() -> int:
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return 0
    # Field name varies by harness: Claude Code sends `prompt`, and Codex's
    # hook payloads are the same shape but not guaranteed to be the same
    # spelling. Take whichever is present rather than silently reading "".
    prompt = ""
    for key in ("prompt", "user_prompt", "message", "text"):
        if isinstance(ev.get(key), str) and ev[key].strip():
            prompt = ev[key].strip()
            break
    if prompt.startswith("/"):
        return 0
    if os.environ.get("LOOPGRAPH_RECALL", "") == "0":
        return 0
    try:
        from loopgraph.memory import open_memory, recall
        conn = open_memory()
        _breadcrumb(conn, ev)
    except Exception:
        return 0                       # a memory miss must never cost a prompt
    if len(prompt) < MIN_PROMPT_CHARS:
        return 0
    try:
        found = recall(conn, prompt, k=TOP_K * 3)
        # The withheld marker carries no terms, so the coverage gate would
        # drop it -- leaving "nothing is known" and "known, but not here"
        # looking identical to the reader. Keep it aside.
        notice = next((h for h in found if h["id"] == "__withheld__"), None)
        hits = [h for h in found
                if h["id"] != "__withheld__"
                and h["coverage"] >= MIN_COVERAGE
                and (len(h["matched"]) >= MIN_TERMS or h["coverage"] == 1.0)][:TOP_K]
    except Exception:
        return 0                       # a memory miss must never cost a prompt
    if not hits and not notice:
        return 0
    if not hits:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": notice["text"]}}))
        return 0
    lines = ["Possibly relevant, from memory (ranked, not certain - verify "
             "before relying on any of it; `mem history <id>` for provenance):"]
    for h in hits:
        stale = (f" SUPERSEDED BY {h['superseded_by']} - prefer that one"
                 if h["superseded_by"] else "")
        first = h["text"].strip().splitlines()[0]
        lines.append(f"- [{h['id']}] ({h['kind']}, {h['created_at'][:10]}"
                     f"{stale})\n  {first[:400]}")
    if notice:
        lines.append(notice["text"])
    lines.append("If any of this turns out to be wrong, correct the record: "
                 "`mem retain \"<what is actually true>\" --supersedes <id>`.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "\n".join(lines)}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
