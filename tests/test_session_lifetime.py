"""
Session lifetime, single finalisation and admission (roadmap item 4).

Driven through the real HTTP API and WebSocket with the labelled offline
fakes (MOCK_LLM=1, text lane). Timing-sensitive cases shrink the budgets by
monkeypatching settings or the budget function, never by sleeping for minutes.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from interview.config import reset_settings_cache
from interview.evaluation.role_eval import MockEvaluator
from interview.hardening.limits import (
    Admission,
    AdmissionRefused,
    RateLimit,
    RateLimiter,
    client_address,
)
from interview.llm.limiter import RpmLimiter
from interview.services.evaluation import EvaluationService
from tests.test_stage15_journey import (  # noqa: F401 — `srv` is a fixture
    ANSWERS,
    SOFTWARE_RESUME,
    auth,
    guest,
    interview,
    read_until,
    run_intake,
    srv,
    wait_for,
)


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()


@pytest.fixture(autouse=True)
def _fresh_settings():
    yield
    reset_settings_cache()


def _prepared(client) -> tuple[dict, str]:
    headers = guest(client)
    intake = run_intake(
        client, headers, files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")}
    )
    assert intake["status"]["state"] == "ready"
    return headers, intake["intake_id"]


def _start(ws, headers, intake_id) -> dict:
    ws.send_text(json.dumps({"type": "session_start", "auth_token": headers["_token"], "intake_id": intake_id}))
    ready = read_until(ws, {"session_ready", "session_rejected"})[-1]
    return ready


def _meta(client, headers, session_id) -> dict:
    return client.get(f"/api/sessions/{session_id}", headers=auth(headers)).json()


# ---------------------------------------------------------------------------
# Watchdog: idle and wall-time expiry through the one close path
# ---------------------------------------------------------------------------


def test_idle_session_warns_then_ends_and_is_evaluated_once(srv, monkeypatch) -> None:
    _settings(monkeypatch, SESSION_IDLE_SECONDS="2", SESSION_IDLE_WARNING_S="1")
    enqueued: list[str] = []
    original = srv.EVALUATION.enqueue
    monkeypatch.setattr(srv.EVALUATION, "enqueue", lambda c, s: (enqueued.append(s), original(c, s))[1])
    with TestClient(srv.app) as client:
        headers, intake_id = _prepared(client)
        with client.websocket_connect("/ws/session") as ws:
            ready = _start(ws, headers, intake_id)
            assert ready["type"] == "session_ready"
            assert ready["limits"]["idle_s"] == 2
            session_id = ready["session_id"]
            seen = read_until(ws, {"session_complete"}, limit=600)
        warnings = [m for m in seen if m["type"] == "session_warning"]
        assert warnings and warnings[0]["reason"] == "idle"
        assert seen[-1]["type"] == "session_complete"
        assert seen[-1]["ended_reason"] == "idle"
        # The closer was spoken to the connected client.
        assert any(m.get("type") == "caption" and "haven't heard from you" in m.get("text", "") for m in seen)
        meta = wait_for(lambda: _meta(client, headers, session_id), lambda m: m["state"] in ("complete", "failed"))
        assert meta["ended_reason"] == "idle"
        assert meta["state"] == "complete"
    assert enqueued == [session_id]
    assert srv.SESSION_CAP.live == 0
    assert srv._LIVE == {}


def test_wall_time_limit_ends_the_session_with_a_time_limit_reason(srv, monkeypatch) -> None:
    monkeypatch.setattr(srv, "_session_budget_s", lambda interview: 1.5)
    with TestClient(srv.app) as client:
        headers, intake_id = _prepared(client)
        with client.websocket_connect("/ws/session") as ws:
            ready = _start(ws, headers, intake_id)
            assert 0 <= ready["limits"]["deadline_s"] <= 2
            session_id = ready["session_id"]
            seen = read_until(ws, {"session_complete"}, limit=600)
        # Under a minute left from the start: the time warning is immediate.
        assert any(m["type"] == "session_warning" and m["reason"] == "time_limit" for m in seen)
        assert seen[-1]["ended_reason"] == "time_limit"
        meta = wait_for(lambda: _meta(client, headers, session_id), lambda m: m["state"] in ("complete", "failed"))
        assert meta["ended_reason"] == "time_limit"
    assert srv.SESSION_CAP.live == 0


def test_budget_respects_shorter_practice_and_the_hard_ceiling(srv, monkeypatch) -> None:
    from interview.session.interview_config import InterviewConfig

    _settings(monkeypatch, MAX_SESSION_SECONDS="900", SESSION_OVERRUN_GRACE_S="60")
    base = dict(target_role="Engineer", role_family="software", background_text="I built a service in Python.")
    practice = InterviewConfig(**base, round="domain_specialist", total_seconds=180.0)
    long = InterviewConfig(**base, round="full", total_seconds=3600.0)
    assert srv._session_budget_s(practice) == 240.0
    assert srv._session_budget_s(long) == 900.0
    assert srv._session_budget_s(None) == 900.0


def test_reconnect_keeps_the_original_monotonic_deadline(srv, monkeypatch) -> None:
    monkeypatch.setattr(srv, "_session_budget_s", lambda interview: 120.0)
    with TestClient(srv.app) as client:
        headers, intake_id = _prepared(client)
        with client.websocket_connect("/ws/session") as ws:
            ready = _start(ws, headers, intake_id)
            read_until(ws, {"agent_utterance_end"})
            ctx = srv._LIVE[ready["session_id"]]
            deadline = ctx.deadline
            watchdog = ctx.watchdog
        # Dropped without session_end: parked, deadline still running.
        time.sleep(1.1)
        with client.websocket_connect("/ws/session") as again:
            again.send_text(json.dumps({"type": "session_resume", "token": ready["resume_token"]}))
            resumed = read_until(again, {"session_ready"})[-1]
            assert resumed["resumed"] is True
            assert srv._LIVE[ready["session_id"]].deadline == deadline
            assert srv._LIVE[ready["session_id"]].watchdog is watchdog, "no new timer per socket"
            assert resumed["limits"]["deadline_s"] < ready["limits"]["deadline_s"]
            again.send_text(json.dumps({"type": "session_end"}))
            read_until(again, {"session_complete"})
    assert watchdog.done(), "watchdog cancelled on cleanup"
    assert srv.SESSION_CAP.live == 0


# ---------------------------------------------------------------------------
# One idempotent finalisation
# ---------------------------------------------------------------------------


def test_concurrent_end_timeout_and_duplicate_close_finalise_once(srv, monkeypatch) -> None:
    enqueued: list[str] = []
    original = srv.EVALUATION.enqueue
    monkeypatch.setattr(srv.EVALUATION, "enqueue", lambda c, s: (enqueued.append(s), original(c, s))[1])
    with TestClient(srv.app) as client:
        headers, intake_id = _prepared(client)
        with client.websocket_connect("/ws/session") as ws:
            ready = _start(ws, headers, intake_id)
            read_until(ws, {"agent_utterance_end"})
            ws.send_text(json.dumps({"type": "candidate_text", "text": ANSWERS[0]}))
            read_until(ws, {"agent_utterance_end"})
            ctx = srv._LIVE[ready["session_id"]]

            async def race():
                await asyncio.gather(
                    srv._close_out(ctx, reason="time_limit"),
                    srv._close_out(ctx, reason="idle"),
                    srv._close_out(ctx),
                    ctx.live.end(reason="client"),
                )

            client.portal.call(race)
            # A duplicate explicit end after finalisation is harmless.
            ws.send_text(json.dumps({"type": "session_end"}))
            read_until(ws, {"session_complete"})
        session_id = ready["session_id"]
        meta = wait_for(lambda: _meta(client, headers, session_id), lambda m: m["state"] in ("complete", "failed"))
        # Whichever path ended the live session first names the reason; there is one.
        assert meta["ended_reason"] in ("time_limit", "client")
        assert meta["ended_reason"] == ctx.live.ended_reason
    assert enqueued == [session_id]
    assert srv.SESSION_CAP.live == 0
    events = [json.loads(l) for l in (srv.REGISTRY.session_dir(guest_id(srv, headers), session_id) / "session.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(1 for e in events if e.get("type") == "session_complete") == 1


def guest_id(srv, headers) -> str:
    return srv.REGISTRY.resolve(headers["Authorization"])


def test_deletion_racing_a_timeout_leaves_nothing_behind(srv, monkeypatch) -> None:
    with TestClient(srv.app) as client:
        headers, intake_id = _prepared(client)
        candidate_id = guest_id(srv, headers)
        with client.websocket_connect("/ws/session") as ws:
            ready = _start(ws, headers, intake_id)
            read_until(ws, {"agent_utterance_end"})
            ctx = srv._LIVE[ready["session_id"]]

            async def race():
                return await asyncio.gather(
                    srv._close_out(ctx, reason="time_limit"),
                    srv.erase_candidate(candidate_id),
                    return_exceptions=True,
                )

            results = client.portal.call(race)
            assert not [r for r in results if isinstance(r, BaseException)], results
        client.portal.call(srv.EVALUATION.wait, ready["session_id"])
    assert not srv.REGISTRY.candidate_dir(candidate_id).exists()
    assert srv.SESSION_CAP.live == 0 and srv._LIVE == {}


def test_slot_is_released_even_if_ending_fails(srv, monkeypatch) -> None:
    with TestClient(srv.app) as client:
        headers, intake_id = _prepared(client)
        with client.websocket_connect("/ws/session") as ws:
            ready = _start(ws, headers, intake_id)
            read_until(ws, {"agent_utterance_end"})
            ctx = srv._LIVE[ready["session_id"]]

            async def boom(*args, **kwargs):
                raise RuntimeError("synthetic end failure")

            monkeypatch.setattr(ctx.live, "end", boom)

            async def close():
                try:
                    await srv._close_out(ctx, reason="time_limit")
                except RuntimeError:
                    return "raised"
                return "ok"

            assert client.portal.call(close) == "raised"
            assert srv.SESSION_CAP.live == 0
            assert ready["session_id"] not in srv._LIVE


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------


def test_new_guests_cannot_bypass_the_address_start_limit(srv, monkeypatch) -> None:
    monkeypatch.setattr(srv, "START_ADDRESS_LIMITER", RateLimiter(RateLimit(max_events=2, window_s=3600.0)))
    with TestClient(srv.app) as client:
        outcomes = []
        for _ in range(3):
            headers, intake_id = _prepared(client)  # a fresh guest every time
            with client.websocket_connect("/ws/session") as ws:
                ready = _start(ws, headers, intake_id)
                outcomes.append(ready)
                if ready["type"] == "session_ready":
                    read_until(ws, {"agent_utterance_end"})
                    ws.send_text(json.dumps({"type": "session_end"}))
                    read_until(ws, {"session_complete"})
        assert [o["type"] for o in outcomes] == ["session_ready", "session_ready", "session_rejected"]
        assert outcomes[2]["retry_after_s"] > 0
        assert "Try again" in outcomes[2]["reason"]
    assert srv.SESSION_CAP.live == 0, "a refused start must not hold a slot"


def test_intake_refusals_after_admission_hand_the_quota_back(srv, monkeypatch) -> None:
    monkeypatch.setattr(srv, "INTAKE_ADDRESS_LIMITER", RateLimiter(RateLimit(max_events=1, window_s=3600.0)))
    with TestClient(srv.app) as client:
        headers = guest(client)
        bad = client.post(
            "/api/intake",
            data={"consent": "1", "target_role": "Engineer", "role_family": "software", "background_text": ""},
            headers=auth(headers),
        )
        assert bad.status_code == 422  # nothing to work from: refused before any job
        ok = client.post(
            "/api/intake",
            data={"consent": "1", "target_role": "Engineer", "role_family": "software",
                  "background_text": "I built a Python service.", "lane": "text"},
            headers=auth(headers),
        )
        assert ok.status_code == 202, ok.text
        third = client.post(
            "/api/intake",
            data={"consent": "1", "target_role": "Engineer", "role_family": "software",
                  "background_text": "I built a Python service.", "lane": "text"},
            headers=auth(headers),
        )
        assert third.status_code == 429
        body = third.json()
        assert body["retry_after_s"] > 0 and "Nothing was started" in body["recovery"]
        assert int(third.headers["Retry-After"]) == body["retry_after_s"]


def test_guest_creation_has_address_and_global_limits(srv, monkeypatch) -> None:
    monkeypatch.setattr(srv, "GUEST_LIMITER", RateLimiter(RateLimit(max_events=50, window_s=3600.0)))
    monkeypatch.setattr(srv, "GUEST_GLOBAL_LIMITER", RateLimiter(RateLimit(max_events=2, window_s=3600.0)))
    with TestClient(srv.app) as client:
        codes = [client.post("/api/guest").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_admission_is_all_or_nothing_and_refundable() -> None:
    a = RateLimiter(RateLimit(max_events=1, window_s=60.0))
    b = RateLimiter(RateLimit(max_events=5, window_s=60.0))
    Admission([(a, "x", "a"), (b, "y", "b")]).admit()
    with pytest.raises(AdmissionRefused) as refused:
        Admission([(a, "x", "a full"), (b, "y", "b")]).admit()
    assert str(refused.value) == "a full" and refused.value.retry_after_s >= 1
    # The refusal did not spend b's budget.
    assert len(b._window("y")) == 1
    adm = Admission([(b, "y", "b")]).admit()
    adm.refund()
    assert len(b._window("y")) == 1


def test_forwarded_for_is_only_believed_from_trusted_proxies() -> None:
    headers = {"x-forwarded-for": "203.0.113.9, 10.0.0.5"}
    # Untrusted peer: the header is ignored, so it cannot mint fresh budgets.
    assert client_address("198.51.100.7", headers, []) == "198.51.100.7"
    assert client_address("198.51.100.7", headers, ["10.0.0.0/8"]) == "198.51.100.7"
    # Trusted proxy chain: the first untrusted hop from the right is the client.
    assert client_address("10.0.0.2", headers, ["10.0.0.0/8"]) == "203.0.113.9"
    assert client_address("127.0.0.1", {"x-forwarded-for": "garbage"}, ["127.0.0.1"]) == "127.0.0.1"
    assert client_address("127.0.0.1", {}, ["127.0.0.1"]) == "127.0.0.1"


def test_retry_is_refused_with_guidance_when_the_queue_is_full(srv, monkeypatch) -> None:
    monkeypatch.setattr(srv.EVALUATION, "at_capacity", lambda: True)
    with TestClient(srv.app) as client:
        headers, intake_id = _prepared(client)
        with client.websocket_connect("/ws/session") as ws:
            ready = _start(ws, headers, intake_id)
            assert ready["type"] == "session_ready"
            read_until(ws, {"agent_utterance_end"})
            ws.send_text(json.dumps({"type": "session_end"}))
            read_until(ws, {"session_complete"})
        meta = wait_for(lambda: _meta(client, headers, ready["session_id"]), lambda m: m["state"] in ("failed", "complete"))
        # Enqueue refused before provider work, transcript kept, recovery stated.
        assert meta["state"] == "failed" and "Retry" in meta["recovery"]
        r = client.post(f"/api/sessions/{ready['session_id']}/evaluation/retry", headers=auth(headers))
        assert r.status_code == 429 and r.json()["retry_after_s"] > 0
        assert client.get(f"/api/sessions/{ready['session_id']}/transcript", headers=auth(headers)).status_code == 200


# ---------------------------------------------------------------------------
# Bounded evaluation work and live-first scheduling
# ---------------------------------------------------------------------------


class SlowMock(MockEvaluator):
    """FAKE: the labelled mock evaluator, slowed down, counting concurrency."""

    active = 0
    peak = 0

    async def complete(self, messages):
        SlowMock.active += 1
        SlowMock.peak = max(SlowMock.peak, SlowMock.active)
        try:
            await asyncio.sleep(0.15)
            return await super().complete(messages)
        finally:
            SlowMock.active -= 1


@pytest.mark.parametrize("concurrency", [1, 2])
def test_round_jobs_never_exceed_the_configured_concurrency(srv, monkeypatch, concurrency) -> None:
    SlowMock.active = SlowMock.peak = 0
    service = EvaluationService(srv.REGISTRY, srv.REPORT_STORE, lambda: SlowMock())
    service.concurrency = concurrency
    monkeypatch.setattr(srv, "EVALUATION", service)
    with TestClient(srv.app) as client:
        headers = guest(client)
        full = run_intake(
            client, headers, round="full",
            files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")},
        )
        assert full["status"]["state"] == "ready"
        session_id, _ = interview(client, headers, full["intake_id"], ANSWERS * 6)
        ready = {"session_id": session_id}
        view = wait_for(
            lambda: client.get(f"/api/sessions/{ready['session_id']}/evaluation", headers=auth(headers)).json(),
            lambda v: v["state"] in ("complete", "failed"),
        )
    model_rounds = [r for r in view["rounds"] if r["state"] == "complete"]
    assert len(model_rounds) == 3, view["rounds"]  # three provider jobs competed for slots
    assert SlowMock.peak == concurrency
    assert all("slot_wait_s" in r for r in view["rounds"] if not r.get("cache_hit"))


async def test_background_calls_leave_the_reserve_for_live_turns() -> None:
    limiter = RpmLimiter(5, live_reserved_share=0.2, background_max_defer_s=30.0)
    assert limiter.reserved_for_live == 1
    for _ in range(4):
        await asyncio.wait_for(limiter.acquire("background"), 0.5)
    # The fifth slot is held for live work.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(limiter.acquire("background"), 0.3)
    await asyncio.wait_for(limiter.acquire("live"), 0.3)
    assert limiter.snapshot()["in_window"] == 5


async def test_background_yields_to_waiting_live_but_is_not_starved() -> None:
    limiter = RpmLimiter(10, live_reserved_share=0.0, background_max_defer_s=0.3)
    limiter._live_waiting = 1  # a live turn is queued (simulated)
    started = time.monotonic()
    await asyncio.wait_for(limiter.acquire("background"), 2.0)
    waited = time.monotonic() - started
    assert 0.25 <= waited < 1.5, waited  # deferred, then proceeded after max defer
    assert limiter.deferrals == 1
    limiter._live_waiting = 0
    started = time.monotonic()
    await limiter.acquire("background")
    assert time.monotonic() - started < 0.1
