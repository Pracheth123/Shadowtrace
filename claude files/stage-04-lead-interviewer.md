# Stage 4 — Wire the lead interviewer (the MVP spine)

Read `CLAUDE.md` first. Stages 2 and 3 must be passing.

**Goal:** a person opens a web page, talks to one AI interviewer, and has a real back-and-forth of
5–8 turns. Transport and inference meet only through the bus. No speculation yet — this stage sets the
baseline that stage 7 has to beat.

## Build

1. **`src/interview/server.py`** — FastAPI app with one WebSocket endpoint per session. It creates the
   bus, the logger, the transport session and the interviewer, and nothing else. Transport and
   inference modules never import each other (contract 1); add a test that fails if they do.
2. **Session lifecycle** — `start` (writes the header, opens the log), turns, `end` (emits
   `session_complete` with `transcript_path`, `log_path`, `pack_id`, `intensity_history`).
3. **Transcript writer** — subscribes to `final_transcript` and agent utterances, honours `truncate`
   (contract 10), writes `transcript.json` at session end: a list of `{speaker, turn_id, text, t_start, t_end}`.
4. **Fixed opener and closer** — the interviewer opens with a fixed line and closes after N turns or
   T minutes, whichever first. Questions are free-form from the model for now; stage 5 replaces this.
5. **Client — React + TypeScript + Vite** in `client/`. One page: start button, mic level meter,
   live caption of the agent's current line, end button. No styling work beyond legible.
6. **Barge-in end to end** — the candidate can interrupt the agent; generation is cancelled, audio
   stops, and the transcript records only what was heard.

## Tests

- Full session against fake STT + fake LLM + fake TTS, no network, produces a valid log and transcript.
- Import-boundary test (transport ↔ inference).
- A recorded real session replays through the waterfall script.

## Exit criterion

Three real sessions of 5+ turns each, recorded. For each: the full per-turn waterfall. Expected
total (endpoint → first audio) is roughly 1.1–1.5 s — report what you actually measure. Paste the
waterfall for one session and the p50/p95 across all three. Keep the three logs in
`fixtures/sessions/` — stage 8 uses them.

## Before you write code

Reply with the WebSocket message types between client and server (these are not bus events; they
are the wire format) and how audio is framed. Wait for approval.
