---
description: "loopgraph: agent coordination and loop gates"
argument-hint: "[on|off|status|claims|classes|artifact check <name>|fact|...]"
allowed-tools: ["Bash(loopgraph:*)"]
---

```!
loopgraph $ARGUMENTS
```

Above is the output of `loopgraph $ARGUMENTS` for the current repo (no
arguments means `status`).

Relay it concisely. How to read it:
- `gates: scope=ON loop=ON` — scope gates agent dispatch, loop gates turn end. Both off by default.
- `claim` exiting 3: dispatch refused, another agent holds part of that write-set, nothing was claimed.
- `validate` exiting 1: the agent's premises moved while it ran; quarantine its conclusions rather than merging.
- `artifact check` exiting 1: that name would duplicate an existing artifact or repeat a recorded refusal.
- `check` exiting 1 is normal when work remains; `status` always exits 0.
- Empty output means success with nothing to report.

Do not re-run the command unless asked.
