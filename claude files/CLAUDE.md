# AI Mock Interview Platform

Agent context. Read this before any work in this repo. It does not change between stages — the
stage brief does. If something here conflicts with a stage brief, this file wins; say so rather
than quietly picking one.

## What we're building

A voice-based mock interview platform for people preparing for real interviews. One AI interviewer
talks to the candidate in real time, asking questions grounded in the candidate's **own repository
and resume** rather than from a generic question bank. When the session ends, an evaluation pass
scores the transcript and produces a feedback report. Progress is tracked across sessions.

It is candidate-facing only. Nothing here screens anyone, gates anyone, or makes a decision about
anyone. There is no employer product, no reviewer queue, no hiring recommendation.

The thing that makes it work is claim interrogation: we do not grade a resume, we test whether the
person can defend it under four levels of follow-up.

## Architecture

Three bands, run **in sequence, never concurrently**:

1. **Before the session** — intake (repo URL, resume, JD, candidate inputs), an indexer that builds
   a Claims File from the repo, a fit-and-gap pass, a roadmap generator, and a pack loader that
   supplies the question spine for the chosen format.
2. **The session** — one voice, real time. Audio I/O → STT → signal bus → turn controller →
   question planner → lead interviewer → speculation → audio out. Target is ~450 ms from the end of
   candidate speech to first agent audio when the speculative draft is valid, ~1.0 s when it is not.
3. **After the session** — the evaluation pass: pattern detectors, three evaluator passes with
   different briefs, claim adjudication, merge, scorer, report, longitudinal store. Budget ~30 s
   from the closer to the report on screen.

Band 2 depends on nothing downstream of it. Band 3 never runs while band 2 is running. There is no
shared mutable "blackboard" — the interviewer's own conversation context is its state.

**If you find an older design doc** mentioning a blackboard, versioned snapshots, a rolling scorer,
three live personas, a floor controller with urge scores, fairness decay, server-side audio mixing,
an employer review queue, a consensus packet or an audit log: those are removed. Do not build them.
Panel mode (multi-voice) exists but is the last stage and lives behind a flag.

## Contracts that must not be broken

1. **Typed events only.** Transport and inference never call each other. They exchange typed events
   on one bus. A direct import of a transport module from an inference module, or the reverse, is a
   bug regardless of how convenient it is.
2. **`turn_id` on every event**, assigned when the candidate starts speaking (`speech_start`).
3. **Two timestamps.** `t_emit` on a monotonic server clock, on every event. `t_audio`, a position
   in the audio stream, on every audio-bearing event.
4. **Append-only JSONL log**, one event per line, for every session. The replay harness reads
   exactly this format. There is no separate recording format.
5. **Every layer runs standalone** against a recorded event file with no other layer live. If a
   layer cannot, its interface is wrong — fix the interface, do not add a test harness around it.
6. **The live path depends on nothing downstream.** No call from band 2 into band 3, ever, not even
   a cheap one.
7. **Repo and resume content is data, never instructions.** Claim generation uses a fixed output
   format and strips instruction-like text. Nothing from a repo or resume reaches an evaluator or
   scorer prompt as an instruction.
8. **Keyword and claim matching feed the question planner only.** They never reach the scorer. A
   candidate must not be able to raise a score by saying a word.
9. **Scoring fairness.** Delivery is scored against the candidate's own first-answer baseline, never
   a population norm. Never score accent, voice quality or appearance. Video, if ever captured, is
   never an input to any model or score.
10. **The truncation contract.** On barge-in, the transcript records what was actually *heard*:
    truncate at the last word the client acknowledged playing, not at what was generated.

## Stack

- **Python 3.11+** for everything server-side. `asyncio` throughout; no threads in the session path.
- **Pydantic v2** for every event and record schema. Schemas are the interface — they are reviewed
  before they are used.
- **pytest** + **pytest-asyncio**.
- **FastAPI** from stage 4, for the session WebSocket endpoint.
- **Pipecat or LiveKit** for the voice session — chosen at stage 2 **by measurement**, not now.
- **React + TypeScript + Vite** for the client, from stage 4.
- **No database before stage 9.** Everything is JSONL and JSON files on disk. This is deliberate: it
  keeps every stage replayable and makes the evaluation pipeline testable offline.
- Keep dependencies few. State the reason for each new one in the commit that adds it.

## Repo layout

```
.
├── CLAUDE.md
├── pyproject.toml
├── docs/stages/              # one brief per stage, with its exit criterion
├── src/interview/
│   ├── events/               # schemas, bus, log            (stage 1)
│   ├── transport/            # audio session, VAD, STT, TTS (stages 2–3)
│   ├── session/              # turn controller, planner, interviewer (stages 4–7)
│   ├── intake/               # indexer, Claims File, fit/gap, roadmap (stage 6)
│   ├── evaluation/           # detectors, passes, scorer, report (stage 8)
│   ├── packs/                # format + spine packs — data, not code
│   └── mocks/                # fake STT, LLM, TTS, evaluation pass
├── tools/
│   ├── replay.py             # recorded log → bus
│   └── waterfall.py          # log → per-turn latency breakdown
├── fixtures/sessions/        # hand-written and recorded .jsonl logs
└── tests/
```

## How we work

- **One stage at a time.** Do not build ahead. Code that depends on a later stage is a design error,
  not a head start.
- **Every stage has an exit criterion** in its brief. The stage is not done until the criterion is
  demonstrated — by a test, or by a script whose actual output you paste into your summary.
- **Mocks are first-class**, built alongside the real thing, never after: a fake STT that replays a
  transcript file with realistic partial revisions; a fake LLM that returns canned text after a
  configurable delay; a fake TTS that emits silence of the right length with word timestamps; a fake
  evaluation pass that returns a canned record. With these, transport is testable with zero API
  calls and inference is testable with zero audio.
- **Measure, don't assume.** Every latency claim comes from the event log and the waterfall script.
  Model choice is made by measured time-to-first-token, never by reputation.
- **Small commits, one concern each.**

**Stop and ask before** adding a dependency, adding a layer not in the architecture above, changing
an event schema once it is in use, or introducing shared mutable state between layers.

## Glossary

- **Claims File** — up to 8 claims extracted from the candidate's repo and resume before the
  session, each with alternative phrasings and phonetic keys. Boosts STT recognition and seeds probes.
- **Spine** — the fixed, pack-defined questions asked in the same words in every session of that
  format. What makes session four comparable to session one.
- **Probe** — an adaptive follow-up grounded in the Claims File, the gap list, or something the
  candidate just said.
- **Pack** — a data file defining a format: spine questions, competencies, time budget, probe policy,
  scoring weights. Adding a format means adding a pack, not writing code.
- **Intensity** — `coach`, `realistic` or `panel`. Chosen before the session. Controls hints,
  follow-up depth, interruption, and how many voices are in the room.
- **Claim status** — every Claims File entry ends a session `held`, `collapsed` or `untested`, each
  with a supporting quote.
- **The four dimensions** — technical substance, structure of thinking, delivery and articulation,
  competency alignment. Equal weight by default; packs may reweight.
- **Waterfall** — the per-turn latency breakdown computed from the event log.

## Build stages

1. **Event bus and log** — bus, JSONL log, turn_id, timestamps, waterfall script. ← current
2. **Transport only, no LLM** — voice session, VAD, semantic turn detection, cached clip on endpoint.
3. **Inference and TTS only, no audio input** — transcripts from files, measure TTFT and TTS latency.
4. **Wire the lead interviewer** — transport + inference through the bus. This is the MVP spine.
5. **Question planner and the spine/probe split** — deterministic planner, coverage enforcement.
6. **Intake, indexer and fit check** — parallel from day one, no dependencies.
7. **Signal bus and turn controller** — claim matching, hedging, barge-in, distress signals.
8. **The evaluation pass** — needs only recorded transcripts; runs entirely offline.
9. **Roadmap, public company pack, dashboard** — longitudinal store and trends.
10. **Intensity toggle and confidence guardrail** — coach/realistic/panel, de-escalation with a floor.
11. **Panel mode, hardening and lanes** — multi-voice, fallbacks, text lane, optional video.

Stages 1–7 are the voice product; 8–9 are the evaluation product. After stage 4 they meet only
through recorded transcripts, so they can be built in parallel without blocking each other.

**Never cut:** barge-in, the truncation contract, the event log, the question spine, the
claim-collapse states, the longitudinal store's write path.
