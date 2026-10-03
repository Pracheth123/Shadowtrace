# Stage 8 — The evaluation pass (evaluator agents)

Read `CLAUDE.md` first. Needs stage 1 + recorded sessions. **Runs entirely offline.** Nothing in
the live path calls it (contract 6). Live agent has no evaluation tool.

**Goal:** within ~30 s, transcript + event log + Claims File → feedback report with quoted evidence.

## Build

1. Load inputs; repo/resume only as delimited data (contract 7).
2. Pattern detectors (no LLM).
3. **Three evaluator agents** (concurrent) with lookup tools: transcript turn by id, event-log
   slice, claim by id — verify a quote before using it. Log `agent_step` / `tool_call` /
   `tool_result` into an eval trace file (not the live session bus).
4. Claim adjudication with negation check.
5. Merge + scorer (findings only; no keyword/claim-match/fit fields — contract 8).
6. Report JSON + HTML. `fake_eval` mock. CLI `tools/evaluate.py`.
7. **Optional: download the transcript as PDF.** A control on the report downloads the
   transcript PDF. Proposed library, not added now: **WeasyPrint**. The report is already
   HTML, so the PDF is a render of that document rather than a second layout that can
   drift. WeasyPrint needs Pango and Cairo on the machine; add it only when this stage
   is built.

## Exit criterion

Stage-4 recordings + two hand-written transcripts: report &lt;30 s; collapsing claim correct;
every finding quote found in transcript.
