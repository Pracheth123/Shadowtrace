# Stage 1 — Event bus, log and waterfall

Build stage 1 of the AI mock interview platform. Read `CLAUDE.md` first — it holds the
architecture, the contracts and how we work.

**This stage has no audio, no LLM, no HTTP and no UI.** It is the plumbing every later stage runs
on, and it has to be right before anything else exists. Resist adding anything from stage 2.

## Build

**1. `src/interview/events/schema.py`** — Pydantic v2 models for every event, as a discriminated
union on a `type` field. The vocabulary:

`speech_start`, `partial`, `endpoint`, `final_transcript`, `signals`, `likely_next`,
`agent_step`, `tool_call`, `tool_result`, `guard_override`,
`draft_ready`, `floor_granted`, `route_decision`, `tts_chunk`, `playback_ack`, `barge_in`,
`truncate`, `question_planned`, `coverage_update`, `intensity_change`, `session_complete`

`schema_version` defaults to **2**. Additive agent events are v2. The v1 fixture
(`fixtures/sessions/fake_session.jsonl`) must still parse and replay byte-identically.

Every event carries `session_id`, `turn_id`, `seq`, `t_emit` and `producer`. Audio-bearing events
also carry `t_audio_in` / `t_audio_out`. Agent events also carry `step_index`.

| Event | Fields beyond the common ones |
| --- | --- |
| `partial` | `text`, `stable_until_ms`, `revision` |
| `endpoint` | `confidence` |
| `final_transcript` | `text`, `word_timings` |
| `signals` | `claim_hits`, `pause_stats`, `silence_ms`, `distress` |
| `likely_next` | `signal_snapshot` |
| `agent_step` | `step_index`, `band`, `phase`, `summary`, `cancelled` |
| `tool_call` | `step_index`, `tool`, `args` |
| `tool_result` | `step_index`, `tool`, `ok`, `result`, `latency_ms` |
| `guard_override` | `step_index`, `rule`, `agent_intent`, `enforced_action` |
| `draft_ready` | `first_sentence`, `variant` (`plain` or `concession`) |
| `route_decision` | `decision` (`defended` / `conceded` / `unclear`) |
| `tts_chunk` | `audio_ref`, `word_timestamps` |
| `playback_ack` | `played_ms` |
| `barge_in` | `confidence` |
| `truncate` | `last_heard_word`, `word_index` |
| `question_planned` | `kind` (`spine` or `probe`), `competency`, `target_depth` |
| `coverage_update` | `covered`, `outstanding` |
| `intensity_change` | `direction`, `signal`, `from_level`, `to_level` |
| `session_complete` | `transcript_path`, `log_path`, `pack_id`, `intensity_history` |

**2. `src/interview/events/bus.py`** — asyncio pub/sub. Bus assigns `seq` and stamps `t_emit`.

**3. `src/interview/events/log.py`** — append-only JSONL writer + typed reader. Round-trip
byte-identically for a given schema_version.

**4. `tools/replay.py`** — re-emits every event through a fresh bus. Recorded `tool_result` and
`agent_step` / `guard_override` are replayed as-is — no model, no tool side effects (contract 5).

**5. `tools/waterfall.py`** — per-turn latency breakdown + p50/p95. Report at least:

- last `partial` → `endpoint`
- `endpoint` → `question_planned` (guard-approved move; may follow agent steps)
- `endpoint` → `draft_ready`
- `draft_ready` → first `tts_chunk`
- first `tts_chunk` → first `playback_ack`
- **total: `endpoint` → first `tts_chunk`**

Flag any turn whose total exceeds 450 ms.

**6. Fixtures (hand-written)**

- `fixtures/sessions/fake_session.jsonl` — v1 vocabulary, 6–8 turns, barge-in+truncate, `unclear`.
- `fixtures/sessions/fake_session_v2.jsonl` — at least one full agent turn: `agent_step`s,
  `tool_call`/`tool_result`, one `guard_override`, and a barge-in that cancels an agent step
  (`agent_step.phase=cancelled`).

**7. Tests** — v1 round-trip + replay identity; v2 fixture parses and replays; bus ordering;
waterfall hand-check on v1.

## Exit criterion

```
python tools/replay.py fixtures/sessions/fake_session.jsonl --fast --out /tmp/replayed.jsonl
python tools/waterfall.py /tmp/replayed.jsonl
python tools/replay.py fixtures/sessions/fake_session_v2.jsonl --fast --out /tmp/replayed_v2.jsonl
```

v1 replayed log identical to fixture; v2 parses and replays. Paste waterfall output.
