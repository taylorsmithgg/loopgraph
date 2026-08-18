# Claude Code hooks

These live in `~/.claude/hooks/` and are loaded by every session on the
machine. They are copied here because that directory is not a git repository:
eight bespoke hooks, no history, no remote, and the only versioning was
`.bak-*` files sitting in the directory that would be lost with it.

| hook | event | what it does |
|---|---|---|
| `consequence.py` | Stop | refuses to end a turn that changed shared config until the effect is stated |
| `command_trap.py` | PreToolUse(Bash) | denies the one command whose failure is expensive and silent |
| `rubber_stamp.py` | UserPromptSubmit | a one-word approval means the last turn ended on a predictable gate |
| `broadcast.py` | UserPromptSubmit | corrected beliefs reach other live sessions on their next prompt |
| `trap_table_cost.py` | (guard) | replays history against the trap table; fails over 0.1% |

Copies, not the originals. `~/.claude/hooks/` is still authoritative until
that directory is itself under version control.
