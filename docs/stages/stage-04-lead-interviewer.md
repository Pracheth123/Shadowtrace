# Stage 4 — Wire the live interviewer (the MVP spine)

Read `CLAUDE.md` first. Stages 2 and 3 must be passing.

**Goal:** a person opens a web page, talks to one AI interviewer, and has a real back-and-forth of
5–8 turns. Transport and inference meet only through the bus. No speculation / full tool loop yet —
this stage sets the baseline that stage 7 has to beat. The interviewer is the live-agent *surface*
(conversation context = state); tools and guard arrive in stage 5.

## Build

1. **`src/interview/server.py`** — FastAPI app with one WebSocket endpoint per session. Creates the
   bus, the logger, the transport session and the interviewer, and nothing else. Transport and
   inference modules never import each other (contract 1); add a test that fails if they do.
2. **Session lifecycle** — `start` (writes the header, opens the log), turns, `end` (emits
   `session_complete` with `transcript_path`, `log_path`, `pack_id`, `intensity_history`).
3. **Transcript writer** — subscribes to `final_transcript` and agent utterances, honours `truncate`
   (contract 10), writes `transcript.json` at session end:
   `{speaker, turn_id, text, t_start, t_end}` list.
4. **Fixed opener and closer** — opens with a fixed line; closes after N turns or T minutes,
   whichever first. Questions are free-form from the model for now; stage 5 guard/spine replaces this.
5. **Client — React + TypeScript + Vite** in `client/`. One page: start button, mic level meter,
   live caption of the agent's current line, end button. No styling work beyond legible.
6. **Barge-in end to end** — candidate can interrupt; generation is cancelled, audio stops, transcript
   records only what was heard. Emit `agent_step(phase=cancelled)` when an in-flight speak is killed.

### Wire format (client ↔ server; not bus events)

Audio: PCM s16le, 16 kHz, mono, 20 ms frames (640 bytes) as binary WebSocket frames both ways.

Client → Server JSON: `session_start`, `playback_ack`, `barge_in`, `session_end`.
Server → Client JSON: `session_ready`, `agent_utterance_start`, `agent_utterance_end`, `caption`,
`turn_end`, `session_complete`, `error`.

## Tests

- Full session against fake STT + fake LLM + fake TTS, no network → valid log + transcript.
- Import-boundary test (transport ↔ session/inference).
- A recorded session replays through the waterfall script.

## Exit criterion

Three real sessions of 5+ turns each in `fixtures/sessions/`. Paste one waterfall and p50/p95 across
all three. Expected endpoint→first audio roughly 1.1–1.5 s — report measured.
