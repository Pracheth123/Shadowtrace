"""
Stage 16 — per-round evaluation jobs.

Driven directly against `EvaluationService` with a FAKE evaluator that answers
per perspective, so a test can hold one round open while another finishes, or
fail one round and not the others. No network.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from interview.candidates import CandidateRegistry, read_json, write_json
from interview.evaluation.role_eval import MockEvaluator
from interview.llm.client import CallMeta, ProviderCallFailed
from interview.roadmap.reports import ReportStore
from interview.services.evaluation import EvaluationService

ROUNDS = [
    {"round": "hr", "label": "Recruiter", "pack_id": "hr-core", "questions_asked": 2, "spine_total": 4, "spine_covered": 2},
    {"round": "hiring_manager", "label": "Hiring manager", "pack_id": "manager-core", "questions_asked": 2, "spine_total": 5, "spine_covered": 2},
    {"round": "domain_specialist", "label": "Software specialist", "pack_id": "specialist-software", "questions_asked": 2, "spine_total": 4, "spine_covered": 2},
]

ANSWER = {
    "hr": "I moved from support into engineering because I wanted to build the tools I kept asking for, and this role is that.",
    "hiring_manager": "I owned the migration plan end to end, decided to cut over in two phases, and told the team why before we started.",
    "domain_specialist": "I built the reconciliation job in Python and chose PostgreSQL over DynamoDB for transactional consistency across accounts.",
}


def perspective_of(messages) -> str:
    system = messages[0]["content"]
    if "recruiter (HR) round" in system:
        return "hr"
    if "hiring-manager round" in system:
        return "hiring_manager"
    return "domain_specialist"


class PerRoundEvaluator(MockEvaluator):
    """FAKE: MockEvaluator replies, with per-round gates and scripted failures."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {"hr": 0, "hiring_manager": 0, "domain_specialist": 0}
        self.gates: dict[str, asyncio.Event] = {}
        self.fail: dict[str, int] = {}

    async def complete_with_meta(self, messages, *, deadline_s=None):
        who = perspective_of(messages)
        self.calls[who] += 1
        gate = self.gates.get(who)
        if gate is not None:
            await gate.wait()
        if self.fail.get(who, 0) > 0:
            self.fail[who] -= 1
            raise ProviderCallFailed("HTTP 429", category="rate_limited", meta=CallMeta(attempts=4))
        return await super().complete_with_meta(messages, deadline_s=deadline_s)


def make_session(registry: CandidateRegistry, *, transcript_override=None) -> tuple[str, str, Path]:
    guest = registry.create_guest()
    session_id = "sess-" + guest.candidate_id[:12]
    directory = registry.session_dir(guest.candidate_id, session_id)
    directory.mkdir(parents=True)
    transcript = []
    for r in ROUNDS:
        transcript.append({"speaker": "agent", "turn_id": f"{r['round']}-q", "text": "Tell me about it?", "round": r["round"]})
        transcript.append({"speaker": "candidate", "turn_id": f"{r['round']}-a", "text": ANSWER[r["round"]], "round": r["round"]})
    write_json(directory / "transcript.json", transcript_override or transcript)
    write_json(
        directory / "meta.json",
        {
            "session_id": session_id,
            "candidate_id": guest.candidate_id,
            "intake_id": None,
            "created_at": "2026-10-06T10:00:00.000Z",
            "lane": "text",
            "config": {"target_role": "Backend engineer", "role_family": "software", "round": "full", "seniority": "mid", "intensity": "realistic"},
            "rounds": ROUNDS,
            "ended_reason": "complete",
            "state": "finalising",
        },
    )
    return guest.candidate_id, session_id, directory


@pytest.fixture
def env(tmp_path):
    registry = CandidateRegistry(tmp_path / "candidates")
    store = ReportStore(tmp_path / "reports.sqlite")
    evaluator = PerRoundEvaluator()
    service = EvaluationService(registry, store, lambda: evaluator)
    return registry, store, service, evaluator


async def wait_until(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.01)


async def test_completed_rounds_are_visible_while_another_round_runs(env) -> None:
    registry, store, service, evaluator = env
    evaluator.gates["domain_specialist"] = asyncio.Event()
    cid, sid, directory = make_session(registry)
    service.enqueue(cid, sid)

    def view():
        return service.job_view(cid, sid)

    await wait_until(
        lambda: {r["round"]: r["state"] for r in view()["rounds"]}.get("hr") == "complete"
        and {r["round"]: r["state"] for r in view()["rounds"]}.get("hiring_manager") == "complete"
    )
    partial = view()
    states = {r["round"]: r["state"] for r in partial["rounds"]}
    assert states["domain_specialist"] == "running"
    assert partial["state"] == "evaluating"
    assert partial["report_ready"] is False            # visibly incomplete
    assert partial["transcript_available"] is True
    hr = next(r for r in partial["rounds"] if r["round"] == "hr")
    assert hr["result"]["status"] == "evaluated"       # readable now
    assert hr["result"]["dimensions"]
    assert partial["elapsed_s"] is not None            # real elapsed time, no percentage
    assert "percent" not in json.dumps(partial)

    evaluator.gates["domain_specialist"].set()
    await service.wait(sid)
    done = view()
    assert done["state"] == "complete" and done["report_ready"] is True
    timings = read_json(directory / "meta.json")["evaluation"]["timings"]
    assert timings["first_result_s"] <= timings["report_s"]


async def test_retry_reruns_only_the_failed_round_and_keeps_the_others(env) -> None:
    registry, store, service, evaluator = env
    evaluator.fail["hiring_manager"] = 1
    cid, sid, directory = make_session(registry)
    service.enqueue(cid, sid)
    await service.wait(sid)
    meta = service.status(cid, sid)
    assert meta["state"] == "failed"
    rounds = {r["round"]: r for r in meta["evaluation"]["rounds"]}
    assert rounds["hiring_manager"]["state"] == "failed"
    assert rounds["hiring_manager"]["category"] == "rate_limited"
    assert "Retry" in rounds["hiring_manager"]["recovery"]
    assert rounds["hr"]["state"] == "complete" and rounds["domain_specialist"]["state"] == "complete"
    assert evaluator.calls == {"hr": 1, "hiring_manager": 1, "domain_specialist": 1}

    service.retry(cid, sid)
    await service.wait(sid)
    assert service.status(cid, sid)["state"] == "complete"
    # Only the failed round was sent to the provider again.
    assert evaluator.calls == {"hr": 1, "hiring_manager": 2, "domain_specialist": 1}
    report = read_json(directory / "evaluation" / "report.json")
    assert [r["round"] for r in report["rounds"]] == ["hr", "hiring_manager", "domain_specialist"]


async def test_reading_state_never_repeats_provider_calls(env) -> None:
    registry, store, service, evaluator = env
    cid, sid, _ = make_session(registry)
    service.enqueue(cid, sid)
    await service.wait(sid)
    before = dict(evaluator.calls)
    for _ in range(5):
        service.status(cid, sid)
        service.job_view(cid, sid)
        service.enqueue(cid, sid)    # a reconnect / second end request
    await asyncio.sleep(0.05)
    assert evaluator.calls == before


async def test_cached_rounds_are_reused_without_a_model_call(env) -> None:
    registry, store, service, evaluator = env
    cid, sid, directory = make_session(registry)
    service.enqueue(cid, sid)
    await service.wait(sid)
    before = dict(evaluator.calls)
    # Same transcript, rubric, prompt and evaluator config → cache hit.
    meta = read_json(directory / "meta.json")
    meta["state"] = "finalising"
    write_json(directory / "meta.json", meta)
    service.enqueue(cid, sid)
    await service.wait(sid)
    assert evaluator.calls == before
    rounds = service.status(cid, sid)["evaluation"]["rounds"]
    assert all(r["cache_hit"] for r in rounds)


async def test_cache_never_crosses_candidates(env) -> None:
    registry, store, service, evaluator = env
    cid_a, sid_a, _ = make_session(registry)
    service.enqueue(cid_a, sid_a)
    await service.wait(sid_a)
    # An identical transcript belonging to someone else is evaluated afresh.
    cid_b, sid_b, dir_b = make_session(registry)
    service.enqueue(cid_b, sid_b)
    await service.wait(sid_b)
    assert evaluator.calls == {"hr": 2, "hiring_manager": 2, "domain_specialist": 2}
    keys_a = {r["cache_key"] for r in service.status(cid_a, sid_a)["evaluation"]["rounds"]}
    keys_b = {r["cache_key"] for r in service.status(cid_b, sid_b)["evaluation"]["rounds"]}
    assert not keys_a & keys_b


async def test_restart_reports_interrupted_rounds_and_keeps_finished_ones(env, tmp_path) -> None:
    registry, store, service, evaluator = env
    evaluator.gates["domain_specialist"] = asyncio.Event()
    cid, sid, _ = make_session(registry)
    service.enqueue(cid, sid)
    await wait_until(
        lambda: any(
            r["round"] == "hr" and r["state"] == "complete"
            for r in service.job_view(cid, sid)["rounds"]
        )
    )
    # The process "restarts": a new service with no tasks reads the same disk.
    restarted = EvaluationService(registry, store, lambda: evaluator)
    meta = restarted.status(cid, sid)
    assert meta["state"] == "failed"
    rounds = {r["round"]: r for r in meta["evaluation"]["rounds"]}
    assert rounds["hr"]["state"] == "complete"
    assert rounds["domain_specialist"]["category"] == "interrupted"
    evaluator.gates["domain_specialist"].set()
    await service.wait(sid)


async def test_round_with_too_little_to_assess_is_not_sent_to_a_model(env) -> None:
    registry, store, service, evaluator = env
    transcript = [
        {"speaker": "agent", "turn_id": "hr-q", "text": "Tell me about yourself?", "round": "hr"},
        {"speaker": "candidate", "turn_id": "hr-a", "text": "I don't know.", "round": "hr"},
    ]
    cid, sid, directory = make_session(registry, transcript_override=transcript)
    service.enqueue(cid, sid)
    await service.wait(sid)
    assert evaluator.calls == {"hr": 0, "hiring_manager": 0, "domain_specialist": 0}
    report = read_json(directory / "evaluation" / "report.json")
    hr = report["rounds"][0]
    assert hr["status"] == "insufficient_evidence"
    assert hr["aggregate"]["score"] is None
    assert report["overall"]["score"] is None          # no neutral score invented
    states = {r["round"]: r["state"] for r in service.status(cid, sid)["evaluation"]["rounds"]}
    assert set(states.values()) == {"not_assessed"}


async def test_deletion_cancels_work_and_nothing_is_recreated(env) -> None:
    registry, store, service, evaluator = env
    evaluator.gates["domain_specialist"] = asyncio.Event()
    cid, sid, directory = make_session(registry)
    service.enqueue(cid, sid)
    await wait_until(
        lambda: any(r["state"] == "running" for r in service.job_view(cid, sid)["rounds"])
    )
    cancelled = await service.cancel_candidate(cid)
    registry.delete(cid)
    evaluator.gates["domain_specialist"].set()
    await asyncio.sleep(0.1)
    assert cancelled >= 1
    assert not registry.candidate_dir(cid).exists()
    assert store.sessions(cid, kind=None) == []
