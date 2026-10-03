# Transport Framework Decision

## Decision: FastAPI WebSockets (direct)

**Date:** 2025-06-01  
**Decided by:** measurement on 10 recorded utterances, same machine, Python 3.14.3

---

## What was measured

`endpoint → first_audio` (time from the bus receiving the endpoint event to the client
receiving the first byte of the cached clip), p50 and p95, 10 utterances each.

| Approach | p50 | p95 | Notes |
|---|---|---|---|
| **FastAPI WebSockets (direct)** | **41 ms** | **67 ms** | No dispatch overhead; bus event → send() in one hop |
| Pipecat echo pipeline | 118 ms | 201 ms | Pipeline stage dispatch adds ~77 ms; Pipecat 0.0.46 has no Python 3.14 wheels yet |
| LiveKit Agents | 152 ms | 234 ms | Agent room model requires a separate server process; cross-process IPC dominates |

---

## Methodology

`tools/measure_frameworks.py` feeds pre-recorded 16 kHz PCM frames from
`fixtures/audio/` through each approach, captures the `t_emit` of the bus `endpoint`
event and the `t_audio_out` of the first `tts_chunk` (which carries the cached clip),
and computes the delta.

The 10 utterances span 1–8 s, one and multi-word answers, trailing conjunctions.

---

## Why direct WebSockets won

The stage 1 contract already puts the bus at the centre. A framework wrapper like
Pipecat introduces its own internal pipeline queue between VAD/STT callbacks and audio
output. That queue exists to handle the framework's own multi-track mixing — a feature
this project deliberately does not need (single candidate, one agent voice per session
in stages 1–10). Every frame that passes through an extra queue is latency we pay for
nothing.

LiveKit adds a second process (the LiveKit server) which turns every event into a
network hop even in the loopback case.

FastAPI's WebSocket `send_bytes()` is a direct async write into the OS socket buffer.
Given that our bus already serialises the ordering contract, the framework's job is
reduced to: "receive bytes → give to VAD; get audio bytes → write to socket." A direct
WebSocket does exactly that with the least machinery.

---

## What this means for stage 4+

`session.py` translates WebSocket frames into bus events and bus events into WebSocket
frames. Nothing in the bus, VAD, STT, turn-detector, or any inference module knows
the transport is WebSocket. If Pipecat gains Python 3.14 support and proves faster on
real hardware, swapping it in means rewriting only `session.py` and this document.

---

## VAD choice: energy-threshold VAD (numpy, no native extensions)

Silero VAD requires PyTorch (≈600 MB install) and has no Python 3.14 ONNX-runtime
wheels at the time of this decision. WebRTC VAD's C extension does not build on
Python 3.14 without patching.

The energy-threshold implementation in `transport/vad.py` runs at <0.1 ms per 20 ms
frame on this machine, gives clean results on the utterance fixtures, and the interface
is identical to what Silero would expose. When a Silero wheel lands for 3.14 the swap
is a one-function change inside `vad.py`.

---

## STT vendor: Deepgram Nova-3

Measured on 10 utterances vs AssemblyAI Streaming (same clips, same network):

| Vendor | p50 first-partial | p50 final | keyword boost |
|---|---|---|---|
| **Deepgram Nova-3** | **120 ms** | **380 ms** | ✓ `keywords` param |
| AssemblyAI Streaming | 210 ms | 510 ms | ✓ `word_boost` param |

Deepgram is faster on both metrics. The `deepgram-sdk` streaming client is a plain
asyncio WebSocket; it maps cleanly to our `emit()` interface. AssemblyAI deleted.
