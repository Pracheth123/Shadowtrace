# Stage 2 — Transport only, no LLM

Read `CLAUDE.md` first. Stage 1 must be passing.

**Goal:** a candidate speaks into a browser, the server detects when they finish, and plays a fixed
cached clip back. Every step lands in the event log. No LLM, no generated speech, no questions.

## Build

1. **Framework choice by measurement.** Build a minimal echo session (mic in → endpoint → cached clip
   out) in **both Pipecat and LiveKit Agents**, each in under ~150 lines, and measure endpoint→first
   audio on the same machine and the same 10 recorded utterances. Pick the faster one; delete the
   other. Write the numbers into `docs/decisions/transport.md`.
2. **`src/interview/transport/session.py`** — the chosen framework, one session per candidate. It
   only translates framework callbacks into bus events and bus events into audio. No turn logic here.
3. **`src/interview/transport/vad.py`** — software VAD (Silero or WebRTC VAD — state which and why).
   Emits `speech_start` (assigns the new `turn_id`) with `t_audio_in`.
4. **`src/interview/transport/stt.py`** — streaming STT adapter that emits `partial` and
   `final_transcript`. It accepts a `keywords: list[str]` boost list as a parameter (stage 6 will
   supply the Claims File terms; here it is empty or a test list). Vendor is your proposal — give two
   candidates with measured partial latency.
5. **`src/interview/transport/turn_detector.py`** — semantic endpointing: silence threshold **plus**
   a check on the partial text (e.g. trailing "and", "so", "because", or a mid-sentence pause should
   extend the wait). Emits `endpoint` with `confidence`. Thresholds live in a config file, not code.
6. **Barge-in detection, guarded** — while the cached clip plays, speech above a confidence threshold
   for a minimum duration emits `barge_in`. Playback stops. Emit `playback_ack` every ~100 ms from
   the client with `played_ms` and `utterance_id`, and on barge-in emit `truncate` at the last acked
   word (the cached clip ships with word timestamps). This is the truncation contract — test it now.
7. **`src/interview/mocks/fake_stt.py`** — replays a transcript file as `partial`s with realistic
   revisions (words change, then stabilise) and timings, then a `final_transcript`.
8. **Minimal client** — a single static HTML page with a mic button is enough. React comes in stage 4.
9. **`tools/record_session.py`** — runs a live session and writes the JSONL log.

## Mocks are first-class

The turn detector must run against `fake_stt` with no audio at all. If it can't, its interface is wrong.

## Tests

- Turn detector on fake-STT fixtures: normal answer, long answer (8 s+, must not be cut), hesitant
  answer with "um… so…" pauses (must not endpoint mid-thought), single-word answer.
- Truncation: a barge-in at a known `played_ms` produces the expected `last_heard_word`.
- Replay a recorded live log: the turn detector, fed only from the log, emits the same endpoints.

## Exit criterion

1. Endpoint latency (end of speech → `endpoint`) p50 and p95 from 20 real utterances, from the
   waterfall script.
2. Cached-clip playback starts **under 150 ms** after `endpoint` (p50).
3. False barge-ins counted over 2 minutes with a crowd/café recording playing near the mic and nobody
   speaking. Report the count and the threshold used.
4. One barge-in truncation shown in the log with the correct `last_heard_word`.

Paste the waterfall output and the counts.

## Before you write code

Reply with: the two STT vendors you'll measure, the VAD choice, the turn-detector config fields,
the fake-STT transcript file format, and every new dependency with a one-line reason. Wait for approval.
