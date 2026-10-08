# Stage 16 — evidence (2026-10-06)

Environment for every run below: Windows 11 x64 developer machine, Python
3.12.8 in fresh venvs created for this stage, Node/Vite 5.4.21, residential
connection, Groq and Deepgram keys from the project's `.env` (values never
printed). Billable runs were made because credentials were available and the
brief asked for them.

## Baseline before any change

- Clean venv, `pip install -e .`: **25 errors** — `RuntimeError: Form data
  requires "python-multipart"` on every intake request. After installing it by
  hand: 258 passed, 1 skipped.
- The checked-in `.venv` cannot import pydantic: a `cp314` `pydantic_core`
  wheel inside a 3.12 environment (installed with the wrong interpreter's pip).
  Left untouched.
- `tests/test_stage2.py` timing tests already flaky (about 1 run in 3).

## Offline verification after the change

| Check | Result |
|---|---|
| Fresh venv from `pyproject.toml` only (`pip install -e ".[test]"`) | installs, incl. `python-multipart 0.0.32` |
| `python -m pytest -q` (run twice) | **297 passed, 1 skipped**; the other run 296 passed + the pre-existing `test_stage2::test_single_word_endpoints` flake |
| New tests | `test_stage16_settings.py` (13), `test_stage16_eval_jobs.py` (8), `test_stage16_journey.py` (17) |
| `npx tsc --noEmit` | clean |
| `npm run build` | succeeds (index 270.6 kB / 83.3 kB gzip) |

What the new tests cover: effective settings precedence; YAML no longer
overriding env; MOCK_LLM/prod refusals; SDK retries off and timeout set;
retries bounded then one fallback hop; no fallback on auth; deadline stops
retries; real model/token metadata; per-turn budget; live proposer falls back
inside its deadline; partial round results while another runs; failed-round-only
retry; no repeated provider calls on refresh/re-enqueue; cache reuse; no cache
across candidates; restart → `interrupted`; too-short rounds make no model
call; deletion during evaluation recreates nothing; report v3 provenance;
wrong-turn / interviewer / too-short quotes rejected; unusable findings dropped
without discarding valid levels; dispute stored beside the unchanged report and
excluded from plan, practice and comparisons; labelled revision; practice
ownership, server-derived record and checklist facts; practice through the live
runtime with before/after; comparison unavailable after dispute or evaluator
change; unaided variation; source review edit/exclude feeding the interview;
review validation; consent required; injected instructions in uploads and
answers never reaching a prompt as instructions; identical answer content via
text and voice paths; retention sweep; diagnostics hide secrets and separate
configured from verified. Software and sales round behaviour remains covered
by `test_stage15_journey.py` (a, b, c).

## Live provider checks

`python tools/check_providers.py` — authenticated, non-generating:

- Groq `GET /models`: 200; `openai/gpt-oss-20b`, `openai/gpt-oss-120b`,
  `qwen/qwen3.8-27b` all available to this key. (First attempt returned 403:
  Groq's edge rejects urllib's default User-Agent. The check now sends its own;
  the key itself was fine.)
- Deepgram `GET /v1/projects`: 200.

No model ids or protocol parameters were changed in this stage.

## Feedback latency benchmark

`python tools/bench_feedback.py --sessions 6 --live` — the real
`EvaluationService`, three rounds per session evaluated concurrently by
`openai/gpt-oss-120b`, one session at a time. Transcripts: 12 turns
(6 answers), 1 172–2 067 characters, alternating software/sales, built from the
evaluation set. Timed from queueing (which follows transcript finalisation by
milliseconds).

| Metric (n = 6, 0 failures) | Median | p95 (nearest rank = max at n = 6) | Min | Goal |
|---|---|---|---|---|
| First validated round result | **15.5 s** | 20.2 s | 5.6 s | median ≤ 15 s — **just missed** |
| Complete report | **38.8 s** | 76.9 s | 33.3 s | median ≤ 45 s met; p95 ≤ 90 s met |

Where the time went: provider request time per round was **2–9 s**; the
client-side limiter waited **0 s**; the rest was the provider's own HTTP 429
responses with `Retry-After` up to 16 s (2–7 provider attempts per round), i.e.
this account's provider-side rate limit when three large evaluator requests
are sent at once. 3 of 18 rounds needed one JSON repair. Not done, by
instruction: raising `GROQ_REQUESTS_PER_MINUTE` (it would not help — the client
was not the limiter), cutting output budgets, or switching models. Options for
the team: a higher provider tier, or staggering rounds (trades first-result
time for fewer 429s) — to be benchmarked before adopting.

The same run's per-round detail (attempts, waits, tokens) is in
`logs/bench_feedback/bench_live_*.json` (git-ignored).

## Live end-to-end journey

`python tools/live_journey_smoke.py --out logs/live_journey_stage16` (real
Groq interviewer and evaluator, text lane, software specialist round):

- intake ready, 3 statements; Groq interviewer; report in **7.1 s** after the
  interview; evaluator `openai/gpt-oss-120b`, no fallback.
- **3 interviewer turns degraded**: `LIVE_MODEL_DEADLINE_S` (10 s) ran out
  while the provider was rate-limiting, so the plan-based proposer asked those
  questions. The candidate saw a `provider_warning` each time and the session's
  `degraded_turns` audit lists them. Before this stage the same situation would
  have kept the candidate waiting through up to five back-offs instead.
- Practice: top gap "Technical substance", coached (6 checklist items), one
  3-minute attempt through the live runtime, evaluated with the same rubric:
  **before "solid", after "strong" → "Clearer explanation of the selected
  gap"**, with the single-attempt and coached limitations attached. One
  attempt by a scripted answer — this shows the pipeline works, not that
  practice helps people.

## Evaluation set (live)

`python tools/run_eval_set.py --live` — 24 examples, **0 failures**. Machine
observations only (no human labels exist):

- silent ×4: no model call; `no_answers` / `insufficient_evidence`; no score.
- malicious ×4: no dimension reached "strong"; no finding quotes the injected
  text.
- 3 of 20 model calls needed one JSON repair (this run predates the change that
  drops empty-quote findings instead of repairing).
- Worth a reviewer's attention: `sw-hm-strong-1` (a specific, owned answer) came
  back entirely "insufficient evidence" with no findings — a possible false
  negative.

Results: `logs/eval_set/live/results.jsonl` and `review_sheet_filled.csv`
(reviewer columns empty).

## What remains unverified

- **Real browser.** The connected Chrome could not reach `localhost` on this
  machine (error page for both `127.0.0.1:5173` and `localhost:5173` while
  `curl` got 200), so the attempt was stopped. Not exercised by a person or a
  browser in this run:
  - [ ] microphone permission, capture and the "Try the microphone again" path
  - [ ] playback, interruption (button and Esc) and truncation as heard
  - [ ] switching to typing after a voice failure, and the T / M / / keys
  - [ ] transcript and scorecard downloads from the page
  - [ ] dispute, re-check, accept/withdraw from the page
  - [ ] "Practise this gap" → checklist → attempt → before/after, and an
        unaided variation
  - [ ] reload on Results, Practice and History keeping the same state
  - [ ] Delete my data from the page, then reload
  These flows are covered at the API level by the tests above and the client
  type-checks and builds; the UI wiring itself is unverified.
- Voice with real Deepgram in this stage (the stage-14 `tools/live_voice_smoke.py`
  was not re-run).
- Human review of the evaluation set; any agreement or user-benefit claim.
- The ~450 ms live turn target.
