# Stage 11 — Panel mode (multi-agent), hardening and lanes

Read `CLAUDE.md` first. Everything before this must be passing. **Panel mode behind `PANEL_MODE`.**

## Build

1. **Panel mode** — 2–3 interviewer **agents** sharing **one guard**; one voice speaks at a time.
   Distinct TTS voices / pack roles. `persona` filled on `likely_next`, `floor_granted`,
   `draft_ready`, `agent_step`. One evaluation, not per persona.
2. Fallbacks: provider failover, push-to-talk, resume-only / fallback repo.
3. Text lane — same bus, same agent+guard, same evaluation; delivery "not assessed".
4. Optional video — display only; never model/score input (contract 9).
5. Hardening — WS reconnect, session cap, intake rate-limit, delete-my-data.

## Exit criterion

Panel session waterfall ≤1.2 s p50; persona correct on agent lines; each fallback once; text-lane
report valid; delete-my-data clean.
