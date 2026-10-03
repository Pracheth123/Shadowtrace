# Stage 3 — Inference and TTS only, no audio input

Read `CLAUDE.md` first. Stage 1 must be passing. Independent of stage 2.

**Goal:** given a transcript of what the candidate said, produce the interviewer's next line as
streamed speech, and measure exactly how long each step takes. No microphone.

## Build

1. **`src/interview/session/interviewer.py`** — the lead interviewer. Input: the conversation so far
   (its own context is its state; no shared store) plus a short system prompt with the format's
   tone rules. Output: streamed text. Emits `draft_ready` when the **first sentence** is complete
   (with `utterance_id`, `variant="plain"`).
2. **`src/interview/transport/tts.py`** — streaming TTS adapter. Emits `tts_chunk` with
   `t_audio_out`, `utterance_id`, `audio_ref` and per-word `word_timestamps`. Start TTS on the first
   sentence, not the whole reply.
3. **`src/interview/mocks/fake_llm.py`** — returns canned text after a configurable delay, streamed
   token by token at a configurable rate.
4. **`src/interview/mocks/fake_tts.py`** — emits silence of the right length with word timestamps.
5. **`tools/bench_ttft.py`** — feeds the 20 transcripts in `fixtures/transcripts/` (write them: realistic
   candidate answers, 30–200 words each) to each candidate model, 5 runs each, and reports p50/p95
   time-to-first-token and time-to-first-sentence. Prompt includes a realistic-size context (a
   Claims File-sized blob of ~8 short claims, plus 6 prior turns).
6. **`tools/bench_tts.py`** — first-chunk latency per TTS vendor, same sentences.
7. **Client playback acks** — a test page plays `tts_chunk` audio and sends `playback_ack` every
   ~100 ms. Verify word timestamps line up with what is audible.

## Rules

- **Model choice is by measured TTFT**, never by name. Measure at least three models (state which,
  including one small/fast and one larger). Quality check: blind-read 10 outputs from each.
- The prompt is short and fixed. No repo or resume text appears in it as instructions (contract 7) —
  when claims are included, they are inside a clearly delimited data block.

## Tests

- Inference runs with `fake_tts`; TTS runs with `fake_llm`. Neither needs audio input.
- Truncation: replay a recorded `tts_chunk` + `playback_ack` + `barge_in` sequence; the transcript
  shows only the words actually heard.

## Exit criterion

- A table of TTFT p50/p95 and TTS first-chunk p50/p95 per model/vendor, produced by the bench scripts.
- The chosen model and TTS, written to `docs/decisions/inference.md` with the numbers.
- Truncation at the last acked word verified on a recording (paste the log lines).

## Before you write code

Reply with the models and TTS vendors you'll benchmark, the system prompt draft, and new
dependencies with reasons. Wait for approval.
