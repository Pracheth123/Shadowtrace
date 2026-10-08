# Stage 11 — panel mode, fallbacks, lanes, hardening

Brief: `claude files/stage-11-panel-hardening-lanes.md`. Panel mode is behind
`PANEL_MODE`; it is off unless the server is started with `PANEL_MODE=1` **and**
the client asks for it.

Item 6 of the brief (live coding pack) is **not built** — see
`stage11_live_coding_proposal.md`. It needs explicit approval first.

## What panel mode is

Two or three interviewer **agents**, one per pack panel role, sharing **one**
guard. Concretely: `LiveSession._agents` holds one `LiveAgent` per persona and
hands every one of them the *same* `SessionTools` instance. There is therefore
one coverage list, one depth counter, one claims scope and one time budget for
the whole room. A panel changes *whose voice asks*; it never changes *what may
be asked*.

A pack declares who is in the room (`src/interview/packs/*.yaml`):

```yaml
panel:
  - persona: lead
    label: Lead interviewer
    voice: nova
    competency: ownership
  - persona: peer
    label: Peer engineer
    voice: alloy
    competency: collaboration
  - persona: manager
    label: Hiring manager
    voice: onyx
    competency: reflection
```

The schema caps a panel at three voices and rejects a reused voice: one voice
speaks at a time, so the candidate has to be able to tell who it was.

### Who holds the floor

`Panel.floor_for_state()` is the only place that decides. It reads the shared
guard state — not a model, not a guard *decision* — because the persona has to
be known **before** the agent loop starts: `agent_step`, `tool_call` and
`likely_next` all carry the voice taking the step, and the speculative loop
emits those while the candidate is still talking.

- The next spine question goes to the persona whose competency matches that
  spine item; no match falls to round-robin.
- A probe stays with the persona who opened the spine it hangs off. This needed
  a fix during the build: keying only off `outstanding[0]` attributed a
  follow-up to whoever owned the *next* spine item, which sounded like the room
  changing the subject mid-thought. The caller now passes `probing=`, derived
  from the same pure `propose()` the agent uses.
- Opener and closer are always the lead.

It is deterministic and idempotent for a given state, which is what lets a
speculative draft be spoken by the voice that drafted it — and what lets a
recorded panel session replay to the same persona sequence with no model live
(contract 5).

### One voice at a time

The speak path is already serialised through `_gen_task`, awaited once per turn.
Nothing was added to make the panel take turns; the single speak path *is* the
mechanism. There is no audio mixing (that was a removed design).

### Distinct voices without a schema change

The persona's voice rides `tts_chunk.audio_ref`
(`fake_tts/<voice>/<utterance>/chunk0.pcm`) rather than a new field, so which
voice spoke is readable off any log without touching an event schema in use.

## Event schema — additive only, still version 2

Asked and approved before building. Everything defaults, so a v1 or early-v2
log parses and replays field-for-field unchanged (verified by
`test_a_panel_log_replays_without_a_model_or_tools` and the stage-1 fidelity
tests, which still pass).

| Addition | Where | Default |
| --- | --- | --- |
| `persona` | `agent_step` | `None` |
| `lane` | `session_complete` | `"voice"` |
| `personas` | `session_complete` | `[]` |
| `fallback_used` | new event type | — |

`persona` on `likely_next`, `draft_ready` and `floor_granted` was already
reserved in the stage-1 schema for this stage; stage 11 only fills it.

`assessed` on `DimensionScore` and `lane` on `Report` are evaluation *records*,
not bus events.

## Recorded evidence

`python tools/record_stage11_session.py` — mock path, no API key, no audio
hardware.

```
Recorded fixtures/sessions/stage11_panel/session.jsonl
  personas on agent lines : ['lead', 'peer', 'manager']
  distinct TTS voices     : ['nova', 'alloy', 'onyx']
  spine asked verbatim    : True (4 of 4)

Recorded fixtures/sessions/stage11_text_lane/session.jsonl
  tts_chunk events        : 0 (expected 0 — no audio lane)
  fallback_used           : [('text_lane', 'no audio lane: delivery is not assessed for this session')]

Recorded fixtures/sessions/stage11_fallbacks/session.jsonl
  fallbacks exercised     : ['provider_failover', 'push_to_talk', 'resume_only',
                             'fallback_repo', 'text_lane', 'ws_reconnect']
```

### Panel waterfall

`python tools/waterfall.py fixtures/sessions/stage11_panel/session.jsonl --panel`

```
  Turn stage11_panel-turn-0  [lead]
    last partial  -> endpoint        (endpoint detection):      1.3 ms
    endpoint      -> question_planned (planning):               0.4 ms
    endpoint      -> draft_ready      (TTFT):                   0.4 ms
    draft_ready   -> first tts_chunk  (TTS first chunk):        0.1 ms
    first tts_chunk -> first playback_ack (net+play):           0.2 ms
    TOTAL (endpoint -> first tts_chunk):        0.5 ms
  ...
  Turn stage11_panel-turn-5  [manager]
    TOTAL (endpoint -> first tts_chunk):        0.5 ms

  Session summary (6 measured turns):
    p50 total (endpoint -> first tts_chunk):     0.5 ms
    p95 total (endpoint -> first tts_chunk):     0.5 ms
    budget 1200 ms p50: within
```

**This is the mock path, and it does not discharge the exit criterion.** The
1.2 s p50 budget is about a real TTS vendor and a real model; 0.5 ms is the
cost of the panel *machinery* — the floor decision, the per-persona agent, the
voice selection — which is the part stage 11 added. `GROQ_API_KEY` is unset and
no TTS vendor is configured, so the vendor leg was not run and no sleep was
added to fake it. This carries forward the same open item stage 7 recorded.

What the mock number does establish: panel mode adds no measurable latency over
a single-voice session (stage 7's mock p50 was 0.2 ms with a shorter turn set),
so the 1.2 s budget is a question about the vendor legs, not about the panel.

## Text lane

Same bus, same agent, same guard, same evaluation. The only difference is the
injected `SpeakPort`: `TextLaneSpeakPort` emits nothing. No `tts_chunk` means no
`playback_ack` and nothing that could be truncated, because nothing was heard.

Delivery is **not assessed**, three ways:

1. The delivery evaluator agent does not run. Its whole method is comparing how
   an answer was *said* against this candidate's own first spoken answer, and a
   typed session has no such baseline.
2. `score_findings(findings, "text")` returns delivery with `assessed=False`,
   and the HTML says "not assessed" in words — a `0.00` beside the other three
   would read as the worst score on the report.
3. `LongitudinalStore.record` skips an unassessed dimension entirely, so a
   candidate who types one session gets a gap in their delivery trend, not a
   cliff (contract 9).

Verified identical otherwise:

```
stage11_panel     lane=voice  technical 1.0  structure 1.0  delivery 1.0        competency 0.5
stage11_text_lane lane=text   technical 1.0  structure 1.0  delivery not assessed  competency 0.5
```

The lane defaults from the log's own `session_complete`, so an operator cannot
score a typed session's delivery by forgetting a flag.

## Fallbacks

| Fallback | Where | Logged as |
| --- | --- | --- |
| Provider failover | `llm/client.py` | `provider_failover` |
| Push-to-talk | `server.py` | `push_to_talk` |
| Resume-only intake | `server.py` `/intake` | `resume_only` |
| Fallback repo path | `server.py` `/intake` | `fallback_repo` |
| Text lane | `runtime.start()` | `text_lane` |
| WS reconnect | `server.py` | `ws_reconnect` |

A 429 is still back-off-and-retry on the same model — the limiter owns that. A
*hard* failure (auth, withdrawn model, 5xx, connection error) is different:
retrying the same model cannot help, so the role is retried once on its
configured failover model, then degrades to the local mock rather than dropping
the candidate mid-session.

Failover only happens **before the first token**. Once the candidate has heard
the start of a sentence, switching models mid-stream would splice two different
answers together, so a stream that has already yielded raises instead.

The degraded path is `FakeLlm` — already a first-class part of this repo — so
the outage path is the same code the tests run against, not a second
implementation that only ever runs in an outage.

## Video — display only

`client/src/App.tsx` shows a local `getUserMedia` preview attached to a
`<video>` element and nothing else. No frame is encoded, sent over the socket,
or stored. There is deliberately **no video ingest path on the server**:
unsolicited binary frames are counted and dropped.

Enforced by test, not just by intent:

- no `video` field on `Report`, `DimensionScore`, `Finding`, or any bus event;
- no module under `evaluation/`, `llm/` or `roadmap/` so much as mentions
  `video`, `webcam`, `camera`, `gaze`, `iris` or `emotion`;
- the server's drop is asserted present.

## Hardening

- **Session cap** (`MAX_LIVE_SESSIONS`, default 8). A reconnect re-acquires an
  id it already holds, so a flaky network cannot eat the cap. Over the limit the
  client gets `session_rejected` and a 1013 close.
- **Intake rate limit** (`INTAKE_RPM`, default 5/hour per candidate). Indexing a
  repo is the most expensive thing an unauthenticated caller can ask for. It
  bounds compute; it is not an eligibility gate and never reaches a score.
- **WS reconnect.** The server hands out a single-use resume ticket. A dropped
  socket *parks* the session for the resume window instead of ending it; the
  client reconnects with the token and is re-attached to the same `LiveSession`,
  so there is one log header, one monotonic `seq` sequence and one `turn_id`
  sequence across the drop. Nothing is replayed: an utterance in flight when the
  socket died was never acknowledged, so by the truncation contract it was not
  heard, and the transcript already records only what was.
- **Delete-my-data** (`POST /me/delete`). Returns a manifest — an erasure you
  cannot evidence is not one a candidate should have to trust. The store is read
  *first* (it is the only index from candidate to sessions) and its rows deleted
  *last*, so an interrupted erasure is simply re-runnable.

  Path safety got stricter after a test caught the first design: the containment
  check was vacuous because the paths being deleted were also the declared
  roots. The function now takes *roots* and joins the candidate and session ids
  onto them itself, rejecting any id that is not a single path segment. A caller
  cannot hand it a path to delete. The resolved-path check remains, and now
  earns its place: it catches a root symlinked outside.

Both limits are in-process. One server, one budget; a second process gets its
own. That is stated in `hardening/limits.py` rather than implied — the honest
limit is the one you can point at.

## Contract check

| Contract | Status |
| --- | --- |
| 1 — typed events only | Panel lives in `session/`; the voice reaches TTS through the injected `SpeakPort`, as before |
| 2 — `turn_id` on every event | Unchanged, and continuous across a reconnect |
| 3 — two timestamps | Unchanged |
| 4 — append-only JSONL | One header per session, including across a drop. Erasure deletes a *finished* session's file at the candidate's request; it never rewrites a line |
| 5 — every layer standalone | Panel log replays field-for-field with no model and no tools live |
| 6 — live path depends on nothing downstream | No band-2 → band-3 call added; the panel has no new tool |
| 7 — repo/resume content is data | Unchanged. `fallback_used.detail` is data, and is truncated to 120 chars of the first line |
| 8 — matching never reaches the scorer | Unchanged |
| 9 — scoring fairness | Strengthened: delivery is not assessed in the text lane rather than scored from silence, and an unassessed dimension is not stored as a zero. Video is never a model or score input, by construction and by test |
| 10 — truncation contract | Unchanged, and explicitly honoured by reconnect (nothing is replayed) |

## Bugs this stage's tests caught

Worth recording, because all three were in code that looked right:

1. **Probe attribution.** The floor was keyed off `outstanding[0]`, so a
   follow-up to the lead's question was spoken by whoever owned the *next*
   spine item. Fixed by passing `probing=`, derived from the same pure
   `propose()` the agent uses.
2. **A leaked bus subscription on reconnect.** `EventBus` has no unsubscribe,
   and each socket was subscribing its own `tts_chunk` handler. After a
   reconnect the old handler was still live and writing to a dead socket, which
   blocked `bus.drain()` and wedged the session that had just been resumed.
   Fixed with `SocketHolder`: one subscription, made once, repointed at the new
   socket.
3. **A drop read as a clean exit.** A disconnect arrives two ways — as a
   `websocket.disconnect` *message* and as a `WebSocketDisconnect` *exception*.
   Only the exception was parking the session; the message path closed it out,
   so the reconnect resumed a finished session and answered nothing. Both paths
   now park.

The first was caught by a unit test, the second and third only by driving a real
WebSocket. That is why `test_stage11_server.py` exists: the composition root is
where the layer contracts actually meet, and a layer-level test cannot see it.

## Tests

- `tests/test_stage11.py` — 36 tests over panel, lanes, fallbacks, video and
  hardening. One skipped: the symlink case needs elevated privileges on Windows.
- `tests/test_stage11_server.py` — 12 tests driving the composition root through
  a real WebSocket: the panel flag both ways, the lane switch, the session cap,
  reconnect onto the same log, a spent token, dropped binary frames,
  push-to-talk gating, the intake rate limit and delete-my-data over HTTP.

Full suite: **142 passed, 1 skipped**.

## Open

- **Live-vendor panel waterfall.** The 1.2 s p50 exit criterion still needs a
  real TTS vendor and a real model. Carried forward from stage 7; the same
  `GROQ_API_KEY` gap blocks both.
