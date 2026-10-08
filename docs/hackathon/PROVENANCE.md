# Feature provenance — template

A record of what existed before the event and what was built during it, for
judges and for the team's own honesty. **Fill the blanks yourselves.** Nothing
here certifies eligibility, and nothing is submitted or published
automatically. Do not rewrite git history to make this table look different.

## Event

| Field | Value |
|---|---|
| Event name | _unfilled_ |
| Theme / track | _unfilled_ |
| Official start (date, time, time zone) | _unfilled_ |
| Official end | _unfilled_ |
| Rules on pre-existing code | _unfilled — quote the rule and link it_ |
| Team roster (names as registered) | _unfilled_ |
| Submission link | _unfilled_ |

## Baseline

The repository already existed before stage 16. Its history (as recorded by
git; author names are git identities, **not** a roster):

| Commit | Date (git) | Git author | Summary |
|---|---|---|---|
| [`2efdd58`](https://github.com/Pracheth123/Shadowtrace/commit/2efdd58) | 2026-10-03 | Pracheth123 | Stage 1 event bus/log, stage 2 transport |
| [`fd2b009`](https://github.com/Pracheth123/Shadowtrace/commit/fd2b009) … [`a21ecb0`](https://github.com/Pracheth123/Shadowtrace/commit/a21ecb0) | 2026-10-03 | Snehith | Guard, intake, speculative drafting, scoring, practice tracking, distress easing |
| [`ad749b9`](https://github.com/Pracheth123/Shadowtrace/commit/ad749b9) | 2026-10-03 | Pracheth123 | Groq via OpenAI SDK |
| [`3a7166a`](https://github.com/Pracheth123/Shadowtrace/commit/3a7166a) … [`72fe1a9`](https://github.com/Pracheth123/Shadowtrace/commit/72fe1a9) | 2026-10-04 | Pracheth123 | Stage 15 journey, UI, fixes |

Whether any of this falls inside the event window: _unfilled — compare with
the official start above_.

## Pre-existing functionality (before stage 16)

React client; FastAPI server; Groq interviewer and evaluator; Deepgram STT/TTS;
sequential HR → hiring manager → specialist rounds; guard; quote checks against
candidate turns; concurrent round evaluation; reports, history, downloads;
guest isolation; delete-my-data. Details: `docs/ARCHITECTURE.md`, `TASK.md`.

## Stage 16 additions (this change set)

Uncommitted at the time of writing. When committed, add the hashes and links.

| Feature | Files (main) | Author(s) | Commit(s) | Date | Demo evidence |
|---|---|---|---|---|---|
| One settings source; YAML no longer overrides env | `config.py`, `config/inference.yaml` | _unfilled_ | _unfilled_ | _unfilled_ | `tests/test_stage16_settings.py` |
| One bounded retry layer, deadlines, call metadata | `llm/client.py`, `session/proposer.py` | _unfilled_ | | | same |
| Provider diagnostics (configured vs verified) | `services/diagnostics.py`, `tools/check_providers.py` | _unfilled_ | | | output in `docs/decisions/stage16_upgrade.md` |
| Per-round evaluation jobs, partial results, retry failed rounds, cache, restart honesty | `services/evaluation.py`, `evaluation/role_eval.py` | _unfilled_ | | | `tests/test_stage16_eval_jobs.py`, live benchmark |
| Report v3 (labels, source match, question/limitation/action, top 3) | `evaluation/role_eval.py`, `scorecard.py`, `ResultsDashboard.tsx` | _unfilled_ | | | |
| Disputes and labelled revised assessments | `services/feedback.py` | _unfilled_ | | | |
| Targeted practice loop | `services/practice.py`, `PracticePage.tsx` | _unfilled_ | | | |
| Source review, practice focus, consent | `services/intake.py`, `SetupPage.tsx` | _unfilled_ | | | |
| Retention sweep; deletion cancels background work | `services/retention.py`, `server.py` | _unfilled_ | | | |
| Room: keyboard, transcript, mic recovery | `InterviewRoom.tsx`, `use-session.ts` | _unfilled_ | | | |
| Evaluation set + review sheet | `evaluation_set/` | _unfilled_ | | | |

## Development assistants vs runtime AI

Keep these separate in the submission.

| Kind | What | Where it was used |
|---|---|---|
| Development assistant | Claude (Claude Code) | Writing and editing code, tests, docs and the synthetic evaluation set during stage 16, at the team's direction. Record any other assistants the team used: _unfilled_. |
| Runtime AI | Groq-hosted models (`openai/gpt-oss-20b` interviewer, `openai/gpt-oss-120b` evaluator, `qwen/qwen3.8-27b` fallback) | Live interviewer proposals and post-session evaluation. |
| Runtime AI | Deepgram `nova-3` (STT), Aura-2 (TTS) | Voice sessions. |
| Not AI | Guard, coordinator, deterministic proposer, quote checks, scoring arithmetic, comparisons | Plain code. |

## Demo evidence checklist

- [ ] Screen recording of the demo sequence in README (date: _unfilled_)
- [ ] `tools/check_providers.py` output on the demo machine
- [ ] `tools/bench_feedback.py --live` JSON from the demo machine
- [ ] Browser checks from `docs/decisions/stage16_upgrade.md` ticked by a person
