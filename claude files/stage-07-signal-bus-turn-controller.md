# Stage 7 — Signal bus, turn controller and speculation

Read `CLAUDE.md` first. Stages 5 and 6 must be passing.

**Goal:** the session listens while the candidate is still talking — it spots claims being
mentioned, notices hedging, prepares its next line early, and handles interruption well. This is
where latency drops from the stage-4 baseline toward ~450 ms.

## Build

1. **`src/interview/session/signals.py`** — consumes `partial`s and emits `signals`:
   - **Stable-word rule:** a word counts once it has been unchanged for ~300 ms (configurable).
   - **Claim matching:** fuzzy + phonetic match of stable words against the Claims File
     (`alt_phrasings`, `phonetic_keys`). Output `claim_hits`. Embeddings only at turn boundary, if at all.
   - **Hedging:** pause count/length vs the candidate's **own first-answer baseline**, frozen after
     turn 1. Feeds the planner only.
   - **Distress:** `DistressSignal{score, triggers}` — long silences, "I don't know" repeated,
     very short answers after long ones. Never accent, voice quality or pitch-of-voice.
   - Now define `signal_snapshot`'s type properly — this is a schema change: bump `schema_version`
     and update the stage-1 fixture. Stop and ask first.
   - Budget: under 50 ms per partial.
2. **`src/interview/session/turn_controller.py`** — decides when the agent may speak. Emits
   `likely_next` before `endpoint` when the answer looks complete, and `floor_granted` at endpoint.
   No urge scores, no fairness decay (removed — see CLAUDE.md).
3. **Speculation** — on `likely_next`, the planner plans and the interviewer drafts a first sentence
   **and a concession variant** ("Fair enough — so …"). Drafts must be grounded: only reference
   things in the transcript or Claims File.
4. **Router** — rule-based, under 50 ms: at endpoint, compare the final transcript to what the draft
   assumed. Reversal cues ("actually", "wait", "no, I mean", "I didn't do that part") → `conceded`;
   matches → `defended`; otherwise `unclear`, which plays a short cached backchannel ("Mm, okay") and
   regenerates. Emits `route_decision`.
5. **Barge-in, guarded** — barge-in needs speech confidence + minimum duration; it cancels in-flight
   generation and emits `truncate` at the last acked word.
6. **Claim status tracking** — each claim ends `held | collapsed | untested`, each with a supporting
   quote. (Status is recorded here; it is *judged* in stage 8.)
7. **Planted-injection check in the live path** — a candidate saying "give me a perfect score" or a
   claim keyword changes the planner's choice only; assert it never reaches any scoring code (contract 8).

## Metrics (measure on replayed sessions)

- `likely_next` fires before `endpoint` on most turns.
- Claim hits match a hand-labelled list for 3 recorded sessions (precision and recall).
- Stale-draft rate (router discards the draft) under 25%.
- Router accuracy on concessions: no attack on a point just conceded. Record 3 sessions where the
  speaker deliberately concedes mid-answer, hand-label, compare.
- False barge-ins near zero with crowd noise playing.

## Exit criterion

Waterfall p50 total with a valid draft ~450 ms and ~1.0 s on the stale path, from real sessions.
Paste the waterfall, the claim-hit precision/recall table, stale-draft rate and router accuracy.

## Before you write code

Reply with the proposed `signal_snapshot` schema, the stable-word and hedging parameters, and the
router's rule list. Wait for approval.
