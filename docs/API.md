# API and report schema (stage 16)

All `/api` routes except `/api/guest` and `/api/diagnostics*` need
`Authorization: Bearer <guest token>`. The candidate is always resolved from the
token; ids in a URL are looked up **inside that candidate's own directory**, so
another candidate's id answers **404** (never 403), and a missing/invalid token
answers **401**. Request bodies are JSON unless noted.

## Identity and diagnostics

| Method | Path | Notes |
|---|---|---|
| POST | `/api/guest` | New guest; returns the token once. Limits stated in the response. |
| GET | `/api/me` | Who the token belongs to. |
| GET | `/health` | Modes (`interviewer`, `evaluator`, `voice`), last `verified` result, upload rules, `retention_days`, `practice_minutes`. |
| GET | `/api/diagnostics` | Effective settings (`public_dict`), configured vs verified per provider. No secret, ever. |
| POST | `/api/diagnostics/verify` | Authenticated, non-generating checks (Groq `GET /models` incl. per-model availability; Deepgram `GET /v1/projects`). Cached 10 min, rate-limited. |

## Intake

| Method | Path | Notes |
|---|---|---|
| POST | `/api/intake` (multipart) | Fields as before **plus `consent=1` (required; 422 `field: consent` otherwise)**. Writes `consent.json`. |
| GET | `/api/intake/{id}` | When ready: `claims[]` each with `source_span {found,start,end,before,match,after}` and `review {action,text?}`; `review`; `objectives` (id → label); `consent`; `fit_gap` (document overlap); `prep`. |
| PUT | `/api/intake/{id}/review` | `{statements:[{id, action:"keep"|"edit"|"exclude", text?}], objective:{competency, note?}|null}`. 422 on unknown id, edit < 10 or > 300 chars, unknown focus. `claims.json` is never modified. |

## Sessions and evaluation

| Method | Path | Notes |
|---|---|---|
| WS | `/ws/session` | `session_start {auth_token, intake_id | practice_id, lane, consent?}`. `turn_end` now carries `text` (the recorded answer). `session_ready` carries `practice` and `objective`. |
| GET | `/api/sessions/{id}` | Lifecycle (`state`), plus `kind` (`interview`/`practice`), `practice`, `interviewer`, `degraded_turns[]`, `objective`. |
| GET | `/api/sessions/{id}/evaluation` | **Per-round job view.** Read-only; never starts work. See below. |
| POST | `/api/sessions/{id}/evaluation/retry` | Re-runs **failed rounds only** (optional `{rounds:[...]}`). 409 when a round already used `EVAL_MAX_ROUND_RUNS`. |
| GET | `/api/sessions/{id}/report` | 409 until complete. The stored report **unchanged**, plus `disputes`, `revisions`, `contested` alongside it. |
| GET | `/api/sessions/{id}/scorecard`, `/transcript` | Downloads. |

### Evaluation job view

```json
{
  "state": "evaluating",                 // finalising | evaluation_queued | evaluating | complete | failed
  "transcript_available": true,
  "report_ready": false,                 // the overall report is incomplete until true
  "rounds_total": 3, "rounds_settled": 2,
  "elapsed_s": 17.4,                     // real seconds since the interview ended; no percentage
  "rounds": [
    {"round": "hr", "label": "Recruiter", "state": "complete", "elapsed_s": 5.6,
     "queue_delay_s": 0.0, "replies": 1, "repairs": 0, "provider_attempts": 1,
     "limiter_wait_s": 0.0, "request_s": 4.4, "model_used": "openai/gpt-oss-120b",
     "fallback_used": false, "usage": {"total_tokens": 2838}, "cache_hit": false,
     "result": { /* RoundResult, readable now */ }},
    {"round": "domain_specialist", "state": "failed", "category": "rate_limited",
     "error": "...", "recovery": "The model provider is rate-limiting requests. Retry this round in a minute."}
  ]
}
```

Round states: `queued`, `running`, `complete`, `failed`, `not_assessed` (no
answers or too little to assess — no model call). Failure categories:
`rate_limited`, `timeout`, `deadline`, `provider_unavailable`,
`model_unavailable`, `auth`, `invalid_output`, `truncated`, `budget_exceeded`,
`interrupted` (server restarted mid-round), `unknown` — each with a recovery
line.

## Disputes and revised assessments

| Method | Path | Notes |
|---|---|---|
| POST | `/api/sessions/{id}/findings/{fid}/dispute` | `{explanation?}` (≤ 1500 chars, sanitised). Idempotent. |
| POST | `/api/sessions/{id}/findings/{fid}/dispute/status` | `{status: "withdrawn" | "accepted_revision" | "open"}`. Accepting needs a completed revision. |
| POST | `/api/sessions/{id}/findings/{fid}/revision` | Labelled automated re-check of that round with the correction as delimited context. Needs an explanation ≥ 10 chars; at most 2 per finding. |

Stored as `<session>/disputes.json` and `<session>/revisions/<id>.json`. While a
dispute is `open`, `reassessed` or `accepted_revision`, the finding is excluded
from comparisons, recurring gaps, the plan and practice; a dimension whose
**only** support was that finding is excluded from comparisons (`contested.dimensions`).

## Targeted practice

| Method | Path | Notes |
|---|---|---|
| POST | `/api/practice` | `{session_id, finding_id, minutes: 3|5|8|10}` or `{parent_practice_id, minutes}` for an unaided variation. Anything else in the body is ignored; the record is derived from the stored report. 404 for another candidate's session; 409 for strengths, ineligible, disputed, incomplete or practice-derived findings. |
| GET | `/api/practice` · `/api/practice/{pid}` | Record + `comparisons[]` + `latest_outcome` + `offer_unaided_variation`. |
| POST | `/api/practice/{pid}/coaching` | Marks the checklist as shown → attempts are labelled coached. 409 for unaided variations. |

Comparison `outcome`: `clearer`, `no_clear_improvement`,
`insufficient_evidence`, `unavailable` (dispute, rubric change, different
evaluator provider), or `pending`. Each carries `before`, `after`, `reason`,
`limitations[]`.

## History, plan, deletion

| Method | Path | Notes |
|---|---|---|
| GET | `/api/history` | `sessions[]` (with `kind`, `open_disputes`), `comparisons[]` (interviews only; disputed-only dimensions left out with a note), `recurring_gaps[]` (disputed findings not counted), `practice[]`. |
| GET | `/api/plan` | `items[]`, `practice[]`, `preparation[]`. |
| POST | `/api/me/delete` | Closes any live session without writing, cancels and awaits evaluation/revision/intake tasks, then removes everything. Manifest includes `practice_records_deleted`, `live_sessions_closed`, `background_tasks_cancelled`, `external_providers`. |

## Report schema `report.v3`

Additive over `report.v2`; older reports still render (the client applies
display adapters).

- `ReportFinding` adds `finding_id`, `source_match` (quote found, as written, in
  the named candidate turn of that round — **not** a validation of the
  judgement), `question`, `question_turn_id`, `limitation`, `priority`
  (1 = practise first), `eligible_for_practice`. `verified` is kept for v2
  compatibility and means the same as `source_match`.
- Quotes shorter than 12 characters never count as evidence.
- `RoundResult.status` adds `insufficient_evidence` (answers too short; no model
  call, no score). `RoundResult.evaluation_meta` records provider, requested and
  used model, fallback, replies/repairs, provider attempts, limiter wait,
  request time, tokens, prompt version.
- `ClaimFinding.status_label` and `SessionReport.claim_status_labels`:
  `held` → "Explained in this session", `collapsed` → "Needs clarification",
  `untested` → "Not explored". Stored values are unchanged.
- `SessionReport` adds `priority_findings` (≤ 3 ids), `session_kind`, `practice`.
- `evaluator` adds `fallback_model`, `prompt_version`.

## Storage layout additions

```
data/candidates/<cid>/
  intake/<iid>/consent.json, review.json            (claims.json unchanged)
  sessions/<sid>/evaluation/rounds/<round>.json     per-round validated result + cache key
  sessions/<sid>/disputes.json, revisions/<rev>.json
  practice/<pid>/practice.json
data/reports.sqlite: session_reports.session_kind, report_gaps.finding_id (auto-migrated)
```
