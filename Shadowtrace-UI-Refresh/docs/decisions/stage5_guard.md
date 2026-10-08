# Stage 5 — Guard replay and live session

**Date:** 2026-10-03  
**Method:** offline sessions via `tools/record_stage5_session.py`  
(pack `behavioral-core`, claims fixture, fake TTS, no network).

## Replay — approved sequence

`python tools/replay_guard.py fixtures/sessions/stage5_sess_a/session.jsonl fixtures/sessions/stage5_sess_b/session.jsonl`

Both sessions, identical spine text:

```
1. spine own-project depth=0 | Tell me about a time you owned a project from start to finish.
2. probe competency=systems depth=1
3. spine disagree-teammate depth=0 | Tell me about a time you disagreed with a teammate and what you did.
4. probe competency=ownership depth=1
5. spine incomplete-info depth=0 | Tell me about a time you made a decision with incomplete information.
6. spine missed-mark depth=0 | Tell me about a time you missed the mark and what you changed afterward.
```

Replay reads `question_planned` and recorded `ask_spine` tool results only.

## Live session

Log: `fixtures/sessions/stage5_sess_live/session.jsonl`

The agent proposed `ask_spine:missed-mark` ("Tell me a joke.") and `probe:not-a-claim`. The guard overrode both and still covered every spine id.

```
guard_override rule=spine_order agent_intent=ask_spine:missed-mark enforced_action=ask_spine:own-project
question_planned kind=spine competency=ownership target_depth=0
coverage_update covered=["own-project"] outstanding=["disagree-teammate","incomplete-info","missed-mark"]

guard_override rule=claims_scope agent_intent=probe:not-a-claim:depth=1 enforced_action=ask_spine:disagree-teammate
question_planned kind=spine competency=collaboration target_depth=0
coverage_update covered=["own-project","disagree-teammate"] outstanding=["incomplete-info","missed-mark"]

question_planned kind=probe competency=systems target_depth=1
question_planned kind=spine competency=ambiguity target_depth=0
question_planned kind=probe competency=ownership target_depth=1
question_planned kind=spine competency=reflection target_depth=0
coverage_update covered=["own-project","disagree-teammate","incomplete-info","missed-mark"] outstanding=[]
```

Each of those turns also has `agent_step` lines (`observe` → tool `act`s → `think` → `speak`). Full lines are in the log from `python tools/record_stage5_session.py`.

Spine ids covered: `own-project`, `disagree-teammate`, `incomplete-info`, `missed-mark`.
