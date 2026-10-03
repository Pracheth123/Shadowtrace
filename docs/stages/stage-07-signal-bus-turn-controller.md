# Stage 7 — Signals, turn controller and speculative agent loop

Read `CLAUDE.md` first. Stages 5 and 6 must be passing.

**Goal:** think while the candidate talks. Stable partials + `likely_next` drive the agent loop
during speech so endpoint→first audio hits ~450 ms with a valid draft (~1.0 s stale).

## Build

1. **`signals.py`** — claim hits, hedging vs own baseline, distress. Feeds agent/guard only
   (contract 8). Budget &lt;50 ms/partial.
2. **`turn_controller.py`** — `likely_next` before endpoint when answer looks complete;
   `floor_granted` at endpoint. Barge-in cancels in-flight agent steps (`agent_step.cancelled`).
3. **Speculative agent loop** — on `likely_next`, run tools + reason + draft first sentence
   (+ concession variant). Hard step limit. At endpoint: router (&lt;50 ms) + at most one short
   model call if draft stale.
4. **Router** — defended / conceded / unclear; emits `route_decision`.
5. Provisional claim status via `note_claim_status` → log only.
6. Planted-injection check: keywords change agent choices only, never scorer inputs.

## Exit criterion

Waterfall p50 ~450 ms valid draft / ~1.0 s stale from real sessions. Paste waterfalls + stale-draft
rate. False barge-ins near zero with crowd noise.
