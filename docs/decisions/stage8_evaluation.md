# Stage 8 — Evaluation pass

**Date:** 2026-10-03  
**Method:** `tools/evaluate.py` on the three stage-4 session transcripts and two hand-written transcripts. No model call. The eval trace is a separate JSONL file. It is not written to the live session bus.

The transcript PDF is a local text PDF linked from the HTML report. Every wrapped line is on a page. WeasyPrint is not installed. It needs Pango and Cairo, and a new dependency waits for an explicit yes.

A claim is held only when at least half of its content words appear in the answer. One shared word, such as “rollback” in “auto rollback on errors”, stays untested.

## Reports

| Input | Elapsed | Findings | Claims |
|---|---|---|---|
| `fixtures/evaluation/stage4_sess_a` | 0.0015 s | 8 | c-kafka held, c-rollback untested |
| `fixtures/evaluation/stage4_sess_b` | 0.0015 s | 7 | c-kafka untested, c-rollback untested |
| `fixtures/evaluation/stage4_sess_c` | 0.0015 s | 7 | c-kafka untested, c-rollback untested |
| `fixtures/evaluation/stage8_collapse` | 0.0008 s | 6 | c-kafka collapsed, c-rollback held |
| `fixtures/evaluation/stage8_hold` | 0.0007 s | 5 | c-kafka held, c-rollback untested |

Every finding quote is taken from a transcript turn the agent looked up. The collapsing line is “I didn't build the Kafka pipeline…”. Dimension scores are the share of support findings in that dimension. Fit, keyword hits, and claim-match counts are not fields on the report.

Re-run:

```bash
python tools/evaluate.py \
  --transcript fixtures/sessions/stage4_sess_a/transcript.json \
  --log fixtures/sessions/stage4_sess_a/session.jsonl \
  --claims fixtures/claims/stage5_claims.json \
  --out fixtures/evaluation/stage4_sess_a
```
