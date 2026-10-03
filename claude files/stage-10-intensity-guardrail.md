# Stage 10 — Intensity toggle and confidence guardrail

Read `CLAUDE.md` first. Stage 7 must be passing.

**Goal:** the candidate picks how hard the interview is, and the interviewer eases off when someone
is clearly struggling — without ever turning into a pushover.

## Build

1. **Intensity settings** — chosen before the session; stored in the session header and
   `intensity_history`. Per level, in a config file:
   - `coach`: hints allowed after a stall, max probe depth 2, no interruptions, warm tone, a short
     "here's what a strong answer covers" after each spine answer.
   - `realistic`: no hints, depth up to 4, interrupts rambles after ~90 s, neutral tone.
   - `panel`: reserved for stage 11 (one voice until then; behaves like `realistic`).
2. **Planner + interviewer read intensity** — probe depth, hint policy, interruption policy come
   from the config, not from prompt wording alone.
3. **Confidence guardrail (de-escalation with a floor)** — when `distress.score` crosses a threshold
   for 2 consecutive turns: lower one level (emit `intensity_change`, `direction="down"`), offer a
   reset line, switch to an easier spine-adjacent probe. **The floor:** spine questions are still
   asked, and claims are still tested — de-escalation changes tone and depth, never coverage. Go back
   up after 2 steady turns, at most once per session.
4. **Tone guardrail on every output** — a fast check (rules first) that blocks sarcasm, belittling,
   or comments on accent/voice. Blocked lines are regenerated and the event logged.
5. **Report note** — the report mentions intensity changes neutrally and **scores are not adjusted
   for them**; the report shows which intensity each segment ran at.

## Tests

- Replay a distressed session: exactly one de-escalation, all spine questions still asked.
- Same transcript evaluated under different intensity labels produces identical scores.
- Tone guardrail catches a planted rude line.

## Exit criterion

Paste the `intensity_change` events and coverage from a replayed distress session, and the tone
guardrail test output. Two listeners can tell `coach` from `realistic` from transcripts alone.

## Before you write code

Reply with the intensity config, the distress thresholds, and the tone rule list. Wait for approval.
