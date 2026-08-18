# Scheduled distillation

`harvest` mines transcripts for what recurred across sessions; `reflect` finds
experience nobody generalised from. Both worked from the day they were written
and had been run seven times in the life of the corpus, because each was a
thing a person had to remember while busy. Nothing here is new capability —
this is the same capability with the human removed from the trigger.

    launchd (06:20 daily)
        └─ loopgraph distill --run      ~1,500 transcripts, seconds
             └─ ~/.loopgraph/distill.json
                  └─ session_brief reads the FILE at session start (~230 tokens)

Mining in the session's critical path would put seconds on every start, and a
brief that slows the session down gets removed — which returns the whole thing
to being manual while looking automated.

## Install

    sed -e "s|{{UV}}|$(command -v uv)|g" \
        -e "s|{{LOOPGRAPH}}|$PWD|g" \
        -e "s|{{HOME}}|$HOME|g" \
        schedule/com.loopgraph.distill.plist.template \
      > ~/Library/LaunchAgents/com.loopgraph.distill.plist
    launchctl load ~/Library/LaunchAgents/com.loopgraph.distill.plist
    launchctl start com.loopgraph.distill      # prove the SCHEDULER can run it

## Watch it fail

A dead schedule is the failure this replaces, so verify the alarm works
before trusting it:

    launchctl unload ~/Library/LaunchAgents/com.loopgraph.distill.plist
    loopgraph distill          # must say "last ran Nd ago ... may be dead"

The staleness warning is printed BEFORE the findings, deliberately: candidates
under a heading that does not mention their age are read as today's.
