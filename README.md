# Shadow Trace

Voice or text mock interviews built from **your own** background. Three
interviewer roles — HR, hiring manager, domain specialist — run one after
another and follow up on what you have actually done. Feedback quotes your
answers, says what it cannot tell you, can be disputed, and lets you practise
one specific gap and compare attempts.

Scores are **experimental coaching indicators**. They are not hiring
predictions, not judgements of truth or honesty, and have not been validated
against human review. Nothing here screens, ranks or gates anyone.

- Design system (tokens, fonts, page compositions, motion): [`docs/DESIGN_SYSTEM.md`](docs/DESIGN_SYSTEM.md)
- UI refresh, browser test evidence and local walkthrough: [`docs/UI_REFRESH.md`](docs/UI_REFRESH.md)
- Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- API and report schema: [`docs/API.md`](docs/API.md)
- Known limitations: [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md)
- Upgrading from stage 15: [`docs/MIGRATION.md`](docs/MIGRATION.md)
- Evidence for this release: [`docs/decisions/stage16_upgrade.md`](docs/decisions/stage16_upgrade.md)
- Hackathon provenance and judging map: [`docs/hackathon/`](docs/hackathon/)
- Licences and attribution: [`docs/LICENSES.md`](docs/LICENSES.md)

## Requirements

- Python **3.11, 3.12 or 3.13** (64-bit). 3.14 is not yet supported by the pinned
  dependency range.
- Node.js 18+ and npm.
- Optional, for real AI and voice: a Groq API key and a Deepgram API key.
  Without them, development mode runs a clearly labelled plan-based interviewer
  and a placeholder evaluator that is marked "not an assessment".

## Windows (PowerShell) — install and run

Run these from the repository folder, e.g. `C:\PRACHETH_FILES\Shadowtrace`.

```powershell
# 1. Create a virtual environment with a specific Python (here 3.12) and use
#    that same interpreter for every install. Mixing interpreters is what broke
#    the old .venv ("No module named pydantic_core._pydantic_core").
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activation is blocked:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

python -m pip install --upgrade pip
python -m pip install -e ".[test]"

# 2. Configuration
Copy-Item .env.example .env
notepad .env        # add GROQ_API_KEY / DEEPGRAM_API_KEY, or leave blank for dev mocks

# 3. Backend (terminal 1)
python -m uvicorn interview.server:app --host 127.0.0.1 --port 8000
```

```powershell
# 4. Frontend (terminal 2)
cd client
npm ci
npm run dev
# 5. Open the app
Start-Process "http://localhost:5173"
```

Check the backend: `Invoke-RestMethod http://127.0.0.1:8000/health`.
Check the provider keys with an authenticated, non-generating request:
`python tools\check_providers.py` (or `POST /api/diagnostics/verify`).

If an old `.venv` reports `No module named 'pydantic_core._pydantic_core'`,
it was filled by a different Python version. Remove it and repeat step 1:
`Remove-Item -Recurse -Force .venv`.

A pinned snapshot of a known-good install is in `requirements-lock.txt`
(`python -m pip install -r requirements-lock.txt` then `pip install -e . --no-deps`).

## Configuration

One validated settings object (`src/interview/config.py`). **Precedence:
process environment > `.env` > built-in default.** `config/inference.yaml`
holds only generation defaults and the TTS vendor; it no longer overrides any
environment setting. Every variable is listed with its meaning in
[`.env.example`](.env.example). The ones people change most:

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `dev` | `prod` refuses mocks, sessions without intake, and voice without Deepgram |
| `ALLOW_MOCK_PROVIDERS` | `1` | Mock STT/LLM/TTS allowed (dev/test only; startup error with `prod`) |
| `MOCK_LLM` | unset | `1` forces the plan-based interviewer + placeholder evaluator; needs mocks allowed |
| `GROQ_API_KEY`, `DEEPGRAM_API_KEY` | empty | Real interviewer/evaluator and real voice |
| `GROQ_MAX_RETRIES` | `3` | **Retries** after the first attempt (attempts = retries + 1). SDK retries are off |
| `GROQ_TIMEOUT_S` / `GROQ_EVAL_TIMEOUT_S` | `12` / `60` | Per-request timeout, live / evaluation |
| `LIVE_MODEL_DEADLINE_S` | `10` | Whole budget for one interviewer decision, retries and fallback included |
| `GROQ_REQUESTS_PER_MINUTE` | `30` | Client-side cap; keep at or below your account's limit |
| `MAX_MODEL_CALLS_PER_TURN` | `3` | Hard cap on model calls per live turn |
| `EVAL_MAX_REPAIRS` | `1` | Re-asks after invalid/truncated evaluator JSON |
| `EVAL_ROUND_DEADLINE_S` / `EVAL_JOB_DEADLINE_S` | `150` / `240` | Evaluation time budgets |
| `GUEST_RETENTION_DAYS` | `30` | Inactive guest data is deleted after this many days (`0` = never) |

## Verify

```powershell
python -m pytest -q                               # offline suite; no network, no keys
cd client; npx tsc --noEmit; npm run build; cd .. # client typecheck + production build
python tools\check_providers.py                   # authenticated provider check (not billed as generation)
python tools\bench_feedback.py --sessions 6 --live   # BILLABLE: feedback latency on the real evaluator
python tools\run_eval_set.py --live --out logs\eval_set\live   # BILLABLE: evaluation set
python tools\live_journey_smoke.py --out logs\live_journey     # BILLABLE: intake→interview→report
```

The test suite blanks provider keys from `.env` (`tests/conftest.py`) and never
calls a provider.

## A genuine demonstration (about 10 minutes)

Use real keys (`/health` shows `interviewer: groq`, `evaluator: groq`).

1. **Home and intake.** Open `http://localhost:5173` and click **Prepare my interview**. Paste a short background or upload a resume, pick *Software* or
   *Sales*, round *Full interview* (or one round to be quick), *Type* or *Speak*.
   Read and tick the consent box — it names Groq and Deepgram, the guest-key
   limits and the retention period.
2. **Source review.** Each extracted statement is shown inside its source text.
   Correct one statement's meaning and exclude another; pick a practice focus.
   Point out that the correction is labelled as yours, not a quotation.
3. **Role-specific interview.** The recruiter, hiring manager and specialist
   take turns with spoken handovers; show the round stepper, the visible
   transcript and the keyboard controls (Esc interrupts, T switches to typing).
4. **Personalised follow-up.** A follow-up quotes something you just said or a
   statement from step 2.
5. **Cited feedback.** End the interview. Each round's state is shown
   (queued → evaluating → complete) and a finished round can be read before the
   others; the overall report appears when all are done. "What to practise
   first" shows three items, each with the question, your quoted answer ("found
   in your answer" — which is not the same as the judgement being right), the
   reasoning, its limits and a next step.
6. **Dispute or practise.** Either click *This feedback seems wrong*, add a
   correction and ask for a labelled re-check (the original report is kept and
   the finding leaves your progress comparisons), **or** click *Practise this
   gap*, open the checklist (built only from your own words) and run a 3-minute
   attempt.
7. **Before and after.** The practice page shows the before and after level on
   that one dimension, both quotes, and one of: clearer explanation, no clear
   improvement, insufficient evidence, or comparison unavailable — plus an
   optional unaided variation to check transfer.

Downloads (transcript, scorecard), History, Next practice and *Delete my data*
all work from the Results page.

## What is deliberately not here

Employer screening or ranking, emotion or lie detection, facial or gaze
analysis, autonomous company research, running candidate code, simultaneous
panel voices. See [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md).
