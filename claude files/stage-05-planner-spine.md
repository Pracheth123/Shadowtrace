# Stage 5 — Question planner and the spine/probe split

Read `CLAUDE.md` first. Stage 4 must be passing.

**Goal:** the interviewer stops improvising the agenda. A deterministic planner decides *what* to ask
next; the model only decides *how to say a probe*. Spine questions are asked word for word.

## Build

1. **Pack format** — `src/interview/packs/<pack_id>.yaml` (or JSON; pick one). Fields: `pack_id`,
   `title`, `time_budget_s`, `spine` (list of `{id, text, competency}`), `competencies` (list of ids
   with descriptions), `probe_policy` (`max_depth` up to 4, `max_probes_per_spine`,
   `probe_sources` order), `scoring_weights` (the four dimensions; default equal). Write a Pydantic
   model for it and validate on load.
2. **Two starter packs** — `generic-swe-project-deepdive` and `generic-behavioral`. 4–6 spine
   questions each. Data only.
3. **`src/interview/session/planner.py`** — pure function: `(pack, coverage_state, last_turn_summary,
   claims, gaps, elapsed_s) → PlannedQuestion`. No LLM. Emits `question_planned` with `kind`,
   `competency`, `target_depth`, and emits `coverage_update` after each turn.
   - Spine questions come out verbatim, in pack order.
   - Between spine questions, up to `max_probes_per_spine` probes, deepening `target_depth` 1→4
     (what → how → why that choice → what breaks / what would you change).
   - **Coverage enforcement:** if remaining time can't fit the remaining spine, stop probing.
     Every spine question is asked in every session unless the session is ended early by the candidate.
   - `claims` and `gaps` are inputs but may be empty — stage 6 fills them. Use a fixture until then.
4. **Interviewer changes** — for a spine question, the interviewer speaks the text exactly. For a
   probe, it gets the planner's target (competency, depth, the claim or quote to probe) and phrases one
   question. Add a check that a spine question was spoken verbatim; log a warning if not.

## Tests

- Planner is a pure function: table-driven tests over coverage states and time budgets.
- With a short time budget, all spine questions still get asked; probes are dropped first.
- Two sessions of the same pack ask identical spine text.

## Exit criterion

Replay two recorded sessions through the planner (planner-only, fed from logs) and print the planned
sequence: spine/probe, competency, depth. Then run one live session and paste its `question_planned`
and `coverage_update` lines and the final coverage (every spine id covered).

## Before you write code

Reply with the pack schema and the planner's input/output types. Wait for approval.
