# Stage 11 — Panel mode, hardening and lanes

Read `CLAUDE.md` first. Everything before this must be passing. **Panel mode lives behind a feature
flag** and is the first thing to cut if time runs short — the single-interviewer product is complete
without it.

## Build

1. **Panel mode (flag: `PANEL_MODE`)** — 2–3 voices, each a pack-defined role (e.g. technical lead,
   hiring manager) with its own question focus and a distinct TTS voice. One model call per turn;
   the planner picks the speaking role deterministically from coverage (no urge scores, no fairness
   decay, no server-side audio mixing — removed in CLAUDE.md). Only one voice speaks at a time.
   `persona` is now filled on `likely_next`, `floor_granted`, `draft_ready`. Reports remain one
   evaluation, not one per persona.
2. **Fallbacks**
   - Model/TTS outage: fail over to a second provider; if none, a local model at lower quality,
     announced on screen.
   - Bad room acoustics: push-to-talk toggle (disables barge-in, keeps truncation correct).
   - No repo / private repo / trivial repo: resume-only mode or the pre-indexed fallback repo,
     shown on screen.
3. **Text lane** — the same session over typed chat (no audio). Same bus, same planner, same
   evaluation; delivery dimension is marked "not assessed".
4. **Optional video** — if captured at all, it is displayed to the candidate only. It is **never an
   input to any model or score** (contract 9). Add a test asserting no evaluation input carries video.
5. **Hardening** — reconnect a dropped WebSocket mid-session without losing the log; cap session
   length; rate-limit intake; delete-my-data command that removes a candidate's files and DB rows.

## Exit criterion

- Panel mode: one recorded session with 2 voices, waterfall still within 1.2 s p50, transcript shows
  correct `persona` on every agent line.
- Each fallback demonstrated once (paste the log lines showing the switch).
- Text-lane session produces a valid report.
- Delete-my-data leaves no files or rows for that candidate (paste the check).

## Before you write code

Reply with the panel role pack format, the fallback order, and the reconnect design. Wait for approval.
