# Stage 6 — Intake, indexer agent and fit check

Read `CLAUDE.md` first. No dependency on stages 2–5. Runs entirely before a session (band 1).
Produces JSON files on disk. It never touches the live session bus.

**Goal:** from a repo URL, resume and optional JD, produce `resume_profile.json`, `claims.json`
and `fit_gap.json`. The repo indexer is an **agent** with read-only tools.

## Build

1. Intake Pydantic schemas (`IntakeRequest`, `ResumeProfile`, `Claim`, `ClaimsFile`, `FitGap`).
2. Resume parser + sanitize (contract 7) — port skill-sync ideas per prior brief; no code copy.
3. **`src/interview/intake/repo_indexer.py`** — indexer **agent**: tools `list_dir`, `read_file`,
   `grep`, `git_log` only. Step limit. Never execute candidate code. Emits an exploration trace
   (JSONL of `agent_step` / `tool_call` / `tool_result` style records on disk — not the live bus).
   Shallow clone with time/size limits → `RepoSummary`.
4. Claims File ≤8 interrogable claims; text must appear in source.
5. Fit check (rule-based first, LLM only for unsure); feeds **agent/guard and roadmap only**.
6. Fallbacks: resume-only / pre-indexed repo. CLI `tools/intake.py`.

## Tests

- Indexer never calls a code-execution tool; step limit enforced.
- Exploration trace replays claims selection without re-cloning when fixtures stub tool_results.
- Hallucinated claim text dropped. Contract 7 sanitize coverage.

## Exit criterion

Run intake on a public repo + resume; paste claims.json (≤8), fit_gap summary, and a snippet of
the exploration trace showing tool use.
