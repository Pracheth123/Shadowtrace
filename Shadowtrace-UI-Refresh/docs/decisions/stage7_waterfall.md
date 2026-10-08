# Stage 7 — Speculative drafts and waterfall

**Date:** 2026-10-03  
**Method:** offline recording via `tools/record_stage7_session.py`  
(mock agent + `FakeSpeakPort`). Not a live mic or a live vendor model.

The agent loop runs on `likely_next`, while the partial is still arriving. `question_planned` and `draft_ready` are emitted only after endpoint, so the waterfall stays non-negative. A stale final refreshes with one local phrase. It does not start a second tool loop and it does not sleep to imitate a vendor round trip.

## Session

| Log | Drafts |
|---|---|
| `fixtures/sessions/stage7_speculative/session.jsonl` | valid 4, stale 1, stale-draft rate 0.20 |

Re-record:

```bash
python tools/record_stage7_session.py
python tools/waterfall.py fixtures/sessions/stage7_speculative/session.jsonl
```

## Waterfall (paste)

```
Turn turn-valid-0
  last partial  -> endpoint        (endpoint detection):      0.6 ms
  endpoint      -> question_planned (planning):               0.2 ms
  endpoint      -> draft_ready      (TTFT):                   0.2 ms
  draft_ready   -> first tts_chunk  (TTS first chunk):        0.0 ms
  TOTAL (endpoint -> first tts_chunk):        0.2 ms

Turn turn-valid-1   TOTAL 0.4 ms
Turn turn-valid-2   TOTAL 0.2 ms
Turn turn-valid-3   TOTAL 0.2 ms
Turn turn-stale     TOTAL 0.2 ms

Session summary (5 measured turns):
  p50 total (endpoint -> first tts_chunk):     0.2 ms
  p95 total (endpoint -> first tts_chunk):     0.2 ms
```

Valid-draft p50 is 0.2 ms. The stale turn is 0.2 ms as well, because the refresh is a local phrase, not a model call. The stage target of ~450 ms valid and ~1.0 s stale is the live-vendor budget. This recording shows the mock path only: the draft is already built before endpoint, so endpoint-to-audio does not wait on tools.

The live-vendor number was not measured. There is no `.env` file and `GROQ_API_KEY` is unset, so `tools/bench_ttft.py` cannot call Groq. No sleep was added to imitate that round trip.

## Other checks

- Signal extraction stays under 50 ms per partial. `scorer_transcript` returns `{text}` only.
- Crowd-noise frames (RMS under the energy threshold) produce zero barge-in confidence crossings. A loud burst does cross 0.80.
- A defended claim hit logs `note_claim_status` as a tool result. The claim id is not a scorer field.
