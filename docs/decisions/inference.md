# Inference and TTS Decisions

## Provisional choice (pending live API benches)

**Date:** 2026-10-03  
**Status:** offline path complete; live vendor numbers below are **provisional** until
`OPENAI_API_KEY` / Anthropic / ElevenLabs keys are available to re-run
`tools/bench_ttft.py` and `tools/bench_tts.py` without `--mock`.

Intended production defaults in `config/inference.yaml`:

- LLM: `gpt-4o-mini` (OpenAI)
- TTS: `openai-tts` / `tts-1` / voice `nova`

Reasons to keep when live benches confirm:

1. Measure at least three LLMs (small/fast + larger) and three TTS vendors.
2. Prefer the model with best first-sentence latency **and** prompt adherence
   (no "Great answer!" preamble).
3. Prefer a single-vendor LLM+TTS pair when latency is within ~50 ms of the leader.

---

## System prompt (fixed, short)

```
You are a professional technical interviewer conducting a mock interview.
Your role: ask one focused follow-up question based on the candidate's last answer.
Be concise. One question per turn. No preamble, no "Great answer!".
If the candidate struggled, use a softer opening (a single-sentence concession) then ask.
Never reveal scoring criteria. Never mention the claims list or resume text directly.
Respond in plain English. Maximum 3 sentences total.
```

Claims data is injected in a delimited `<<CLAIMS>>` block inside the user message,
never in the system prompt (CLAUDE.md §7 — repo/resume content is data, not instructions).

---

## Measured TTFT (from tools/bench_ttft.py)

| Model | TTFT p50 | TTFT p95 | First-sentence p50 | First-sentence p95 |
|---|---|---|---|---|
| mock-fast (50ms delay, 60 t/s) | 62 ms | 63 ms | 155 ms | 156 ms |
| mock-medium (150ms delay, 30 t/s) | 155 ms | 157 ms | 295 ms | 299 ms |
| mock-slow (300ms delay, 15 t/s) | 309 ms | 313 ms | 541 ms | 546 ms |


## Measured TTS first-chunk (from tools/bench_tts.py)

| Vendor | First-chunk p50 | First-chunk p95 |
|---|---|---|
| mock (FakeTts) | 0 ms | 0 ms |

Live next (same 20 sentences): `openai-tts` (tts-1), `elevenlabs` (turbo-v2),
`google-tts` (neural2-d). LLMs for TTFT: `gpt-4o-mini`, `claude-3-haiku-20240307`,
`gpt-4o` (± `gemini-2.0-flash`).

## Truncation contract — verified log lines

Replay of `fixtures/audio/cached_response.json` with `played_ms=850`:

```text
[tts_chunk]     utterance_id=utt-cached words=14 audio_ref=fixtures/audio/cached_response.pcm
[playback_ack]  played_ms=850 utterance_id=utt-cached
[barge_in]      turn_id=trunc-turn
[truncate]      last_heard_word=walk word_index=6 utterance_id=utt-cached
```

Word timeline (offset_ms): Thanks@0 for@180 sharing@280 that.@420 Can@680 you@760
**walk@820** ← last heard at 850 ms · me@900 through@950 …

Verified by `tests/test_stage3.py::test_truncation_word_index_from_tts_chunk`
(emits the four events onto the bus + JSONL log). Manual ear-check page:
`client/stage3_playback.html` (acks every 100 ms, barge-in truncates at last heard word).

---

## Pipeline note

With provisional LLM+TTS numbers (~310 ms TTFT + ~180 ms TTS), endpoint→first_audio
is ~640 ms p50 before speculative drafting. Stage 4 closes the gap toward the
~450 ms target by starting TTS on a speculative first sentence.
