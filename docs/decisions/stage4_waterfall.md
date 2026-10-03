# Stage 4 — Recorded sessions and waterfall

**Date:** 2026-10-03  
**Method:** offline recording via `tools/record_stage4_session.py`  
(FakeStt + mock LLM + FakeTts). Not live mic / live vendor APIs.

## Sessions (5 candidate turns each)

| Session | Log | Transcript fixture |
|---|---|---|
| A | `fixtures/sessions/stage4_sess_a/session.jsonl` | `fixtures/transcripts/stage4_session_a.json` |
| B | `fixtures/sessions/stage4_sess_b/session.jsonl` | `fixtures/transcripts/stage4_session_b.json` |
| C | `fixtures/sessions/stage4_sess_c/session.jsonl` | `fixtures/transcripts/stage4_session_c.json` |

Re-record:

```bash
python tools/record_stage4_session.py --all
```

## Waterfall — session A (paste)

```
Turn … 
  last partial  -> endpoint        (endpoint detection):      0.0 ms
  endpoint      -> question_planned (planning):               n/a
  endpoint      -> draft_ready      (TTFT):                  55.2 ms
  draft_ready   -> first tts_chunk  (TTS first chunk):      168.8 ms
  first tts_chunk -> first playback_ack (net+play):           0.3 ms
  TOTAL (endpoint -> first tts_chunk):      224.1 ms
… (5 measured turns)
Session summary: p50 439.7 ms  p95 452.1 ms
```

Full output: `python tools/waterfall.py fixtures/sessions/stage4_sess_a/session.jsonl`

## p50 / p95 across three sessions

| Session | Measured turns | p50 | p95 |
|---|---|---|---|
| stage4_sess_a | 5 | 439.7 ms | 452.1 ms |
| stage4_sess_b | 5 | 442.8 ms | 454.0 ms |
| stage4_sess_c | 5 | 435.2 ms | 449.2 ms |
| **All 15 turns** | 15 | **442.8 ms** | **454.0 ms** |

## Notes

- Brief expected ~1.1–1.5 s for **live** vendors. These numbers are the **mock baseline**
  (no network STT/LLM/TTS). Stage 7 speculation targets ~450 ms with a valid draft on live path.
- `question_planned` is n/a until stage 5 guard lands.
- Server composes FakeStt when `FAKE_STT_PATH` is set; browser lane still uses `candidate_final`.
