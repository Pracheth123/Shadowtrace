"""
Stage 11 — panel mode, fallbacks, lanes, video, hardening.

1. Panel: 2-3 agents share one guard. The spine is still asked verbatim in pack
   order, personas are filled on the agent events, voices are distinct, and one
   voice speaks at a time.
2. One evaluation per panel session, not one per persona.
3. Text lane: same bus, agent, guard and evaluation; delivery not assessed, and
   substance scores identically to the voice lane.
4. Fallbacks: each one exercised once and visible in the log.
5. Video: no ingest path, and video cannot reach a model, score or report.
6. Hardening: session cap, intake rate limit, WS reconnect, delete-my-data.
7. Replay: a panel log replays field-for-field with no model and no tools live.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from interview.events.bus import EventBus
from interview.events.log import EventLogger, read_events
from interview.evaluation.pipeline import evaluate
from interview.evaluation.schema import DimensionScore, Finding, Report
from interview.evaluation.score import score_findings
from interview.hardening.erasure import UnsafeIdentifier, delete_candidate_data
from interview.hardening.limits import (
    RateLimit,
    RateLimiter,
    SessionCap,
    SessionCapExceeded,
)
from interview.hardening.reconnect import ReconnectRegistry
from interview.packs.model import Pack, load_pack
from interview.roadmap.store import LongitudinalStore
from interview.session.guard import GuardState
from interview.session.panel import Panel
from interview.session.runtime import LiveSession, SessionConfig
from interview.session.speak import FakeSpeakPort, TextLaneSpeakPort

ROOT = Path(__file__).parent.parent
CLAIMS = ROOT / "fixtures" / "claims" / "stage5_claims.json"
PANEL_LOG = ROOT / "fixtures" / "sessions" / "stage11_panel" / "session.jsonl"

ANSWERS = [
    "I owned the payments pipeline from design through launch this quarter.",
    "We measured consumer lag and I decided to shed load at the edge first.",
    "I disagreed with the on-call rota and wrote the reason down for the team.",
    "I pushed back in review and we agreed to split the change into two deploys.",
    "I made the cutover call with incomplete traces and watched the error rate.",
    "I set a rollback threshold first, then let the migration run overnight.",
    "I missed a rollback window and changed the release checklist the next day.",
    "I added a dry-run step because I had skipped that check under time pressure.",
]


async def _run(
    tmp_path: Path,
    name: str,
    *,
    panel_mode: bool = False,
    lane: str = "voice",
    answers: list[str] | None = None,
) -> tuple[Path, LiveSession]:
    out = tmp_path / name
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "session.jsonl"
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, name)
    speak = (
        TextLaneSpeakPort(bus, name) if lane == "text" else FakeSpeakPort(bus, name)
    )
    live = LiveSession(
        bus=bus,
        session_id=name,
        log_path=str(log_path),
        transcript_path=str(out / "transcript.json"),
        speak=speak,
        config=SessionConfig(
            max_turns=16,
            max_minutes=30,
            pack_id="behavioral-core",
            intensity="panel" if panel_mode else "realistic",
            panel_mode=panel_mode,
            lane=lane,
            use_mock_llm=True,
        ),
    )
    live.attach()
    await live.start()
    await live.wait_idle()
    for index, text in enumerate(answers or ANSWERS):
        if live._closed:
            break
        await live.ingest_final(text, turn_id=f"{name}-turn-{index}")
    await live.end(reason="client")
    await bus.drain()
    await logger.close()
    return log_path, live


def _events(log_path: Path) -> list:
    return list(read_events(log_path))


def _spine_texts(events: list) -> list[str]:
    return [
        event.result.get("text", "")
        for event in events
        if event.type == "tool_result" and event.tool == "ask_spine" and event.ok
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Panel mode
# ─────────────────────────────────────────────────────────────────────────────


def test_pack_declares_a_panel_roster_with_distinct_voices() -> None:
    pack = load_pack("behavioral-core")
    assert 2 <= len(pack.panel) <= 3
    voices = [role.voice for role in pack.panel]
    assert len(set(voices)) == len(voices), "each persona needs its own voice"
    known = {c.id for c in pack.competencies}
    assert all(role.competency in known for role in pack.panel)


def test_a_panel_is_capped_at_three_voices() -> None:
    pack = load_pack("behavioral-core")
    raw = pack.model_dump()
    raw["panel"] = raw["panel"] + [
        {
            "persona": "fourth",
            "label": "Fourth",
            "voice": "echo",
            "competency": "ownership",
        }
    ]
    with pytest.raises(ValueError, match="at most 3 voices"):
        Pack.model_validate(raw)


def test_a_panel_cannot_reuse_a_voice() -> None:
    pack = load_pack("behavioral-core")
    raw = pack.model_dump()
    raw["panel"][1]["voice"] = raw["panel"][0]["voice"]
    with pytest.raises(ValueError, match="used twice"):
        Pack.model_validate(raw)


def test_panel_falls_back_to_one_voice_without_a_roster() -> None:
    pack = load_pack("behavioral-core")
    bare = Pack.model_validate({**pack.model_dump(), "panel": []})
    panel = Panel.build(bare, enabled=True)
    assert panel.enabled is False
    assert panel.persona_for_log("lead") is None


def test_floor_policy_is_idempotent_for_one_state() -> None:
    """The speculative loop and the committed turn must name the same voice."""
    pack = load_pack("behavioral-core")
    panel = Panel.build(pack, enabled=True)
    state = GuardState(
        pack=pack,
        intensity="panel",
        covered=(),
        outstanding=tuple(pack.spine_ids()),
        depth_on_current=0,
        time_remaining_s=900.0,
        claim_ids=frozenset(),
        claim_competency={},
        transcript_texts=(),
    )
    first = panel.floor_for_state(state).persona
    assert [panel.floor_for_state(state).persona for _ in range(5)] == [first] * 5


def test_a_probe_keeps_the_voice_that_opened_the_thread() -> None:
    pack = load_pack("behavioral-core")
    panel = Panel.build(pack, enabled=True)

    def state(covered, outstanding):
        return GuardState(
            pack=pack,
            intensity="panel",
            covered=tuple(covered),
            outstanding=tuple(outstanding),
            depth_on_current=0,
            time_remaining_s=900.0,
            claim_ids=frozenset(),
            claim_competency={},
            transcript_texts=(),
        )

    spine = pack.spine_ids()
    asker = panel.floor_for_state(state([], spine)).persona
    # Same state, but the next move is a follow-up: the voice must not hand over
    # to whoever owns the *next* spine item.
    prober = panel.floor_for_state(
        state([spine[0]], spine[1:]), probing=True
    ).persona
    assert prober == asker


@pytest.mark.asyncio
async def test_panel_fills_persona_and_still_asks_the_spine_verbatim(tmp_path: Path) -> None:
    log_path, live = await _run(tmp_path, "panel", panel_mode=True)
    events = _events(log_path)
    pack = load_pack("behavioral-core")

    assert live._panel is not None and live._panel.enabled

    # The shared guard still owns the spine: same text, same order.
    assert _spine_texts(events) == [item.text for item in pack.spine]

    # persona is filled on exactly the events the brief names.
    for kind in ("agent_step", "draft_ready", "floor_granted", "likely_next"):
        carrying = [
            event for event in events if event.type == kind and event.persona
        ]
        assert carrying, f"no {kind} carried a persona in panel mode"

    # More than one voice actually spoke, and every persona is on the roster.
    spoke = {event.persona for event in events if event.type == "draft_ready"}
    assert len(spoke) >= 2
    assert spoke <= set(live._panel.personas)

    complete = [event for event in events if event.type == "session_complete"][0]
    assert complete.personas == live._panel.spoken_order


@pytest.mark.asyncio
async def test_panel_agents_share_one_guard(tmp_path: Path) -> None:
    """Separate agents, one coverage state — not one spine run per persona."""
    _, live = await _run(tmp_path, "shared_guard", panel_mode=True)
    assert live._tools is not None
    agents = list(live._agents.values())
    assert len(agents) >= 2
    # Every agent holds the identical tools object, so there is one guard state.
    assert all(agent._tools is live._tools for agent in agents)
    assert len({id(agent) for agent in agents}) == len(agents)


@pytest.mark.asyncio
async def test_one_voice_at_a_time_with_distinct_voices(tmp_path: Path) -> None:
    log_path, _ = await _run(tmp_path, "voices", panel_mode=True)
    events = _events(log_path)
    chunks = [event for event in events if event.type == "tts_chunk"]
    voices = {event.audio_ref.split("/")[1] for event in chunks}
    assert len(voices) >= 2, f"expected distinct panel voices, saw {voices}"

    # One voice at a time: within an utterance every chunk has one voice, and
    # utterances never interleave in the log.
    by_utt: dict[str, set[str]] = {}
    order: list[str] = []
    for event in chunks:
        voice = event.audio_ref.split("/")[1]
        by_utt.setdefault(event.utterance_id, set()).add(voice)
        if event.utterance_id not in order:
            order.append(event.utterance_id)
    assert all(len(v) == 1 for v in by_utt.values())
    seen: set[str] = set()
    current = None
    for event in chunks:
        if event.utterance_id != current:
            assert event.utterance_id not in seen, "utterances interleaved"
            if current is not None:
                seen.add(current)
            current = event.utterance_id


@pytest.mark.asyncio
async def test_solo_session_has_no_persona_anywhere(tmp_path: Path) -> None:
    """Stage 11 must be invisible in a non-panel log."""
    log_path, _ = await _run(tmp_path, "solo", panel_mode=False)
    for event in _events(log_path):
        assert getattr(event, "persona", None) is None
    complete = [e for e in _events(log_path) if e.type == "session_complete"][0]
    assert complete.personas == []
    assert complete.lane == "voice"


# ─────────────────────────────────────────────────────────────────────────────
# 2. One evaluation per panel session
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_panel_session_gets_one_evaluation_not_one_per_persona(
    tmp_path: Path,
) -> None:
    log_path, _ = await _run(tmp_path, "panel_eval", panel_mode=True)
    report = await evaluate(
        transcript_path=log_path.with_name("transcript.json"),
        log_path=log_path,
        claims_path=CLAIMS,
        out_dir=tmp_path / "panel_eval_out",
    )
    # One report, one session id, one set of four dimensions.
    assert report.session_id == "panel_eval"
    assert [item.dimension for item in report.dimensions] == [
        "technical",
        "structure",
        "delivery",
        "competency",
    ]
    # Nothing in the evaluation record is per-persona: the evaluators read the
    # transcript, which does not name a voice.
    blob = report.model_dump_json()
    assert "persona" not in blob
    assert len(list((tmp_path / "panel_eval_out").glob("report*.json"))) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. Text lane
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_text_lane_runs_the_same_bus_agent_and_guard(tmp_path: Path) -> None:
    log_path, _ = await _run(tmp_path, "textlane", lane="text")
    events = _events(log_path)
    pack = load_pack("behavioral-core")

    # Same guard, same spine, same agent loop.
    assert _spine_texts(events) == [item.text for item in pack.spine]
    assert any(event.type == "agent_step" for event in events)
    assert any(event.type == "question_planned" for event in events)

    # No audio at all, so nothing to acknowledge and nothing to truncate.
    assert not [event for event in events if event.type == "tts_chunk"]
    assert not [event for event in events if event.type == "playback_ack"]

    complete = [event for event in events if event.type == "session_complete"][0]
    assert complete.lane == "text"
    assert any(
        event.type == "fallback_used" and event.kind == "text_lane"
        for event in events
    )


@pytest.mark.asyncio
async def test_text_lane_report_is_valid_and_delivery_is_not_assessed(
    tmp_path: Path,
) -> None:
    log_path, _ = await _run(tmp_path, "textreport", lane="text")
    report = await evaluate(
        transcript_path=log_path.with_name("transcript.json"),
        log_path=log_path,
        claims_path=CLAIMS,
        out_dir=tmp_path / "textreport_out",
    )
    assert report.lane == "text"
    by_dimension = {item.dimension: item for item in report.dimensions}
    assert by_dimension["delivery"].assessed is False
    assert by_dimension["delivery"].finding_count == 0
    assert not [item for item in report.findings if item.dimension == "delivery"]
    # The other three are assessed normally.
    assert all(
        by_dimension[name].assessed
        for name in ("technical", "structure", "competency")
    )
    # The report says so in words, not as a 0.00 the candidate has to decode.
    html = (tmp_path / "textreport_out" / "report.html").read_text(encoding="utf-8")
    assert "not assessed" in html
    # And it round-trips as a valid record.
    Report.model_validate_json(
        (tmp_path / "textreport_out" / "report.json").read_text(encoding="utf-8")
    )


def test_lane_only_changes_delivery() -> None:
    findings = [
        Finding(
            agent="substance",
            dimension="technical",
            summary="s",
            quote="q",
            turn_id="t",
            polarity="support",
        )
    ]
    voice = {item.dimension: item for item in score_findings(findings, "voice")}
    text = {item.dimension: item for item in score_findings(findings, "text")}
    for name in ("technical", "structure", "competency"):
        assert voice[name].score == text[name].score
        assert voice[name].assessed is text[name].assessed is True
    assert voice["delivery"].assessed is True
    assert text["delivery"].assessed is False


def test_a_not_assessed_dimension_is_not_stored_as_a_zero(tmp_path: Path) -> None:
    """A text-lane session must not put a false 0.0 into the delivery trend."""
    store = LongitudinalStore(tmp_path / "store.sqlite")
    report = Report(
        session_id="text-1",
        claims=[],
        findings=[],
        dimensions=[
            DimensionScore(dimension="technical", score=0.8, finding_count=2),
            DimensionScore(dimension="structure", score=0.7, finding_count=2),
            DimensionScore(
                dimension="delivery", score=0.0, finding_count=0, assessed=False
            ),
            DimensionScore(dimension="competency", score=0.6, finding_count=1),
        ],
        elapsed_s=0.1,
        lane="text",
    )
    store.record("cand", "behavioral-core", "2026-10-04T00:00:00Z", report)
    stored = {row["dimension"] for row in store.get_scores("text-1")}
    assert "delivery" not in stored
    assert stored == {"technical", "structure", "competency"}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Fallbacks
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_fallback_is_exercised_once_and_logged(tmp_path: Path) -> None:
    out = tmp_path / "fallbacks"
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "session.jsonl"
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, "fallbacks")
    live = LiveSession(
        bus=bus,
        session_id="fallbacks",
        log_path=str(log_path),
        transcript_path=str(out / "transcript.json"),
        speak=FakeSpeakPort(bus, "fallbacks"),
        config=SessionConfig(pack_id="behavioral-core", use_mock_llm=True),
    )
    live.attach()
    await live.start()
    await live.wait_idle()
    for kind, detail in (
        ("provider_failover", "primary model failed; degraded to local mock"),
        ("push_to_talk", "open-mic VAD unusable; holding to talk"),
        ("resume_only", "no repo supplied; claims built from the resume"),
        ("fallback_repo", "clone refused; indexed a local path"),
        ("text_lane", "candidate switched to the text lane"),
        ("ws_reconnect", "socket dropped and the session was resumed"),
    ):
        await live.note_fallback(kind, detail)
    await bus.drain()
    await live.end(reason="client")
    await bus.drain()
    await logger.close()

    logged = [
        (event.kind, event.detail)
        for event in _events(log_path)
        if event.type == "fallback_used"
    ]
    kinds = [kind for kind, _ in logged]
    assert set(kinds) == {
        "provider_failover",
        "push_to_talk",
        "resume_only",
        "fallback_repo",
        "text_lane",
        "ws_reconnect",
    }
    assert len(kinds) == len(set(kinds)), "each fallback once"
    assert all(detail for _, detail in logged), "a fallback must say why"


def test_provider_failover_names_a_second_model() -> None:
    from interview.llm.client import GroqModelClient

    client = GroqModelClient(api_key="not-used-offline")
    for role in ("live_interviewer", "indexer", "evaluator", "roadmap"):
        fallback = client.failover_model_for(role)  # type: ignore[arg-type]
        assert fallback, f"{role} has no failover model"
        assert fallback != client.model_for(role)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# 5. Video — display only (contract 9)
# ─────────────────────────────────────────────────────────────────────────────


def test_no_video_field_reaches_a_report_or_a_score() -> None:
    assert "video" not in Report.model_fields
    assert "video" not in DimensionScore.model_fields
    assert "video" not in Finding.model_fields
    report = Report(
        session_id="s",
        claims=[],
        findings=[],
        dimensions=score_findings([]),
        elapsed_s=0.0,
    )
    assert "video" not in report.model_dump_json().casefold()


def test_no_module_on_the_scoring_or_model_path_handles_video() -> None:
    """
    Contract 9 by construction: nothing that feeds a model or a score can read
    a frame, because no such code exists on those paths.
    """
    suspects = ("video", "webcam", "camera", "frame_rgb", "gaze", "iris", "emotion")
    for directory in ("evaluation", "llm", "roadmap"):
        for path in (ROOT / "src" / "interview" / directory).rglob("*.py"):
            body = path.read_text(encoding="utf-8").casefold()
            for word in suspects:
                assert word not in body, f"{path.name} mentions {word!r}"


def test_the_server_has_no_video_ingest_path() -> None:
    body = (ROOT / "src" / "interview" / "server.py").read_text(encoding="utf-8")
    # Binary frames are counted and dropped rather than parsed into anything.
    assert "dropped_binary += 1" in body
    assert "video" in body.casefold(), "the refusal should be documented"
    # No event in the vocabulary carries a frame.
    from interview.events import schema

    for name in dir(schema):
        model = getattr(schema, name)
        fields = getattr(model, "model_fields", None)
        if not fields:
            continue
        assert not any("video" in field for field in fields)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Hardening
# ─────────────────────────────────────────────────────────────────────────────


def test_session_cap_rejects_beyond_the_limit_and_frees_on_release() -> None:
    cap = SessionCap(limit=2)
    cap.acquire("a")
    cap.acquire("b")
    with pytest.raises(SessionCapExceeded):
        cap.acquire("c")
    # A reconnect re-acquires an id it already holds; that must not count twice.
    cap.acquire("a")
    assert cap.live == 2
    cap.release("a")
    cap.acquire("c")
    assert cap.live == 2
    assert cap.remaining == 0


def test_intake_rate_limit_is_per_candidate_and_recovers() -> None:
    now = [1000.0]
    limiter = RateLimiter(RateLimit(max_events=2, window_s=60.0), clock=lambda: now[0])
    assert limiter.allow("ada") is True
    assert limiter.allow("ada") is True
    assert limiter.allow("ada") is False
    # One candidate's budget is their own.
    assert limiter.allow("grace") is True
    assert limiter.retry_after_s("ada") == pytest.approx(60.0)
    now[0] += 61.0
    assert limiter.allow("ada") is True
    limiter.forget("ada")
    assert limiter.retry_after_s("ada") == 0.0


def test_resume_ticket_is_single_use_and_expires() -> None:
    now = [0.0]
    registry = ReconnectRegistry(ttl_s=30.0, clock=lambda: now[0])
    ticket = registry.issue("sess-1", live="object")
    assert registry.claim("nope") is None

    claimed = registry.claim(ticket.token)
    assert claimed is not None
    assert claimed.session_id == "sess-1"
    assert claimed.attachments["live"] == "object"
    # Single use: a leaked token cannot be replayed.
    assert registry.claim(ticket.token) is None

    stale = registry.issue("sess-2")
    now[0] += 31.0
    assert registry.claim(stale.token) is None
    assert len(registry) == 0

    revoked = registry.issue("sess-3")
    registry.revoke("sess-3")
    assert registry.claim(revoked.token) is None


@pytest.mark.asyncio
async def test_reconnect_keeps_one_session_one_log(tmp_path: Path) -> None:
    """
    A dropped socket must not split the session.

    The registry hands back the same LiveSession, so turn_ids continue and the
    log keeps one header and one monotonic seq sequence.
    """
    out = tmp_path / "reconnect"
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "session.jsonl"
    bus = EventBus()
    logger = await EventLogger.open(bus, log_path, "reconnect")
    live = LiveSession(
        bus=bus,
        session_id="reconnect",
        log_path=str(log_path),
        transcript_path=str(out / "transcript.json"),
        speak=FakeSpeakPort(bus, "reconnect"),
        config=SessionConfig(pack_id="behavioral-core", use_mock_llm=True),
    )
    live.attach()
    await live.start()
    await live.wait_idle()
    await live.ingest_final(ANSWERS[0], turn_id="before-drop")

    registry = ReconnectRegistry()
    ticket = registry.issue("reconnect", bus=bus, live=live, logger=logger)

    # The socket dies. Nothing ends; the ticket is redeemed.
    claimed = registry.claim(ticket.token)
    assert claimed is not None
    resumed = claimed.attachments["live"]
    assert resumed is live
    await resumed.note_fallback("ws_reconnect", "socket dropped and resumed")
    await resumed.ingest_final(ANSWERS[2], turn_id="after-drop")
    await resumed.end(reason="client")
    await bus.drain()
    await logger.close()

    lines = log_path.read_text(encoding="utf-8").splitlines()
    headers = [
        line for line in lines if json.loads(line).get("kind") == "session_header"
    ]
    assert len(headers) == 1, "a reconnect must not start a second log"

    events = _events(log_path)
    turns = {event.turn_id for event in events}
    assert "before-drop" in turns and "after-drop" in turns
    seqs = [event.seq for event in events]
    assert seqs == sorted(seqs), "seq must stay monotonic across the drop"
    assert any(
        event.type == "fallback_used" and event.kind == "ws_reconnect"
        for event in events
    )
    assert len([e for e in events if e.type == "session_complete"]) == 1


def test_delete_my_data_removes_everything_and_reports_it(tmp_path: Path) -> None:
    store_path = tmp_path / "store.sqlite"
    store = LongitudinalStore(store_path)
    report = Report(
        session_id="sess-a",
        claims=[],
        findings=[],
        dimensions=score_findings([]),
        elapsed_s=0.1,
    )
    store.record("ada", "behavioral-core", "2026-10-04T00:00:00Z", report)

    logs = tmp_path / "logs"
    reports = tmp_path / "reports"
    intake_root = tmp_path / "intake"
    intake = intake_root / "ada"
    for directory, name in ((logs, "session.jsonl"), (reports, "report.json")):
        (directory / "sess-a").mkdir(parents=True, exist_ok=True)
        (directory / "sess-a" / name).write_text("{}", encoding="utf-8")
    # A session recorded on disk that never reached the store — an interview cut
    # off before its evaluation ran.
    (logs / "sess-orphan").mkdir(parents=True, exist_ok=True)
    (logs / "sess-orphan" / "session.jsonl").write_text("{}", encoding="utf-8")
    intake.mkdir(parents=True, exist_ok=True)
    (intake / "claims.json").write_text("{}", encoding="utf-8")

    # Another candidate's data must survive.
    (logs / "sess-other").mkdir(parents=True, exist_ok=True)
    (logs / "sess-other" / "session.jsonl").write_text("{}", encoding="utf-8")

    # Another candidate's intake must survive too.
    (intake_root / "grace").mkdir(parents=True, exist_ok=True)
    (intake_root / "grace" / "claims.json").write_text("{}", encoding="utf-8")

    erased = delete_candidate_data(
        "ada",
        store_path=store_path,
        session_dirs=(logs,),
        report_dirs=(reports,),
        intake_roots=(intake_root,),
        extra_session_ids=("sess-orphan",),
    )

    assert erased.clean, erased.paths_skipped
    assert set(erased.session_ids) == {"sess-a", "sess-orphan"}
    assert erased.store_rows_deleted > 0
    assert not (logs / "sess-a").exists()
    assert not (logs / "sess-orphan").exists()
    assert not (reports / "sess-a").exists()
    assert not intake.exists()
    assert (logs / "sess-other").exists(), "another candidate was affected"
    assert (intake_root / "grace" / "claims.json").exists()

    conn = sqlite3.connect(store_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE candidate_id = 'ada'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM dimension_scores WHERE session_id = 'sess-a'"
        ).fetchone()[0] == 0
    finally:
        conn.close()

    # Idempotent: a repeat is clean and finds nothing left.
    again = delete_candidate_data(
        "ada",
        store_path=store_path,
        session_dirs=(logs,),
        report_dirs=(reports,),
        intake_roots=(intake_root,),
    )
    assert again.clean
    assert again.paths_deleted == []


@pytest.mark.parametrize(
    "bad_id",
    ["../escape", "..", "a/b", "/abs", r"C:\win", "with space", r"back\slash"],
)
def test_delete_my_data_refuses_an_id_that_could_escape_its_roots(
    tmp_path: Path, bad_id: str
) -> None:
    """
    Erasure must not become a path-traversal primitive.

    The function joins the id onto a declared root itself, so the only way out
    would be an id that is not a single path segment. Those are rejected.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    roots = tmp_path / "roots" / "intake"
    roots.mkdir(parents=True)

    with pytest.raises(UnsafeIdentifier):
        delete_candidate_data(
            bad_id,
            store_path=tmp_path / "missing.sqlite",
            session_dirs=(tmp_path / "roots",),
            intake_roots=(roots,),
        )
    assert (outside / "keep.txt").exists()


def test_delete_my_data_refuses_an_unsafe_session_id(tmp_path: Path) -> None:
    with pytest.raises(UnsafeIdentifier):
        delete_candidate_data(
            "ada",
            store_path=tmp_path / "missing.sqlite",
            session_dirs=(tmp_path,),
            extra_session_ids=("../escape",),
        )


def test_delete_my_data_needs_a_candidate_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="candidate id"):
        delete_candidate_data("  ", store_path=tmp_path / "s.sqlite")


def test_a_symlinked_target_outside_the_roots_is_skipped(tmp_path: Path) -> None:
    """The resolved-path check is what catches a root that points elsewhere."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    intake_root = tmp_path / "intake"
    intake_root.mkdir()
    try:
        (intake_root / "ada").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need elevated privileges on this platform")

    erased = delete_candidate_data(
        "ada",
        store_path=tmp_path / "missing.sqlite",
        session_dirs=(),
        intake_roots=(intake_root,),
    )
    assert (outside / "keep.txt").exists()
    assert erased.clean is False
    assert any("outside the declared roots" in note for note in erased.paths_skipped)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Replay (contract 5)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_panel_log_replays_without_a_model_or_tools() -> None:
    from tools.replay import replay  # type: ignore[import]

    assert PANEL_LOG.is_file(), "run tools/record_stage11_session.py first"
    original = _events(PANEL_LOG)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "replayed.jsonl"
        await replay(PANEL_LOG, fast=True, out=out)
        replayed = _events(out)

    assert len(replayed) == len(original)
    for index, (before, after) in enumerate(zip(original, replayed)):
        assert before.model_dump() == after.model_dump(), (
            f"panel event {index} ({before.type}) changed on replay"
        )
    # The persona survives a replay, which is what makes a panel session
    # reviewable from the log alone.
    assert any(
        event.type == "draft_ready" and event.persona for event in replayed
    )
