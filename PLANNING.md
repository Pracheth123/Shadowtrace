# Planning

Architecture, contracts, stack, and stage order live in
[`claude files/CLAUDE.md`](claude%20files/CLAUDE.md). That file wins on conflict
with a stage brief.

Active work is tracked in [`TASK.md`](TASK.md). Stage briefs are in
[`claude files/`](claude%20files/).

## Current focus

Stage 7 exit path is in place on the mock stack (signals, turn controller, speculative draft, router). Recorded p50 is 0.2 ms because the draft is ready before endpoint; stale-draft rate on that session is 0.20. The ~450 ms / ~1.0 s figures are the live-vendor budget and are not measured here.  
Next: Stage 8 — evaluation agents. Do not start them until a new stage brief is executed.
