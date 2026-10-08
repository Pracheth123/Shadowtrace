# Judging map — shipped capabilities by criterion

Only what exists in the code and has evidence. Claims of user benefit or
employment outcomes are **not** made: none have been measured.

## Real-World Impact

| Capability | Evidence |
|---|---|
| Practice built from the candidate's own background, for software and sales (others run, less validated) | `tests/test_stage15_journey.py` a/b; `docs/KNOWN_LIMITATIONS.md` |
| Feedback that shows its evidence and limits, and can be disputed | Report v3; `tests/test_stage16_journey.py::test_dispute_*` |
| Targeted practice on one gap with before/after evidence | `test_practice_attempt_runs_through_the_runtime_and_compares_one_dimension` |
| Consent, retention, deletion that cancels background work | `test_nothing_is_processed_without_consent`, `test_deletion_during_evaluation_*`, `test_retention_sweep_*` |
| Not a screening tool; no hiring predictions | `CLAUDE.md`, README, every report's disclaimer |

## Technical Implementation and AI Use

| Capability | Evidence |
|---|---|
| Runtime AI: Groq models propose interviewer moves; a deterministic guard enforces spine, depth, time, scope | `session/proposer.py`, `session/guard.py` |
| Runtime AI: per-round evaluators with schema-validated JSON, bounded repair, quote verification against the right turn | `evaluation/role_eval.py`; tests in `test_stage16_journey.py` |
| One bounded retry layer with deadlines and real model/token metadata | `llm/client.py`; `test_stage16_settings.py` |
| Partial results, idempotent jobs, cache, restart honesty | `services/evaluation.py`; `test_stage16_eval_jobs.py` |
| Prompt-injection handling (uploads sanitised, data delimited) | `test_instructions_inside_uploads_do_not_reach_any_prompt_as_instructions`; evaluation set "malicious" |
| Development assistant use is disclosed separately | `PROVENANCE.md` |

## Innovation

| Capability | Note |
|---|---|
| Claim interrogation from the candidate's own material, with source review | Statements shown in their source text; corrections kept as the candidate's own words |
| Disputable feedback with labelled automated re-check that does not "self-certify" | `services/feedback.py` |
| Same-rubric practice comparison with explicit "unavailable" outcomes | `services/practice.py` |

## Execution

| Item | Evidence |
|---|---|
| Offline suite | 297 passed, 1 skipped (pre-existing `test_stage2` timing flake noted; see `docs/decisions/stage16_upgrade.md`) |
| Client | `tsc --noEmit` clean, `vite build` succeeds |
| Clean install | Fresh Python 3.12 venv from `pyproject.toml` (found and fixed the missing `python-multipart`) |
| Live checks | Provider auth + model availability; 6-session latency benchmark; 24-example evaluation run |

## Presentation

| Item | Where |
|---|---|
| Demo script | README → "A genuine demonstration" |
| Architecture | `docs/ARCHITECTURE.md` |
| Honest limits | `docs/KNOWN_LIMITATIONS.md` |
| Latency with sample size and caveats | `docs/decisions/stage16_upgrade.md` |
