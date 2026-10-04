"""
Stage 15 — the candidate journey, end to end, through the real HTTP API and
WebSocket.

    guest → intake (multipart, background job) → personalised session →
    transcript → background evaluation → report → history → plan → delete

Deterministic fakes, clearly identified: `MOCK_LLM=1` selects the role-aware
DeterministicProposer for the interviewer and the labelled MockEvaluator for
evaluation; the text lane needs no audio provider; repository cloning is
replaced by copying a local directory. None of this is evidence that a live
provider session works — see tools/live_journey_smoke.py for that.
"""

from __future__ import annotations

import io
import json
import shutil
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from interview.evaluation.role_eval import (
    ClaimInput,
    EvaluationPassFailed,
    MockEvaluator,
    TranscriptTurn,
    delivery_observations,
    evaluate_round,
    merge_claims,
    ClaimPosition,
)
from interview.events.log import read_events
from interview.hardening.limits import RateLimit, RateLimiter, SessionCap
from interview.hardening.reconnect import ReconnectRegistry
from interview.packs.model import load_pack

SOFTWARE_RESUME = """Ada Lovelace
ada@example.com

Skills
Python, PostgreSQL, Kafka

Projects
Payments ledger
I built a double-entry ledger service in Python that reconciled card settlements nightly.
I chose PostgreSQL over DynamoDB because we needed transactional consistency across accounts.
Tech: Python, PostgreSQL
"""

REPO_README = """# ledger-svc

A double-entry ledger that reconciles card settlements against bank statements nightly.
The reconciler streams settlement files through a Kafka topic and writes balanced journal entries.
"""

SALES_BACKGROUND = (
    "I have spent four years in B2B sales. I closed a 400k renewal with a logistics "
    "customer after a six-month cycle. I built a qualification checklist that my team "
    "adopted for enterprise deals. I negotiated a multi-year contract while holding our "
    "price floor."
)

SOFTWARE_SPINE_TEXTS = {item.text for item in load_pack("specialist-software").spine}
SALES_SPINE_TEXTS = {item.text for item in load_pack("specialist-sales").spine}


@pytest.fixture
def srv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import interview.server as server
    from interview.services.evaluation import build_services

    monkeypatch.setattr(server, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(server, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(server, "INTAKE_DIR", tmp_path / "intake")
    monkeypatch.setattr(server, "STORE_PATH", tmp_path / "store.sqlite")
    monkeypatch.setattr(server, "SESSION_CAP", SessionCap(limit=8))
    monkeypatch.setattr(
        server, "INTAKE_LIMITER", RateLimiter(RateLimit(max_events=50, window_s=3600.0))
    )
    monkeypatch.setattr(server, "RECONNECT", ReconnectRegistry(ttl_s=60.0))
    monkeypatch.setattr(server, "_PARKED", {})
    monkeypatch.setenv("MOCK_LLM", "1")
    registry, store, evaluation = build_services(tmp_path / "data", use_mock_llm=lambda: True)
    monkeypatch.setattr(server, "REGISTRY", registry)
    monkeypatch.setattr(server, "REPORT_STORE", store)
    monkeypatch.setattr(server, "EVALUATION", evaluation)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    repo = tmp_path / "fixture-repo"
    repo.mkdir()
    (repo / "README.md").write_text(REPO_README, encoding="utf-8")
    (repo / ".env").write_text("SECRET=do-not-read", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "ledger-svc"\n', encoding="utf-8")

    def fake_clone(url: str, dest: Path) -> None:  # FAKE: copies a local tree
        if "missing" in url:
            raise RuntimeError("repository not found")
        shutil.copytree(repo, dest)

    monkeypatch.setattr("interview.services.intake.shallow_clone", fake_clone)
    return server


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def guest(client: TestClient) -> dict[str, str]:
    body = client.post("/api/guest").json()
    return {"Authorization": f"Bearer {body['token']}", "_token": body["token"]}


def auth(headers: dict[str, str]) -> dict[str, str]:
    return {"Authorization": headers["Authorization"]}


def wait_for(fn, predicate, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while True:
        value = fn()
        if predicate(value):
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out; last value {value}")
        time.sleep(0.05)


def run_intake(client, headers, *, files=None, **fields) -> dict:
    data = {
        "target_role": "Backend engineer",
        "role_family": "software",
        "seniority": "mid",
        "round": "domain_specialist",
        "lane": "text",
        "intensity": "realistic",
        **fields,
    }
    response = client.post("/api/intake", data=data, files=files or {}, headers=auth(headers))
    assert response.status_code == 202, response.text
    intake_id = response.json()["intake_id"]
    result = wait_for(
        lambda: client.get(f"/api/intake/{intake_id}", headers=auth(headers)).json(),
        lambda body: body["status"]["state"] in ("ready", "failed"),
    )
    result["intake_id"] = intake_id
    return result


def read_until(ws, wanted: set[str], limit: int = 400) -> list[dict]:
    seen: list[dict] = []
    for _ in range(limit):
        data = ws.receive()
        if data.get("type") == "websocket.close":
            break
        if data.get("text") is None:
            continue
        msg = json.loads(data["text"])
        seen.append(msg)
        if msg.get("type") in wanted:
            break
    return seen


def interview(client, headers, intake_id: str, answers: list[str], *, end_early: bool = False):
    """Run one text-lane session. Returns (session_id, all wire messages)."""
    messages: list[dict] = []
    with client.websocket_connect("/ws/session") as ws:
        ws.send_text(
            json.dumps(
                {"type": "session_start", "auth_token": headers["_token"], "intake_id": intake_id}
            )
        )
        ready = read_until(ws, {"session_ready", "session_rejected"})
        messages += ready
        assert ready[-1]["type"] == "session_ready", ready[-1]
        session_id = ready[-1]["session_id"]
        messages += read_until(ws, {"agent_utterance_end"})
        for answer in answers:
            ws.send_text(json.dumps({"type": "candidate_text", "text": answer}))
            got = read_until(ws, {"agent_utterance_end", "session_complete"})
            messages += got
            if got and got[-1]["type"] == "session_complete":
                return session_id, messages
            # A round handover is two utterances: the transition, then the opener.
            if any(m["type"] == "round_transition" for m in got):
                messages += read_until(ws, {"agent_utterance_end"})
        ws.send_text(json.dumps({"type": "session_end"}))
        messages += read_until(ws, {"session_complete"})
    return session_id, messages


def finished_report(client, headers, session_id: str) -> dict:
    meta = wait_for(
        lambda: client.get(f"/api/sessions/{session_id}", headers=auth(headers)).json(),
        lambda body: body.get("state") in ("complete", "failed"),
    )
    assert meta["state"] == "complete", meta
    report = client.get(f"/api/sessions/{session_id}/report", headers=auth(headers))
    assert report.status_code == 200
    return report.json()


def questions(messages: list[dict]) -> list[str]:
    return [m["text"] for m in messages if m["type"] == "agent_utterance_start"]


ANSWERS = [
    "I built a double-entry ledger service in Python that reconciled card settlements nightly. "
    "I owned the reconciliation design and decided to stream settlement files through Kafka.",
    "I chose PostgreSQL over DynamoDB because we needed transactional consistency across "
    "accounts, and I verified it with a replay of a month of settlements before launch.",
    "The first version failed when a bank sent duplicate files, so I added idempotency keys "
    "and a dead-letter queue, and measured that mismatches dropped to zero over two weeks.",
    "If I did it again I would partition by account earlier, because hot accounts caused "
    "lock contention, which I found by profiling the slow journal writes.",
    "I wrote the runbook and paired with the on-call engineer so the team could own it after I moved on.",
    "We reviewed every mismatch weekly and I kept a log of root causes to prioritise fixes.",
]


# ---------------------------------------------------------------------------
# A. Software candidate: resume + repository → grounded questions → real report
# ---------------------------------------------------------------------------


def test_a_software_candidate_resume_and_repo_reach_the_questions_and_report(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client,
            headers,
            files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")},
            repo_url="https://github.com/ada/ledger-svc",
            job_description="Required skills\nPython, Kafka, Terraform",
        )
        assert intake["status"]["state"] == "ready", intake["status"]
        claims = intake["claims"]
        kinds = {claim["evidence_kind"] for claim in claims}
        assert "candidate_assertion" in kinds and "repository" in kinds
        # Every claim is a verbatim span of something intake actually read.
        corpus = SOFTWARE_RESUME + REPO_README
        for claim in claims:
            assert " ".join(claim["text"].split()) in " ".join(corpus.split())
        # The secret file in the repository was never read into any artifact.
        assert "do-not-read" not in json.dumps(intake)
        assert intake["fit_gap"]["missing"] == ["Terraform"]
        prep_kinds = {item["kind"] for item in intake["prep"]["items"]}
        assert {"defend_claim", "jd_gap", "round_preview", "company"} <= prep_kinds

        session_id, messages = interview(client, headers, intake["intake_id"], ANSWERS[:4])
        asked = questions(messages)
        claim_texts = [claim["text"].rstrip(".") for claim in claims]
        assert any(
            any(text in question for text in claim_texts) for question in asked
        ), f"no question was grounded in an intake claim: {asked}"
        assert not any(q in SALES_SPINE_TEXTS for q in asked)

        report = finished_report(client, headers, session_id)
        assert report["session_id"] == session_id
        assert report["intake_id"] == intake["intake_id"]
        assert report["personalised"] is True
        assert report["rounds"][0]["rubric_version"] == "specialist.software.v1"
        assert report["evaluator"]["provider"] == "mock"
        assert report["evaluator"]["is_assessment"] is False
        assert any("DEVELOPMENT EVALUATION" in line for line in report["limitations"])
        assert {c["claim_id"] for c in report["claims"]} == {c["id"] for c in claims}


# ---------------------------------------------------------------------------
# B. Non-technical candidate: sales, no GitHub, no software leakage
# ---------------------------------------------------------------------------


def test_b_sales_candidate_gets_sales_questions_and_a_sales_rubric(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client,
            headers,
            role_family="sales",
            target_role="Account executive",
            background_text=SALES_BACKGROUND,
        )
        assert intake["status"]["state"] == "ready"
        assert intake["sources"]["repository"]["fallback"] == "resume-only"
        assert "No repository" in intake["evidence_note"]
        session_id, messages = interview(
            client,
            headers,
            intake["intake_id"],
            [
                "I qualify on budget, authority and timing, and I walked away from a deal "
                "when procurement would not commit to a decision date.",
                "The toughest objection was price; I reframed around the cost of downtime "
                "and offered a phased rollout instead of a discount.",
            ],
        )
        asked = questions(messages)
        assert any(text in q for q in asked for text in SALES_SPINE_TEXTS)
        assert not any(text in q for q in asked for text in SOFTWARE_SPINE_TEXTS)
        report = finished_report(client, headers, session_id)
        rubric = report["rounds"][0]
        assert rubric["rubric_version"] == "specialist.sales.v1"
        labels = {d["label"] for d in rubric["dimensions"]}
        assert "Sales knowledge" in labels
        assert not any("technical" in label.casefold() for label in labels)


# ---------------------------------------------------------------------------
# C. Full interview: three sequential rounds, handoffs, recorded coverage
# ---------------------------------------------------------------------------


def test_c_full_interview_runs_three_rounds_in_order_with_handoffs(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client,
            headers,
            role_family="sales",
            target_role="Account executive",
            round="full",
            background_text=SALES_BACKGROUND,
        )
        long_answers = [
            f"{SALES_BACKGROUND} On this point specifically, answer {i}: I decided the "
            "approach myself and checked the result with my manager."
            for i in range(40)
        ]
        session_id, messages = interview(client, headers, intake["intake_id"], long_answers)
        transitions = [m for m in messages if m["type"] == "round_transition"]
        assert [(t["from_round"], t["to_round"]) for t in transitions] == [
            ("hr", "hiring_manager"),
            ("hiring_manager", "domain_specialist"),
        ]
        assert all(t["carried_statements"] > 0 for t in transitions)
        labels = [
            m["speaker_label"] for m in messages if m["type"] == "agent_utterance_start"
        ]
        order = list(dict.fromkeys(labels))
        assert order == ["Recruiter", "Hiring manager", "Sales specialist"]
        progress = [m for m in messages if m["type"] == "progress"]
        assert progress and all(p["round_count"] == 3 for p in progress)
        assert progress[-1]["round"] == "domain_specialist"
        # No question is repeated across rounds.
        asked = [q for q in questions(messages)]
        assert len(asked) == len(set(asked))

        report = finished_report(client, headers, session_id)
        assert [r["perspective"] for r in report["rounds"]] == [
            "hr", "hiring_manager", "domain_specialist",
        ]
        assert all(r["coverage"]["coverage_complete"] for r in report["rounds"])
        log = Path(srv.REGISTRY.session_dir(report_owner(srv, headers), session_id)) / "session.jsonl"
        events = list(read_events(log))
        assert sum(1 for e in events if e.type == "round_transition") == 2
        complete = [e for e in events if e.type == "session_complete"][0]
        assert complete.ended_reason == "complete"
        assert len(complete.rounds) == 3


def report_owner(srv, headers) -> str:
    return srv.REGISTRY.resolve(headers["Authorization"])


# ---------------------------------------------------------------------------
# E. Text lane: the transcript is evaluated; delivery is not assessed
# ---------------------------------------------------------------------------


def test_e_text_session_evaluates_the_transcript_and_not_delivery(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client, headers, files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")}
        )
        session_id, _ = interview(client, headers, intake["intake_id"], ANSWERS[:3])
        report = finished_report(client, headers, session_id)
        assert report["lane"] == "text"
        assert report["delivery"]["assessed"] is False
        assert report["delivery"]["included_in_score"] is False
        # Every quoted piece of evidence is a span of something the candidate typed.
        typed = " ".join(" ".join(a.split()) for a in ANSWERS[:3]).casefold()
        for result in report["rounds"]:
            for dim in result["dimensions"]:
                for citation in dim["citations"]:
                    assert " ".join(citation["quote"].split()).casefold() in typed
            for finding in result["findings"]:
                assert " ".join(finding["quote"].split()).casefold() in typed


# ---------------------------------------------------------------------------
# F. Results come from the completed session, with real downloads
# ---------------------------------------------------------------------------


def test_f_report_scorecard_and_transcript_belong_to_the_session(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client, headers, files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")}
        )
        session_id, _ = interview(client, headers, intake["intake_id"], ANSWERS[:2])
        finished_report(client, headers, session_id)
        transcript = client.get(f"/api/sessions/{session_id}/transcript", headers=auth(headers))
        assert transcript.status_code == 200
        assert "attachment" in transcript.headers["content-disposition"]
        assert ANSWERS[0][:60] in transcript.text
        scorecard = client.get(f"/api/sessions/{session_id}/scorecard", headers=auth(headers))
        assert scorecard.status_code == 200
        assert "scorecard" in scorecard.headers["content-disposition"]
        assert "specialist.software.v1" in scorecard.text
        # No canned fixture anywhere.
        assert "fixture" not in scorecard.text.casefold()


# ---------------------------------------------------------------------------
# G. History: two compatible sessions → an honest comparison, not a trend
# ---------------------------------------------------------------------------


def test_g_two_compatible_sessions_produce_a_comparison_not_a_trend(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client, headers, files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")}
        )
        first, _ = interview(client, headers, intake["intake_id"], ANSWERS[:2])
        finished_report(client, headers, first)
        second, _ = interview(client, headers, intake["intake_id"], ANSWERS[2:5])
        finished_report(client, headers, second)

        history = client.get("/api/history", headers=auth(headers)).json()
        assert {row["session_id"] for row in history["sessions"]} == {first, second}
        assert all(row["state"] == "complete" for row in history["sessions"])
        [comparison] = history["comparisons"]
        assert comparison["kind"] == "comparison"
        assert comparison["sessions"] == 2
        assert comparison["previous_session_id"] == first
        assert comparison["latest_session_id"] == second
        assert any("not proof of improvement" in note for note in comparison["notes"])

        # A session of a different profession is never on the same comparison.
        sales = run_intake(
            client, headers, role_family="sales", target_role="AE", background_text=SALES_BACKGROUND
        )
        third, _ = interview(client, headers, sales["intake_id"], ["I qualify on budget and timing and I walked away from one deal."])
        finished_report(client, headers, third)
        history = client.get("/api/history", headers=auth(headers)).json()
        assert len(history["comparisons"]) == 1

        plan = client.get("/api/plan", headers=auth(headers)).json()
        assert plan["based_on_sessions"] == 3
        assert plan["preparation"], "intake preparation guidance missing from the plan"


# ---------------------------------------------------------------------------
# H. Failure paths
# ---------------------------------------------------------------------------


def _docx(text: str) -> bytes:
    body = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.splitlines())
        + "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", body)
    return buffer.getvalue()


def test_h_uploads_are_validated_by_content_and_docx_is_read(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        image = client.post(
            "/api/intake",
            data={"target_role": "x", "role_family": "software"},
            files={"resume": ("scan.png", b"\x89PNG....", "image/png")},
            headers=auth(headers),
        )
        assert image.status_code == 422 and "OCR" in image.json()["error"]
        fake_pdf = client.post(
            "/api/intake",
            data={"target_role": "x", "role_family": "software"},
            files={"resume": ("cv.pdf", b"not really a pdf at all", "application/pdf")},
            headers=auth(headers),
        )
        assert fake_pdf.status_code == 422 and fake_pdf.json()["recovery"]
        too_big = client.post(
            "/api/intake",
            data={"target_role": "x", "role_family": "software"},
            files={"resume": ("cv.txt", b"a" * (5 * 1024 * 1024 + 10), "text/plain")},
            headers=auth(headers),
        )
        assert too_big.status_code == 422
        docx = run_intake(
            client, headers, files={"resume": ("ada.docx", _docx(SOFTWARE_RESUME), "application/octet-stream")}
        )
        assert docx["status"]["state"] == "ready"
        assert docx["sources"]["resume"]["kind"] == "docx"
        assert docx["claims"], "DOCX text produced no claims"
        no_context = client.post(
            "/api/intake", data={"target_role": "x"}, headers=auth(headers)
        )
        assert no_context.status_code == 422
        assert "background" in no_context.json()["error"]
        assert client.post("/api/intake", data={"target_role": "x"}).status_code == 401


def test_h_intake_failure_is_recoverable_and_blocks_a_session(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        failed = run_intake(client, headers, background_text="Hi there.")
        assert failed["status"]["state"] == "failed"
        assert failed["status"]["recovery"]
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(
                json.dumps(
                    {"type": "session_start", "auth_token": headers["_token"], "intake_id": failed["intake_id"]}
                )
            )
            rejected = read_until(ws, {"session_rejected", "session_ready"})[-1]
            assert rejected["type"] == "session_rejected"
            assert "not ready" in rejected["reason"]
        # A missing repository does not fail intake and is not held against anyone.
        repo_missing = run_intake(
            client,
            headers,
            background_text=SALES_BACKGROUND,
            repo_url="https://github.com/ada/missing",
        )
        assert repo_missing["status"]["state"] == "ready"
        assert "says nothing about you" in repo_missing["status"]["warnings"][0]


def test_h_evaluation_failure_is_shown_and_retry_recovers(srv) -> None:
    from interview.services.evaluation import NoEvaluator

    def unavailable():
        raise NoEvaluator("No evaluation model is configured.")

    srv.EVALUATION.evaluator_factory = unavailable
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client, headers, files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")}
        )
        session_id, _ = interview(client, headers, intake["intake_id"], ANSWERS[:2])
        meta = wait_for(
            lambda: client.get(f"/api/sessions/{session_id}", headers=auth(headers)).json(),
            lambda body: body.get("state") in ("complete", "failed"),
        )
        assert meta["state"] == "failed" and "No evaluation model" in meta["error"]
        assert client.get(f"/api/sessions/{session_id}/report", headers=auth(headers)).status_code == 409
        srv.EVALUATION.evaluator_factory = MockEvaluator
        assert client.post(
            f"/api/sessions/{session_id}/evaluation/retry", headers=auth(headers)
        ).json()["state"] == "evaluation_queued"
        report = finished_report(client, headers, session_id)
        assert report["session_id"] == session_id
        meta = client.get(f"/api/sessions/{session_id}", headers=auth(headers)).json()
        assert meta["evaluation_attempts"] == 2


def test_h_early_termination_records_incomplete_coverage(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client, headers, round="full", role_family="sales", background_text=SALES_BACKGROUND
        )
        session_id, messages = interview(
            client, headers, intake["intake_id"], ["I moved into sales because I wanted ownership of outcomes."]
        )
        complete = [m for m in messages if m["type"] == "session_complete"][0]
        assert complete["ended_reason"] == "client"
        report = finished_report(client, headers, session_id)
        statuses = [r["status"] for r in report["rounds"]]
        assert statuses[0] == "evaluated"
        assert statuses[1:] == ["not_reached", "not_reached"]
        assert any("ended the interview early" in line for line in report["limitations"])
        for result in report["rounds"][1:]:
            assert result["aggregate"]["score"] is None
            assert all(not d["assessed"] and d["score"] is None for d in result["dimensions"])


def test_h_session_with_no_answers_has_no_score_rather_than_a_neutral_one(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client, headers, files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")}
        )
        session_id, _ = interview(client, headers, intake["intake_id"], [])
        report = finished_report(client, headers, session_id)
        assert report["overall"]["score"] is None
        assert all(c["status"] == "untested" for c in report["claims"])
        for dim in report["rounds"][0]["dimensions"]:
            assert dim["level"] == "insufficient_evidence" and dim["score"] is None


def test_h_reconnect_resumes_without_duplicating_turns(srv) -> None:
    with TestClient(srv.app) as client:
        headers = guest(client)
        intake = run_intake(
            client, headers, files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")}
        )
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"type": "session_start", "auth_token": headers["_token"], "intake_id": intake["intake_id"]}))
            ready = read_until(ws, {"session_ready"})[-1]
            read_until(ws, {"agent_utterance_end"})
            ws.send_text(json.dumps({"type": "candidate_text", "text": ANSWERS[0]}))
            read_until(ws, {"agent_utterance_end"})
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"type": "session_resume", "token": ready["resume_token"]}))
            resumed = read_until(ws, {"session_ready"})[-1]
            assert resumed["resumed"] is True
            assert resumed["session_id"] == ready["session_id"]
            replay = read_until(ws, {"caption"})
            assert any(m["type"] == "progress" for m in replay)
            ws.send_text(json.dumps({"type": "candidate_text", "text": ANSWERS[1]}))
            read_until(ws, {"agent_utterance_end"})
            ws.send_text(json.dumps({"type": "session_end"}))
            read_until(ws, {"session_complete"})
        report = finished_report(client, headers, ready["session_id"])
        assert report["candidate_turns"] == 2


def test_h_production_refuses_unprepared_and_unvoiced_sessions(srv, monkeypatch) -> None:
    from interview.config import reset_settings_cache

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "0")
    reset_settings_cache()
    try:
        with TestClient(srv.app) as client:
            with client.websocket_connect("/ws/session") as ws:
                ws.send_text(json.dumps({"type": "session_start", "lane": "text"}))
                rejected = read_until(ws, {"session_rejected", "session_ready"})[-1]
                assert rejected["type"] == "session_rejected"
                assert "intake" in rejected["reason"]
            headers = guest(client)
            intake = run_intake(
                client,
                headers,
                lane="voice",
                files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")},
            )
            with client.websocket_connect("/ws/session") as ws:
                ws.send_text(json.dumps({"type": "session_start", "auth_token": headers["_token"], "intake_id": intake["intake_id"]}))
                rejected = read_until(ws, {"session_rejected", "session_ready"})[-1]
                assert rejected["type"] == "session_rejected"
                assert rejected["can_use_text_lane"] is True
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "1")
        reset_settings_cache()


# ---------------------------------------------------------------------------
# I. Data isolation and deletion
# ---------------------------------------------------------------------------


def test_i_candidates_cannot_reach_each_others_data_and_deletion_is_complete(srv) -> None:
    with TestClient(srv.app) as client:
        ada = guest(client)
        grace = guest(client)
        intake = run_intake(
            client, ada, files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")}
        )
        session_id, _ = interview(client, ada, intake["intake_id"], ANSWERS[:2])
        finished_report(client, ada, session_id)

        for path in (
            f"/api/intake/{intake['intake_id']}",
            f"/api/sessions/{session_id}",
            f"/api/sessions/{session_id}/report",
            f"/api/sessions/{session_id}/transcript",
            f"/api/sessions/{session_id}/scorecard",
        ):
            assert client.get(path, headers=auth(grace)).status_code == 404, path
            assert client.get(path).status_code == 401, path
        assert client.get("/api/history", headers=auth(grace)).json()["sessions"] == []
        with client.websocket_connect("/ws/session") as ws:
            ws.send_text(json.dumps({"type": "session_start", "auth_token": grace["_token"], "intake_id": intake["intake_id"]}))
            assert read_until(ws, {"session_rejected", "session_ready"})[-1]["type"] == "session_rejected"
        # A forged token for Ada's id does not work.
        ada_id = srv.REGISTRY.resolve(ada["Authorization"])
        forged = {"Authorization": f"Bearer {ada_id}.not-the-secret"}
        assert client.get("/api/history", headers=forged).status_code == 401

        grace_intake = run_intake(client, grace, role_family="sales", background_text=SALES_BACKGROUND)
        manifest = client.post("/api/me/delete", headers=auth(ada)).json()
        assert manifest["clean"] is True
        assert manifest["session_ids"] == [session_id]
        assert manifest["report_rows_deleted"] >= 1
        assert not srv.REGISTRY.candidate_dir(ada_id).exists()
        assert srv.REPORT_STORE.sessions(ada_id) == []
        assert client.get("/api/history", headers=auth(ada)).status_code == 401
        # Grace is untouched.
        assert client.get(f"/api/intake/{grace_intake['intake_id']}", headers=auth(grace)).status_code == 200


# ---------------------------------------------------------------------------
# Evaluation contract: malformed output, fabricated quotes, disagreement,
# delivery measurement
# ---------------------------------------------------------------------------


class ScriptedEvaluator:
    """FAKE evaluator returning scripted replies in order."""

    provider = "scripted"
    model = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def complete(self, messages):
        self.calls += 1
        if not self.replies:
            raise RuntimeError("no more scripted replies")
        return self.replies.pop(0)


TURNS = [
    TranscriptTurn("t-q", "agent", "Walk me through how you qualify an opportunity.", "domain_specialist", 0.0),
    TranscriptTurn("t1", "candidate", "I qualify on budget and timing. I walked away when procurement would not commit.", "domain_specialist", 1.0),
]
CLAIMS = [ClaimInput("c1", "I closed a 400k renewal.", "practical_application", "background", "candidate_assertion")]


def _valid(quote: str = "I qualify on budget and timing", claim_status: str = "held") -> str:
    dims = [
        {"dimension_id": d.id, "level": "solid", "rationale": "specific",
         "citations": [{"turn_id": "t1", "quote": quote}]}
        for d in load_pack("specialist-sales").rubric.dimensions
    ]
    return json.dumps({
        "dimensions": dims,
        "findings": [{"dimension_id": "domain_knowledge", "polarity": "strength",
                      "explanation": "Named qualification criteria.", "quote": quote,
                      "turn_id": "t1", "confidence": "moderate", "practice": "Add a number."}],
        "claims": [{"claim_id": "c1", "status": claim_status, "reason": "r", "quote": quote, "turn_id": "t1"}],
    })


async def _round(evaluator):
    return await evaluate_round(
        evaluator,
        perspective="domain_specialist",
        round_meta={"round": "domain_specialist", "label": "Sales specialist", "questions_asked": 1},
        pack=load_pack("specialist-sales"),
        turns=TURNS,
        claims=CLAIMS,
        family_label="Sales",
        target_role="AE",
        seniority="mid",
    )


async def test_malformed_output_is_retried_then_accepted() -> None:
    evaluator = ScriptedEvaluator(["not json", '{"dimensions": []}', _valid()])
    result = await _round(evaluator)
    assert evaluator.calls == 3
    assert result.round_result.attempts == 3
    assert all(d.assessed for d in result.round_result.dimensions)


async def test_persistently_malformed_output_fails_the_pass_explicitly() -> None:
    evaluator = ScriptedEvaluator(["nope", "still nope", "{}"])
    with pytest.raises(EvaluationPassFailed):
        await _round(evaluator)


async def test_fabricated_quotes_are_rejected_and_levels_downgraded() -> None:
    result = await _round(ScriptedEvaluator([_valid(quote="I single-handedly tripled revenue")]))
    rr = result.round_result
    assert all(not d.assessed and d.score is None for d in rr.dimensions)
    assert rr.findings == []
    assert rr.rejected_evidence >= 4
    assert rr.aggregate.score is None
    [(_, position)] = result.claim_positions
    assert position.status == "untested"


def test_claim_disagreement_is_kept_not_averaged() -> None:
    positions = [
        ("c1", ClaimPosition(perspective="hiring_manager", status="held", quote="a", turn_id="t1", verified=True)),
        ("c1", ClaimPosition(perspective="domain_specialist", status="collapsed", quote="b", turn_id="t2", verified=True)),
    ]
    claims, disagreements = merge_claims(CLAIMS, positions)
    assert claims[0].status == "untested"
    assert "disagreed" in claims[0].reason
    assert len(disagreements) == 1 and len(disagreements[0].positions) == 2


def test_delivery_is_measured_only_from_timings_and_against_own_baseline() -> None:
    voice_turns = [
        TranscriptTurn("a1", "candidate", " ".join(["word"] * 60), "hr", 0.0, speech_s=30.0, word_count=60),
        TranscriptTurn("a2", "candidate", " ".join(["word"] * 90), "hr", 40.0, speech_s=25.0, word_count=90),
    ]
    observed = delivery_observations("voice", voice_turns)
    assert observed.assessed is True and observed.included_in_score is False
    assert observed.baseline_turn_id == "a1"
    assert observed.measurements[0].words_per_minute == 120.0
    assert observed.measurements[1].wpm_vs_first == 1.8
    assert any("faster than your first answer" in o for o in observed.observations)
    text = delivery_observations("text", voice_turns)
    assert text.assessed is False and not text.measurements
