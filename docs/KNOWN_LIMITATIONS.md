# Known limitations (stage 16)

## What the feedback is and is not

- **Experimental coaching indicators.** Levels and scores have not been
  validated against human reviewers. The evaluation set in `evaluation_set/`
  has **no reviewer labels yet**; no agreement figure exists.
- **"Found in your answer" ≠ correct.** `source_match` only checks that a quote
  appears in the named answer of that round. The interpretation can still be
  wrong — which is why every finding can be disputed.
- **Voice quotes are transcripts.** Speech-to-text errors change what is quoted.
  The evaluation set includes transcription-error cases; their effect on
  feedback has not been measured.
- **Repository and work samples** show that material exists, not who wrote it.
  An unclear answer does not establish dishonesty. There is no lie detection.
- **Keyword overlap** with a job description is document overlap only.
- **Revised assessments** are automated re-checks by the same kind of model,
  with the candidate's correction as context. They are not independent review.
- **One improved practice answer** is not general readiness. Only one dimension
  is compared; overall scores are never subtracted.

## Validation status by profession

- Software and sales are exercised by automated tests through the full API
  (intake → interview → evaluation → report → dispute/practice) and by the live
  latency benchmark. Hardware, marketing, operations, finance and the generic
  pack load and run through the same code but have **no dedicated end-to-end
  tests or live runs**; do not claim equal validation for them.

## Latency

- Measured once, on one machine, n = 6 sessions (see
  `docs/decisions/stage16_upgrade.md`). Median first validated feedback was
  about the 15 s goal (15.5 s), median complete report met the 45 s goal, and
  the p95 figure is effectively the maximum of six runs. Not a guarantee.
- The binding constraint was the **provider's own rate limit** (HTTP 429 with
  `Retry-After` up to 16 s) when three rounds are evaluated concurrently on one
  key, not the client limiter. A higher provider tier would help; raising
  `GROQ_REQUESTS_PER_MINUTE` would not.
- Under the same provider rate limit, live interviewer turns can exceed
  `LIVE_MODEL_DEADLINE_S` (10 s). In the stage-16 live run, 3 turns of one
  session fell back to the plan-based proposer this way. The candidate is told
  each time and the turns are listed in `degraded_turns`; questions stay
  guard-checked, but they are less personalised.
- The live interview's ~450 ms speculative-turn target remains unmeasured
  against real vendors (unchanged from stages 7 and 11).

## Not verified in this release

See `docs/decisions/stage16_upgrade.md` → "What remains unverified". In short:
in-browser microphone capture, playback, barge-in/interruption, typing recovery
after a voice failure, report download, dispute, practice retry, reload and
deletion **in a real browser** were not exercised by a human in this run; they
are covered by API-level tests and a type-checked, built client.

## Product scope (deliberate)

- Guest identity only: a bearer key in one browser, no recovery, no
  cross-device access.
- No employer product, ranking, screening, or hiring recommendation.
- No emotion, lie, accent, facial or gaze analysis. Video is a local preview
  only and never reaches the server.
- No autonomous company research; only context the candidate types.
- No code execution of any kind (no E2B); repositories are read, never run.
- One voice speaks at a time; no simultaneous panel audio. Deepgram uses one
  configured voice per session.
- Deletion removes everything this app stores. It cannot delete copies held by
  Groq or Deepgram under their own retention policies.

## Engineering

- `tests/test_stage2.py` has two pre-existing wall-clock timing flakes
  (`test_normal_answer_endpoints_once`, `test_single_word_endpoints`), seen in
  the clean baseline before any stage-16 change. They fail roughly one run in
  three on this machine.
- The legacy stage-8 pipeline remains for fixture replay and is not used by the
  app.
- The retention sweep runs inside the web process; with several workers each
  would sweep (harmless but redundant).
