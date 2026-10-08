# Migrating from stage 15 to stage 16

Nothing destructive happens to existing data on upgrade. Read item 6 before
starting the server on a machine with old guest data.

1. **Reinstall dependencies** in a venv created by the same Python you install
   with (see README). New runtime dependency: `python-multipart` (it was always
   needed by the multipart intake endpoint but was never declared, so a clean
   install failed every intake). `pytest`/`pytest-asyncio` moved to the `test`
   extra: `pip install -e ".[test]"`.

2. **`.env`.** Existing files keep working. New optional variables are listed
   in `.env.example` (`MOCK_LLM`, `GROQ_EVAL_TIMEOUT_S`, `LIVE_MODEL_DEADLINE_S`,
   `EVAL_*`, `MAX_LIVE_SESSIONS`, `INTAKE_RPH`, `RESUME_TTL_S`,
   `GUEST_RETENTION_DAYS`, `RETENTION_SWEEP_INTERVAL_S`).
   - `MOCK_LLM=1` together with `ALLOW_MOCK_PROVIDERS=0` is now a **startup
     error** (it used to silently run mocks anyway).
   - With `ALLOW_MOCK_PROVIDERS=0` and no `GROQ_API_KEY`, sessions are now
     **refused** instead of running the plan-based interviewer.

3. **`config/inference.yaml`** no longer contains a `groq:` section. If you
   customised `requests_per_minute`, `max_retries_on_429`, `max_calls_per_turn`,
   `roles` or `failover_roles` there, move them to `GROQ_REQUESTS_PER_MINUTE`,
   `GROQ_MAX_RETRIES`, `MAX_MODEL_CALLS_PER_TURN`, `MODEL_*`,
   `MODEL_FALLBACK_*`. Those YAML values were read *instead of* the environment
   before, which is why changing `.env` appeared to do nothing.
   Semantics changed slightly: `GROQ_MAX_RETRIES` now counts retries for all
   retryable errors (429, 408, 409, 5xx, timeouts, connection errors), not only
   429, and the OpenAI SDK's own two retries are switched off.

4. **Report store.** `data/reports.sqlite` is migrated automatically on first
   open (adds `session_reports.session_kind` default `'interview'` and
   `report_gaps.finding_id` default `''`). Idempotent; no rows change.

5. **Reports.** New reports are `report.v3`. Existing `report.v2` files are not
   rewritten; the client shows them with the new labels via display adapters.
   v2 findings have no `finding_id`, so they cannot be disputed or practised —
   re-run evaluation for that session if you need those actions
   (`POST /api/sessions/{id}/evaluation/retry` only works on failed jobs; for a
   completed v2 session, run a new interview).

6. **Retention.** `GUEST_RETENTION_DAYS` defaults to **30**. On startup, and
   hourly, guests whose files have not changed for 30 days are **deleted**. To
   keep everything while you decide, set `GUEST_RETENTION_DAYS=0` before
   starting the server. The setup page discloses the period to candidates.

7. **Consent.** New intakes require `consent=1`. Intakes created before this
   stage have no `consent.json`; starting a session from one requires the
   client to send `consent: true` (the current client does after the candidate
   ticks the box on the setup or practice page), and the server then records it.

8. **Evaluation in flight during the upgrade.** A session that was
   `evaluating` when the old server stopped is reported as failed with
   category `interrupted`; retry it from the Results page.

9. **The checked-in `.venv` is broken** (a CPython 3.14 `pydantic_core` wheel in
   a 3.12 environment). It was not modified. Recreate it as in the README.
