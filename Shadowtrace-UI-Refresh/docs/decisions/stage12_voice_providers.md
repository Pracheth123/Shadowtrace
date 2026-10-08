# Stage 12 — real voice providers (Deepgram), verified config

Status: **partially complete.** Adapters, audio conversion, turn-boundary logic
and configuration are implemented and verified offline. Wiring them into the
live server, the client capture/playback path, and live-credential validation
are **not done** — see "Outstanding" at the end.

## Provider facts, verified 2026-10-04

Every one of these was checked against official documentation before writing
code, because the previous implementation was built on assumptions that turned
out to be false.

| Thing | Previous assumption | Verified reality | Source |
| --- | --- | --- | --- |
| Nova-3 vocabulary boosting | `keywords=[...]` | **`keyterm`**, repeated once per term, 500-token budget across all terms. `keywords` is Nova-2 only and is ignored by nova-3 | [keyterm](https://developers.deepgram.com/docs/keyterm) |
| `is_final` | one completed answer | fires **many times per utterance**; finalises a *segment* | [endpointing & interim results](https://developers.deepgram.com/docs/understand-endpointing-interim-results/) |
| `speech_final` | not handled | provider endpointing detected a pause — *this* is the utterance boundary | same |
| `UtteranceEnd` | not handled | separate message, backstop when endpointing does not fire; **requires `vad_events=true`** | [listen-streaming](https://developers.deepgram.com/reference/speech-to-text/listen-streaming) |
| `endpointing` default | n/a | **10 ms** — fires on tiny gaps; 300–500 ms is the conversational range | same |
| TTS `Flush` | free to call per phrase | **max 20 per 60 s** | [tts-ws-flush](https://developers.deepgram.com/docs/tts-ws-flush) |
| TTS `Clear` | cancels playback | clears the *provider's* buffers and stops new chunks; **says nothing about audio already sent** | [tts-ws-clear](https://developers.deepgram.com/docs/tts-ws-clear) |
| TTS word alignment | assumed available | **not documented** for the Speak API | [tts streaming overview](https://developers.deepgram.com/docs/tts-streaming-feature-overview) |
| TTS voices | `nova`/`alloy`/`onyx` (OpenAI names) | Aura-2: `aura-2-thalia-en`, `aura-2-apollo-en`, `aura-2-vesta-en`, `aura-2-arcas-en`, `aura-2-orpheus-en`, `aura-2-andromeda-en` | [tts-models](https://developers.deepgram.com/docs/tts-models) |
| `gemma2-9b-it` fallback | available | **retired.** Groq production: `llama-3.1-8b-instant`, `llama-3.3-70b-versatile`, `openai/gpt-oss-20b`, `openai/gpt-oss-120b` | [Groq models](https://console.groq.com/docs/models) |

## Decisions

### No `deepgram-sdk`; the documented WebSocket protocol instead

`deepgram-sdk>=3.0` was declared in `pyproject.toml` and **was never
installed**. `SttAdapter.start()` caught `ImportError`, logged a warning and
returned — so the adapter entered "no-op mode" and the session produced no
transcripts at all while still reporting success. That is the exact failure mode
this stage was asked to remove, and a dependency that can do it on import is
not a dependency worth keeping.

Replaced with direct use of `websockets` (already a dependency) against the
documented `/v1/listen` and `/v1/speak` protocols. Reasons:

- The SDK's async surface has moved repeatedly between versions
  (`listen.asyncwebsocket.v("1")` and successors). Pinning a version we cannot
  exercise against a live key would be a guess presented as a decision.
- The wire protocol is documented and stable.
- Most importantly: it is **testable offline**. `tests/test_stage12_deepgram.py`
  runs a real local WebSocket server that speaks the same JSON, and points the
  real adapters at it. 32 tests, no credentials, no network.

`deepgram-sdk` was removed from `pyproject.toml`.

### Turn boundaries: buffer segments, close on either boundary signal

The old adapter emitted a `final_transcript` for **every** `is_final`. Since
`is_final` fires several times inside one answer, a candidate saying three
sentences produced three completed answers, so the interviewer replied
mid-sentence and the transcript recorded three turns where there was one.

Now: `is_final` segments accumulate (text, word timings, confidence), and
exactly one `final_transcript` is emitted when the first of `speech_final`,
`UtteranceEnd`, or an explicit client finalise arrives. A lock plus an
empty-buffer check means a `speech_final` **and** a following `UtteranceEnd` for
the same utterance produce one answer, not two. `FinalTranscript.boundary`
records which signal closed the turn, because "the provider thought you
stopped" and "you released push-to-talk" are different claims.

### Audio format is converted and declared, never assumed

`PcmStreamConverter` is stateful per session because the three ways to get this
wrong all happen at chunk seams:

- a chunk ending mid-frame byte-swaps everything after it unless the odd byte is
  carried;
- resampling each chunk independently restarts interpolation at sample 0,
  dropping a fraction of a sample per seam — audible clicking and measurable
  drift against `t_audio_in`;
- stereo chunks can end mid-frame-group, so the carry must be whole frames.

Verified by a spectral test: a 1 s 440 Hz tone at 48 kHz, pushed in six
irregular chunks, resamples to **exactly** 16000 frames with the peak still at
440.0 Hz and no step discontinuity. The declared `encoding`/`sample_rate` on the
socket is whatever the converter actually produced.

Resampling is linear interpolation. It does alias energy above 8 kHz when
downsampling 48 k → 16 k. That is stated rather than hidden; `target_rate` is
configurable so the device rate can be sent instead.

### TTS: sentence-wise Speak, one Flush per line

Flush is limited to 20 per minute, so flushing per phrase would exhaust the
budget inside a single long answer. Each sentence of an agent line is sent as
its own `Speak` (so synthesis of sentence one starts immediately), and `Flush`
is sent **once** per line. `FlushBudget` guards the documented limit; exhausting
it degrades to provider-side buffering rather than erroring.

### Word timings on synthesised audio are estimates, and say so

The Speak API is not documented to return word alignment. So word timestamps on
a real TTS chunk are derived from a fixed speaking rate, and **every chunk
carries `timings_estimated=True`**. Consequence, stated plainly: **truncation on
barge-in is word-approximate, not exact.** Any earlier claim of exact
word-level truncation applied only to the stage-3 mock, which generated the
timings it then reported.

On STT the situation is the opposite — Deepgram reports real per-word
start/end on finalised segments — so `FinalTranscript.timings_estimated` is
`False` there, and the distinction is explicit in the log rather than inferred.

### `Clear` is only half of barge-in

`Clear` stops the provider generating. It does not recall audio already sent,
which by then is sitting in the browser's queue. So barge-in needs both halves:
provider `Clear`, **and** stopping/discarding local playback. This stage
implements the provider half plus late-chunk rejection (a chunk arriving for a
cancelled utterance is counted and dropped, never forwarded). The client half —
real audio-clock accounting and queue teardown — is **not yet implemented**; see
Outstanding.

### Credentials

`DEEPGRAM_API_KEY` and `GROQ_API_KEY` are `SecretStr` in one settings object, so
a stray log line, `repr`, or `model_dump_json` prints a mask. The key reaches
Deepgram in an `Authorization: Token …` **request header**, never in a URL
(where it would land in logs and proxies). Verified by test: the fake server
sees the header, and the recorded query string does not contain the key.
`public_dict()` is what `/healthz` and the browser may see — booleans for
credential presence, never values. A grep of `client/` for either key name
returns nothing.

### Mocks cannot run in production

`APP_ENV=prod` with `ALLOW_MOCK_PROVIDERS=1` is a **startup error**. Mock STT,
LLM and TTS stay first-class in dev and test, where they are what makes the
suite run without credentials. In production they are the mechanism by which a
missing key becomes a "successful" interview made of silence and canned text.

### Retired models refused at startup

`Settings` rejects a configured model in `RETIRED_GROQ_MODELS` with the current
production list in the error. A fallback that 404s is worse than no fallback: it
burns the retry budget and still fails. Separately — and this is a limitation,
not a fix — a second model **on the same provider** does not protect against a
provider-wide outage. The runtime still owes the candidate an honest degraded
state in that case.

## Verification

`python -m pytest -q` → **177 passed, 1 skipped**. New: 32 in
`tests/test_stage12_deepgram.py`, 4 in `tests/test_stage6.py`.

Covered offline, against a real local server speaking the documented protocol:

- query string declares model/encoding/sample_rate/channels/vad_events/
  endpointing/utterance_end_ms; `keyterm` repeated per term; `keywords` absent
- API key in the header, never the URL; never in `public_dict()` or a model dump
- 3 `is_final` segments + 1 `speech_final` → **one** answer, 11 word timings
  preserved in order, averaged confidence, `boundary="speech_final"`
- `UtteranceEnd` closes a turn endpointing missed
- `speech_final` **then** `UtteranceEnd` → still one answer
- interim results → partials with incrementing revisions; revision resets after
  a finalised segment; a mid-answer partial carries the whole answer so far
- client finalise (push-to-talk release) closes the turn; a second release is a
  no-op
- missing key → `SttUnavailable`/`TtsUnavailable`, never a silent empty session
- unreachable provider → raises; provider `Error` recorded in stats
- `close()` cancels receive and keepalive tasks, nulls the socket, is idempotent
- TTS: one `Speak` per sentence, exactly one `Flush`, 3 chunks forwarded as they
  arrive, `sample_rate=24000`/`encoding=linear16` on every chunk,
  `timings_estimated=True`
- `Clear` sent on cancel; a chunk arriving afterwards is dropped, not forwarded
- panel personas → three distinct `aura-2-*` voices; unknown persona falls back
- flush budget refuses the 4th flush in a window of 3 and recovers after 60 s
- audio: exact 48 k→16 k frame count, 440 Hz preserved, odd-byte carry,
  stereo downmix by averaging, `describe()` reports what was done
- config: retired model refused, prod+mock refused, non-PCM encoding refused,
  non-Aura voice refused

## Outstanding — explicitly NOT done

**Live validation (blocked on credentials).** No `DEEPGRAM_API_KEY` or
`GROQ_API_KEY` is present in this environment, so none of the following has been
run. Supply keys via the server environment (never in chat, never committed):

```
DEEPGRAM_API_KEY=...   DEEPGRAM_API_KEY is read server-side only
GROQ_API_KEY=...
APP_ENV=prod ALLOW_MOCK_PROVIDERS=0
```

Then the remaining live checks are:

1. One real browser interview using Deepgram STT and TTS, including a
   multi-sentence answer and a successful interruption.
2. Confirm nova-3 accepts the exact `keyterm` list built from a real Claims File
   without a "keyterm limit exceeded" error.
3. Confirm Aura-2 voice ids resolve and personas sound distinct.
4. Measure speech-end → first audible response, reporting STT endpointing,
   interviewer, TTS and playback contributions separately, with p50/p95, sample
   size, model ids and environment.

**No latency claim is made.** The repo's existing waterfall numbers are
mock-path only and measure framework overhead, not provider time. The ~450 ms
target is **unverified**.

**Implementation not yet done** (tracked in TASK.md):

- wiring these adapters into `server.py` in place of `FakeSpeakPort`, and
  browser mic capture into the authenticated session socket
- client-side playback accounting from the audio clock, queue teardown on
  barge-in, and `scheduled_ms` reporting (the schema field exists; nothing
  populates it yet)
- push-to-talk gating actual capture rather than only interruption handling
- authentication, ownership checks, persistence, post-session evaluation runner
- model-driven interviewer follow-ups; rubric-based evaluation
- language-aware repository exploration beyond README/Python
