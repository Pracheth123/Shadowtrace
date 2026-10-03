# Stage 10 — Intensity toggle and confidence guardrail

Read `CLAUDE.md` first. Stage 7 must be passing.

**Goal:** candidate picks hardness; interviewer eases off on distress without becoming a pushover.

## Build

1. Intensity config: `coach` / `realistic` / `panel` (panel reserved for stage 11).
2. **Guard + agent read intensity** — probe depth, hints, interruption from config.
3. Confidence guardrail: distress → one-level down (`intensity_change`); floor keeps spine + claim
   testing. Recover after 2 steady turns, at most once per session.
4. Tone guardrail on every speak output.
5. Report notes intensity neutrally; scores not adjusted for it.

## Tests / exit

Replayed distress session: one de-escalation, all spine still asked. Tone guardrail catches planted
rude line. Listeners can tell coach from realistic from transcripts alone.
