# Stage 5 — Guard and the spine/probe split

Read `CLAUDE.md` first. Stage 4 must be passing.

**Goal:** the interviewer stops improvising the agenda. The **live agent proposes** the next move;
a deterministic **guard** enforces spine order/verbatim, time budget, probe depth and claims scope.
Spine questions are asked word for word via `ask_spine`.

## Build

1. **Pack format** — `src/interview/packs/<pack_id>.yaml`. Fields: `pack_id`, `title`, `time_budget_s`,
   `spine` (`{id, text, competency}`), `competencies`, `probe_policy`, `scoring_weights`.
   Pydantic model; validate on load. Two starter packs.
2. **`src/interview/session/tools.py`** — local in-memory tools (&lt;20 ms): `get_claims`, `get_claim`,
   `get_coverage`, `get_time_remaining`, `get_candidate_signals`, `note_claim_status`, `ask_spine`,
   `end_session`. Emit `tool_call` / `tool_result`. Claims empty until stage 6 (use fixture).
3. **`src/interview/session/guard.py`** — pure rules over agent intents. On violation emit
   `guard_override` and the enforced action. Never chooses questions on its own.
   **No repeated question.** A spine or probe must not repeat a question already asked in
   this session. Compare the proposed text with questions already asked; a near-duplicate
   counts (same wording aside from case, whitespace, and small edits). On that violation
   the override rule is `question_repeat` and the enforced action is the next unasked
   spine, or end the session if none remain.
4. **Minimal agent loop** — asyncio tool loop with hard step limit; emits `agent_step`. For spine,
   speak pack text exactly (check + warn). For probes, phrase one question from guard-approved target.
5. **`question_planned` / `coverage_update`** — emitted after guard acceptance (waterfall compat).

## Tests

- Guard table-driven: spine order, time budget drops probes, depth cap, claims scope.
- No repeat: asking a spine or probe whose text was already asked this session, including a
  near-duplicate, is rejected and logged as `guard_override` with rule `question_repeat`.
- Every override appears as `guard_override` in the log.
- Two sessions of the same pack ask identical spine text.
- Replay of recorded tool_results needs no live tools (contract 5).

## Exit criterion

Replay two sessions through the guard; print approved sequence. One live session: paste
`agent_step` / `guard_override` / `question_planned` / `coverage_update` lines; every spine id covered.
