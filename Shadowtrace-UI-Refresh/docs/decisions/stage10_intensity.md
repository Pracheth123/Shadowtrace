# Stage 10 — Intensity and the confidence guardrail

**Date:** 2026-10-03

`coach`, `realistic`, and `panel` are one voice. `panel` only raises the probe-depth ceiling (the pack's own cap still applies). A second interviewer is stage 11 and is not built here.

The guard reads intensity through `depth_cap`. Coach adds one hint after a spine question. Realistic and panel do not. Coach does not interrupt; realistic and panel may follow up. The hint is only on the spoken line. The `ask_spine` tool result stays the pack sentence.

Distress on a final steps the session down one level and emits `intensity_change`. The floor is still the spine and claim testing. After two finals with no distress trigger, it steps back one level. Each move happens at most once. If the session is already at `coach`, a further drop does not emit an event.

Every line passed to the speaker goes through the tone guardrail. A planted rude line is replaced with "Let's stay with the question." and an `agent_step` records the rewrite.

The evaluation report adds `intensity_note`. `score_findings` does not read that field. When the log has no `intensity_change`, the note says intensity was not recorded and scores are not adjusted.

## Distress replay

`tests/test_stage10.py` replays a realistic `behavioral-core` session. The first answer is calm. The second is filled with hedges and fillers. Later answers are steady. The log has one down (`realistic` → `coach`) and one up (`coach` → `realistic`). All four spine sentences are still asked through `ask_spine`.

## Coach and realistic

The same first answer under `coach` produces a transcript that contains the first spine sentence and "If you want a hint". The realistic transcript contains the spine sentence and does not contain the hint.
