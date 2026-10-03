# Stage 9 — Roadmap, public company pack, dashboard

Read `CLAUDE.md` first. Stages 6 and 8 must be passing. **This is the first stage allowed a
database.**

**Goal:** a candidate can see how they are improving across sessions, and gets a concrete
preparation plan.

## Build

1. **Longitudinal store** — SQLite (propose; stop and ask). Tables for candidates, sessions,
   dimension scores, claim statuses and gaps per session. Session logs and transcripts stay as files;
   the DB indexes them. **Write path first** (never cut): every `EvaluationRecord` is written on
   completion, idempotently. Add a migration that imports existing session folders.
2. **Comparability** — trends are only drawn between sessions of the same pack, because only the
   spine is identical across them. Say so on the chart.
3. **Roadmap generator** — `src/interview/intake/roadmap.py`: input = latest gap list + collapsed or
   untested claims + weakest dimension trend. Output: 3–5 concrete prep items, each tied to its
   evidence ("Your answer on caching collapsed at depth 3 — prepare: …"). Candidate-facing only.
4. **Public company pack** — one pack built only from publicly available interview-format
   information (e.g. "big-tech hardware intern" or "startup SWE"). Data only; no company-private
   material. Note the sources in the pack file.
5. **Dashboard** (React) — session list, per-dimension trend lines per pack, claim history
   (which claims have held over time), roadmap, link to each report. Build against `fake_eval` first.

## Tests

- Writing the same evaluation twice doesn't duplicate rows.
- Trend query never mixes packs.
- Roadmap items each cite a gap or claim that exists in the input.

## Exit criterion

Seed the store with 4 sessions for one candidate (two packs). Paste: the trend query output, the
generated roadmap, and a screenshot of the dashboard.

## Before you write code

Reply with the DB schema, the roadmap input/output types and the public pack's draft. Wait for approval.
