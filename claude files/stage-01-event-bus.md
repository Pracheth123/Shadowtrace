# Stage 1 — Event bus, log and waterfall

Build stage 1 of the AI mock interview platform. Read `CLAUDE.md` first — it holds the
architecture, the contracts and how we work.

**This stage has no audio, no LLM, no HTTP and no UI.** It is the plumbing every later stage runs
on, and it has to be right before anything else exists. Resist adding anything from stage 2.

## Build

**1. `src/interview/events/schema.py`** — Pydantic v2 models for every event, as a discriminated
union on a `type` field. The vocabulary:

`speech_start`, `partial`, `endpoint`, `final_transcript`, `signals`, `likely_next`, `draft_ready`,
`floor_granted`, `route_decision`, `tts_chunk`, `playback_ack`, `barge_in`, `truncate`,
`question_planned`, `coverage_update`, `intensity_change`, `session_complete`

Every event carries `session_id`, `turn_id`, `seq`, `t_emit` (monotonic float seconds, server
clock) and `producer`. Audio-bearing events also carry `t_audio` (position in the audio stream).
Per-event fields, at minimum:

| Event | Fields beyond the common ones |
| --- | --- |
| `partial` | `text`, `stable_until_ms`, `revision` |
| `endpoint` | `confidence` |
| `final_transcript` | `text`, `word_timings` |
| `signals` | `claim_hits`, `pause_stats`, `silence_ms`, `distress` |
| `likely_next` | `signal_snapshot` |
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

Where this table is silent, pick something minimal and tell me what you picked.

**2. `src/interview/events/bus.py`** — an asyncio pub/sub bus. Publishers emit; subscribers register
by event type or by predicate. No subscriber blocks a publisher. The bus assigns `seq` monotonically
per session and stamps `t_emit` if the producer did not.

**3. `src/interview/events/log.py`** — an append-only JSONL writer that subscribes to everything and
writes one event per line, plus a reader that yields them back as typed models. Write and read must
round-trip byte-identically.

**4. `tools/replay.py`** — reads a `.jsonl` session log and re-emits every event through a fresh bus,
in order. Two modes: `--fast` (as quickly as possible) and `--realtime` (paced by the original
`t_emit` deltas). Takes `--out` to write the resulting log. This is how every later stage gets tested
with nothing else live, so it is more important than it looks.

**5. `tools/waterfall.py`** — reads a log and prints a per-turn latency breakdown, plus p50 and p95
across the session. Per turn, report at least:

- last `partial` → `endpoint` (endpoint detection)
- `endpoint` → `question_planned` (planning)
- `endpoint` → `draft_ready` (time to first token)
- `draft_ready` → first `tts_chunk` (TTS first chunk)
- first `tts_chunk` → first `playback_ack` (network and playback)
- **total: `endpoint` → first `tts_chunk`**

Flag any turn whose total exceeds 450 ms.

**6. `fixtures/sessions/fake_session.jsonl`** — a hand-written session of six to eight turns that
exercises the whole vocabulary, including one barge-in followed by a `truncate`, and one turn where
`route_decision` is `unclear`. **Write this by hand**, not by running the code — it is the
specification of the format, and if the code generates it then the code is marking its own homework.

**7. Tests** — round-trip through the log; bus ordering with concurrent publishers; replay of the
fixture produces a log identical to the fixture; waterfall numbers match values you compute by hand
from the fixture.

## Exit criterion

```
python tools/replay.py fixtures/sessions/fake_session.jsonl --fast --out /tmp/replayed.jsonl
python tools/waterfall.py /tmp/replayed.jsonl
```

prints a correct per-turn waterfall, and `/tmp/replayed.jsonl` is identical to the fixture. Paste
the actual output into your summary.

## Before you write any code

Reply with the full event field list you intend to use, and anything above you think is wrong or
underspecified. **Do not start implementing until I've agreed to the schema.** Every later stage
rides on these events, and the schema is the one thing in this project that is genuinely expensive
to change later.
