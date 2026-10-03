# Stage 9 — Roadmap agent, public company pack, dashboard

Read `CLAUDE.md` first. Stages 6 and 8 must be passing. **First stage allowed a database.**

**Goal:** show improvement across sessions and a concrete prep plan.

## Build

1. Longitudinal store (SQLite — propose schema first). Write path never cut.
2. Comparability only within the same pack.
3. **Roadmap agent** — plans across past sessions using the store (local tools: list sessions,
   get scores, get claim history, get gaps). Emits a short exploration/plan trace. Output: 3–5
   concrete prep items each tied to evidence.
4. Public company pack (public sources only).
5. Dashboard (React) against `fake_eval` first.

## Exit criterion

Seed 4 sessions; paste trend query, generated roadmap, dashboard screenshot.
