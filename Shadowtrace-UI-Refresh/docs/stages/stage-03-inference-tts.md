# Stage 3 — Inference and TTS only, no audio input

Read `CLAUDE.md` first. Stage 1 must be passing. Independent of stage 2.

**Goal:** given a transcript of what the candidate said, produce the interviewer's next line as
streamed speech, and measure exactly how long each step takes. No microphone. This is the **speak
path** the live agent will use; the tool loop itself lands in stages 5/7.

## Build

1. **`src/interview/session/interviewer.py`** — phrasing / speak surface for the live agent.
   Input: conversation history (its own context is its state) + short fixed system prompt.
   Output: streamed text. Emits `draft_ready` when the **first sentence** is complete
   (`utterance_id`, `variant="plain"`). No tools yet.
2. **`src/interview/transport/tts.py`** — streaming TTS. Emits `tts_chunk` with `t_audio_out`,
   `utterance_id`, `audio_ref`, per-word `word_timestamps`. Start TTS on the first sentence.
3. **`src/interview/mocks/fake_llm.py`** / **`fake_tts.py`** — canned streaming + silence timestamps.
4. **`tools/bench_ttft.py`** / **`tools/bench_tts.py`** — measure models/vendors; Claims File-sized
   context in a delimited data block (contract 7).
5. **Client playback acks** — test page plays `tts_chunk` audio, `playback_ack` every ~100 ms.

## Rules

- Model choice by measured TTFT. Prompt short and fixed; repo/resume only as delimited data.

## Tests

- Inference with `fake_tts`; TTS with `fake_llm`. Truncation replay honours last acked word.

## Exit criterion

TTFT/TTS p50/p95 tables in `docs/decisions/inference.md`; truncation log lines pasted.
