# Stage 8 — The evaluation pass

Read `CLAUDE.md` first. Needs only stage 1 and recorded sessions (stage 4's logs, or hand-written
transcripts). **Runs entirely offline.** It never runs while a session is live and nothing in the
live path calls it (contract 6).

**Goal:** within ~30 s of the closer, turn a transcript + event log + Claims File into a feedback
report with quoted evidence.

## Build

1. **`src/interview/evaluation/inputs.py`** — loads `transcript.json`, the event log, `claims.json`,
   and the pack. The transcript already honours truncation. Repo/resume content enters only as
   delimited data (contract 7).
2. **Pattern detectors** (no LLM) — filler rate, answer length vs question type, hedge phrases,
   pause stats, "I/we" ratio on project answers, whether a spine answer addressed the question.
   Delivery measures are compared to the **candidate's own first-answer baseline** (contract 9).
3. **Three evaluator passes with different briefs**, run concurrently:
   - *Technical depth* — correctness and depth on the technical substance.
   - *Structure* — reasoning and clarity of thinking.
   - *Competency alignment* — against the pack's competencies.
   Each returns findings, every finding with a verbatim quote and its `turn_id`/timestamp.
4. **Claim adjudication** — each Claims File entry → `held | collapsed | untested`, each with a
   supporting quote that passes a **negation check** (the quote must not be "I did *not* build…").
5. **Merge** — deduplicate findings, drop any without a valid quote (quote must be found in the transcript).
6. **Scorer** — the four dimensions (technical substance, structure of thinking, delivery and
   articulation, competency alignment), weights from the pack. Inputs are findings only. **Keyword
   hits, claim-match counts and fit scores are not inputs** (contract 8); add a test that asserts the
   scorer's input type has no such fields.
7. **Report** — `report.json` + a rendered HTML page: scores per dimension, 3 strengths and 3 things
   to work on, each with a quoted moment and timestamp; claim statuses; the gap list.
8. **`src/interview/mocks/fake_eval.py`** — returns a canned `EvaluationRecord` (so the dashboard in
   stage 9 can be built without running models).
9. **`tools/evaluate.py`** — `python tools/evaluate.py --session dir/`.

## Fairness tests

- Same transcript with fillers removed vs present: technical score unchanged.
- A transcript that repeats every Claims File keyword but says nothing substantive scores no higher
  than one that doesn't.
- No input field carries audio features beyond pause/timing.

## Exit criterion

On the three stage-4 recordings plus two hand-written transcripts (one strong, one where a claim
collapses): report renders within 30 s from start of `evaluate.py` (paste timings), the collapsing
claim is marked `collapsed` with the right quote, and every finding's quote is found in its transcript.

## Before you write code

Reply with the `EvaluationRecord` schema, the three evaluator briefs (draft prompts), and the
scorer's exact input type. Wait for approval.
