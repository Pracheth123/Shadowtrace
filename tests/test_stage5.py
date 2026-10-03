"""
Stage 5 tests — guard and the spine/probe split.

1. Packs validate on load (two starter packs).
2. Guard table: spine order, verbatim, time budget, depth cap, claims scope.
3. Every override is a guard_override in the log (no silent coercion).
4. Two sessions of the same pack speak identical spine text.
5. Replay of recorded tool_results needs no live tools.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from interview.events.bus import EventBus
from interview.events.log import EventLogger, read_events
from interview.packs.model import PackLoadError, load_pack
from interview.session.guard import GuardState, Intent, approved_sequence, evaluate
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort
from interview.session.tools import SessionTools, load_claims_fixture

ROOT = Path(__file__).parent.parent
PACK = "behavioral-core"


def _pack():
    return load_pack(PACK)


def _state(**overrides) -> GuardState:
    pack = _pack()
    base = GuardState(
        pack=pack,
        intensity="realistic",
        covered=(),
        outstanding=tuple(pack.spine_ids()),
        depth_on_current=0,
        time_remaining_s=900,
        claim_ids=frozenset({"c-kafka", "c-rollback"}),
        claim_competency={"c-kafka": "systems", "c-rollback": "ownership"},
        transcript_texts=("I built a Kafka pipeline for payments.",),
    )
    data = {
        "pack": base.pack,
        "intensity": base.intensity,
        "covered": base.covered,
        "outstanding": base.outstanding,
        "depth_on_current": base.depth_on_current,
        "time_remaining_s": base.time_remaining_s,
        "claim_ids": base.claim_ids,
        "claim_competency": base.claim_competency,
        "transcript_texts": base.transcript_texts,
    }
    data.update(overrides)
    return GuardState(**data)


def _after_first_spine() -> GuardState:
    pack = _pack()
    ids = pack.spine_ids()
    return _state(covered=(ids[0],), outstanding=tuple(ids[1:]), depth_on_current=0)


@pytest.mark.parametrize("pack_id", ["behavioral-core", "systems-design"])
def test_starter_packs_validate(pack_id: str) -> None:
    pack = load_pack(pack_id)
    assert pack.pack_id == pack_id
    assert len(pack.spine) >= 2
    assert pack.spine[0].text.endswith(".")


def test_pack_rejects_bad_weights(tmp_path: Path) -> None:
    raw = yaml.safe_load((ROOT / "src" / "interview" / "packs" / "behavioral-core.yaml").read_text())
    raw["scoring_weights"]["technical"] = 0.9
    path = tmp_path / "behavioral-core.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(PackLoadError):
        load_pack("behavioral-core", directory=tmp_path)


@pytest.mark.parametrize(
    ("intent", "state", "rule", "kind", "spine_id", "depth"),
    [
        (
            Intent(action="ask_spine", spine_id="disagree-teammate"),
            None,
            "spine_order",
            "spine",
            "own-project",
            0,
        ),
        (
            Intent(
                action="ask_spine",
                spine_id="own-project",
                proposed_text="What is your greatest weakness?",
            ),
            None,
            "spine_verbatim",
            "spine",
            "own-project",
            0,
        ),
        (
            Intent(action="ask_spine", spine_id="own-project"),
            None,
            None,
            "spine",
            "own-project",
            0,
        ),
        (
            Intent(action="probe", claim_id="c-kafka", target_depth=1),
            "after",
            "time_budget",
            "spine",
            "disagree-teammate",
            0,
        ),
        (
            Intent(action="probe", claim_id="c-kafka", target_depth=5),
            "after",
            "probe_depth",
            "probe",
            "own-project",
            2,
        ),
        (
            Intent(action="probe", claim_id="c-kafka", target_depth=2),
            "coach",
            "probe_depth",
            "probe",
            "own-project",
            1,
        ),
        (
            Intent(action="probe", claim_id="not-a-claim", target_depth=1),
            "after",
            "claims_scope",
            "spine",
            "disagree-teammate",
            0,
        ),
        (
            Intent(action="probe", transcript_anchor="Kafka pipeline", target_depth=1),
            "after",
            None,
            "probe",
            "own-project",
            1,
        ),
        (
            Intent(action="end_session"),
            None,
            "spine_order",
            "spine",
            "own-project",
            0,
        ),
    ],
)
def test_guard_table(intent, state, rule, kind, spine_id, depth) -> None:
    if state == "after":
        guard_state = _after_first_spine()
    elif state == "coach":
        guard_state = _after_first_spine()
        guard_state = GuardState(
            pack=guard_state.pack,
            intensity="coach",
            covered=guard_state.covered,
            outstanding=guard_state.outstanding,
            depth_on_current=0,
            time_remaining_s=900,
            claim_ids=guard_state.claim_ids,
            claim_competency=guard_state.claim_competency,
            transcript_texts=guard_state.transcript_texts,
        )
    else:
        guard_state = _state()
    if rule == "time_budget":
        guard_state = GuardState(
            pack=guard_state.pack,
            intensity=guard_state.intensity,
            covered=guard_state.covered,
            outstanding=guard_state.outstanding,
            depth_on_current=guard_state.depth_on_current,
            time_remaining_s=10,
            claim_ids=guard_state.claim_ids,
            claim_competency=guard_state.claim_competency,
            transcript_texts=guard_state.transcript_texts,
        )
    decision = evaluate(intent, guard_state)
    assert decision.rule == rule
    assert decision.accepted is (rule is None)
    assert decision.kind == kind
    assert decision.spine_id == spine_id
    assert decision.target_depth == depth
    if kind == "spine":
        assert decision.text == guard_state.pack.spine_item(spine_id).text


@pytest.mark.asyncio
async def test_tools_are_local_and_fast() -> None:
    pack = _pack()
    tools = SessionTools(pack, load_claims_fixture(), intensity="realistic")
    bus = EventBus()
    for name, args in (
        ("get_claims", {}),
        ("get_claim", {"id": "c-kafka"}),
        ("get_coverage", {}),
        ("get_time_remaining", {}),
        ("get_candidate_signals", {}),
        ("note_claim_status", {"id": "c-kafka", "status": "held", "quote": "two million"}),
        ("end_session", {}),
    ):
        await tools.call(
            bus,
            session_id="s",
            turn_id="t",
            step_index=0,
            name=name,
            args=args,
        )
    await bus.drain()

    bus2 = EventBus()
    seen: list = []
    bus2.subscribe("tool_result", lambda e: seen.append(e))
    await tools.call(
        bus2,
        session_id="s",
        turn_id="t",
        step_index=1,
        name="ask_spine",
        args={"id": "own-project"},
    )
    await bus2.drain()
    assert seen and seen[0].ok
    assert seen[0].latency_ms < 20
    assert seen[0].result["text"] == pack.spine_item("own-project").text
    assert tools.notes["c-kafka"]["status"] == "held"


async def _run_session(
    tmp_path: Path,
    name: str,
    answers: list[str],
    *,
    scripted: dict[int, Intent] | None = None,
    max_tool_calls: int | None = None,
    pack_id: str = PACK,
) -> Path:
    log_path = tmp_path / name / "session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, name)
    live = LiveSession(
        bus=bus,
        session_id=name,
        log_path=str(log_path),
        transcript_path=str(tmp_path / name / "transcript.json"),
        speak=FakeSpeakPort(bus, name),
        config=SessionConfig(
            max_turns=12,
            max_minutes=30,
            pack_id=pack_id,
            use_mock_llm=True,
            scripted_intents=scripted,
            max_tool_calls=max_tool_calls,
        ),
    )
    live.attach()
    await live.start()
    await live.wait_idle()
    for i, text in enumerate(answers):
        if live._closed:
            break
        await live.ingest_final(text, turn_id=f"{name}-turn-{i}")
    await live.end(reason="client")
    await bus.drain()
    await asyncio.sleep(0)
    await bus.drain()
    await logger.close()
    return log_path


def _spine_texts(path: Path) -> list[str]:
    texts = []
    for event in read_events(path):
        if event.type == "tool_result" and event.tool == "ask_spine" and event.ok:
            texts.append(event.result["text"])
    return texts


@pytest.mark.asyncio
async def test_two_sessions_identical_spine_text(tmp_path: Path) -> None:
    answers_a = [
        "I owned the payments pipeline from design through launch.",
        "We shipped it.",
        "I disagreed with the on-call rotation.",
        "We changed it.",
        "I decided without the full trace.",
        "I missed a rollback window and changed the checklist.",
    ]
    answers_b = [
        "Different story about a migration I owned.",
        "It landed.",
        "A teammate and I argued about the schema.",
        "We wrote the decision down.",
        "The data was incomplete and I still chose.",
        "A bad cutover taught me to rehearse.",
    ]
    log_a = await _run_session(tmp_path, "sess-a", answers_a)
    log_b = await _run_session(tmp_path, "sess-b", answers_b)
    pack = _pack()
    expected = [item.text for item in pack.spine]
    assert _spine_texts(log_a) == expected
    assert _spine_texts(log_b) == expected
    assert _spine_texts(log_a) == _spine_texts(log_b)


@pytest.mark.asyncio
async def test_overrides_are_logged(tmp_path: Path) -> None:
    scripted = {
        0: Intent(
            action="ask_spine",
            spine_id="missed-mark",
            proposed_text="Tell me a joke.",
        ),
        1: Intent(action="probe", claim_id="not-a-claim", target_depth=1),
    }
    answers = [
        "I owned the launch.",
        "Something off the resume.",
        "We disagreed.",
        "Then we aligned.",
        "I chose with gaps.",
        "I missed and I changed the review.",
    ]
    log_path = await _run_session(tmp_path, "sess-override", answers, scripted=scripted)
    events = list(read_events(log_path))
    overrides = [e for e in events if e.type == "guard_override"]
    rules = [e.rule for e in overrides]
    assert "spine_order" in rules
    assert "claims_scope" in rules
    for event in overrides:
        assert event.agent_intent
        assert event.enforced_action
        assert event.rule
    # Enforced spine text is the pack line, not the joke.
    spoken = _spine_texts(log_path)
    assert spoken[0] == _pack().spine[0].text
    assert "joke" not in " ".join(spoken)
    covered = []
    for event in events:
        if event.type == "coverage_update":
            covered = event.covered
    assert set(covered) == set(_pack().spine_ids())


@pytest.mark.asyncio
async def test_replay_uses_recorded_tool_results_only(tmp_path: Path, monkeypatch) -> None:
    answers = [f"Answer number {i} about the work." for i in range(6)]
    log_path = await _run_session(tmp_path, "sess-replay", answers)
    events = list(read_events(log_path))
    first = approved_sequence(events)
    assert [row["text"] for row in first if row["kind"] == "spine"] == [
        item.text for item in _pack().spine
    ]

    def explode(*_a, **_k):
        raise AssertionError("live tools must not run during replay")

    monkeypatch.setattr(SessionTools, "call", explode)
    monkeypatch.setattr(SessionTools, "__init__", explode)

    bus = EventBus()
    planned: list[str] = []

    async def on_planned(event) -> None:
        if event.type == "question_planned":
            planned.append(event.kind)

    bus.subscribe("question_planned", on_planned)
    for event in events:
        await bus.emit(event)
    await bus.drain()

    again = approved_sequence(events)
    assert again == first
    assert planned == [row["kind"] for row in first]
    assert any(e.type == "tool_result" and e.tool == "ask_spine" for e in events)


@pytest.mark.asyncio
async def test_step_limit_override(tmp_path: Path) -> None:
    log_path = await _run_session(
        tmp_path,
        "sess-limit",
        ["I owned a migration."],
        max_tool_calls=1,
    )
    events = list(read_events(log_path))
    limits = [e for e in events if e.type == "guard_override" and e.rule == "step_limit"]
    assert limits
    planned = [e for e in events if e.type == "question_planned"]
    assert planned and planned[0].kind == "spine"
    assert _spine_texts(log_path)[0] == _pack().spine[0].text
