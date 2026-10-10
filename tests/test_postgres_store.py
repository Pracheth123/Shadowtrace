"""
Report store on both backends, against a REAL PostgreSQL server.

Run with an admin-capable URL (each test creates and drops its own database):

    TEST_DATABASE_URL=postgresql://user:pass@127.0.0.1:55432/postgres python -m pytest -q tests/test_postgres_store.py

Skipped when TEST_DATABASE_URL is unset or the driver is missing. No SQL is
mocked here; the SQLite cases run everywhere.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient

from interview.roadmap.reports import ReportStore

ADMIN_URL = os.environ.get("TEST_DATABASE_URL", "")
psycopg = pytest.importorskip("psycopg") if ADMIN_URL else None
needs_pg = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set (real PostgreSQL required)")


def _db_url(name: str) -> str:
    parts = urlsplit(ADMIN_URL)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@pytest.fixture
def pg_url():
    """A brand-new, empty database for one test; dropped afterwards."""
    name = f"st_t_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield _db_url(name)
    finally:
        from interview import storage

        for url, store in list(storage._PG_STORES.items()):
            if url == _db_url(name):
                store.close()
                storage._PG_STORES.pop(url, None)
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def pg_store(url: str, **kw):
    from interview.storage import PostgresReportStore

    return PostgresReportStore(url, auto_migrate=kw.pop("auto_migrate", True), max_size=kw.pop("max_size", 8), **kw)


def report(session_id: str, *, score=0.5, gaps=1, kind="interview", family="software") -> dict:
    return {
        "session_id": session_id,
        "created_at": "2026-10-10T10:00:00.000Z",
        "config": {"target_role": "Engineer", "role_family": family, "round": "full",
                   "intensity": "realistic", "seniority": "mid"},
        "lane": "text",
        "ended_reason": "complete",
        "session_kind": kind,
        "evaluator": {"provider": "mock"},
        "overall": {"score": score},
        "rounds": [{
            "round": "hr", "rubric_version": "hr.v1",
            "dimensions": [{"dimension_id": "clarity", "label": "Clarity", "level": "solid", "score": score, "assessed": True},
                           {"dimension_id": "fit", "label": "Fit", "level": "insufficient_evidence", "score": None, "assessed": False}],
            "findings": [{"polarity": "gap", "round": "hr", "dimension_id": "clarity", "dimension_label": "Clarity",
                          "explanation": f"gap {i}", "quote": "I did it", "turn_id": f"t{i}", "finding_id": f"f{i}"}
                         for i in range(gaps)],
        }],
    }


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=needs_pg)])
def store(request, tmp_path):
    if request.param == "sqlite":
        yield ReportStore(tmp_path / "reports.sqlite")
        return
    url = request.getfixturevalue("pg_url")
    s = pg_store(url)
    try:
        yield s
    finally:
        s.close()


# ----------------------------------------------------------- both backends

def test_recording_twice_replaces_rather_than_duplicates(store) -> None:
    store.record("cand-a", report("s1", score=0.4, gaps=3))
    store.record("cand-a", report("s1", score=0.7, gaps=1))
    rows = store.sessions("cand-a")
    assert len(rows) == 1 and abs(rows[0]["overall_score"] - 0.7) < 1e-9
    assert len(store.gaps(["s1"])) == 1 and len(store.dimensions(["s1"])) == 2
    # Not-assessed dimensions keep a NULL score, never a zero.
    assert [d["score"] for d in store.dimensions(["s1"]) if d["dimension_id"] == "fit"] == [None]


def test_ownership_is_enforced_and_isolated(store) -> None:
    store.record("cand-a", report("s1"))
    with pytest.raises(PermissionError):
        store.record("cand-b", report("s1"))
    assert store.sessions("cand-b") == [] and len(store.sessions("cand-a")) == 1


def test_practice_rows_are_kept_out_of_interview_trends(store) -> None:
    store.record("cand-a", report("s1"))
    store.record("cand-a", report("p1", kind="practice"))
    assert [r["session_id"] for r in store.sessions("cand-a")] == ["s1"]
    assert {r["session_id"] for r in store.sessions("cand-a", kind=None)} == {"s1", "p1"}


def test_candidate_erasure_removes_every_row(store) -> None:
    store.record("cand-a", report("s1", gaps=2))
    store.record("cand-a", report("s2"))
    store.record("cand-b", report("s3"))
    assert store.delete_candidate("cand-a") == 2 + 4 + 3  # sessions + dims + gaps
    assert store.sessions("cand-a", kind=None) == [] and store.gaps(["s1", "s2"]) == []
    assert len(store.sessions("cand-b")) == 1


# ----------------------------------------------------------- PostgreSQL only

@needs_pg
def test_migrations_are_explicit_versioned_and_idempotent(pg_url) -> None:
    from interview.storage import EXPECTED_VERSION

    s = pg_store(pg_url, auto_migrate=False)
    try:
        assert s.schema_version() == 0 and s.schema_ready() is False
        threads = [threading.Thread(target=s.migrate) for _ in range(4)]  # concurrent migrators
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert s.schema_version() == EXPECTED_VERSION and s.schema_ready()
        assert s.migrate() == []
        with psycopg.connect(pg_url) as conn:
            assert conn.execute("select count(*) from schema_migrations").fetchone()[0] == EXPECTED_VERSION
    finally:
        s.close()


@needs_pg
def test_concurrent_writes_of_one_session_leave_one_consistent_set(pg_url) -> None:
    s = pg_store(pg_url, max_size=8)
    try:
        errors: list[BaseException] = []

        def write(n: int) -> None:
            try:
                s.record("cand-a", report("s1", score=n / 10, gaps=n % 3 + 1))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        row = s.sessions("cand-a")[0]
        n = round(row["overall_score"] * 10)
        assert len(s.gaps(["s1"])) == n % 3 + 1  # the gaps belong to the same write as the score
        assert s.counts()["session_reports"] == 1
    finally:
        s.close()


@needs_pg
def test_a_failed_write_rolls_back_and_keeps_the_previous_rows(pg_url) -> None:
    s = pg_store(pg_url)
    try:
        s.record("cand-a", report("s1", score=0.4, gaps=2))
        bad = report("s1", score=0.9)
        bad["rounds"][0]["dimensions"][0]["score"] = "not-a-number"  # fails inside the transaction
        with pytest.raises(Exception):
            s.record("cand-a", bad)
        rows = s.sessions("cand-a")
        assert abs(rows[0]["overall_score"] - 0.4) < 1e-9 and len(s.gaps(["s1"])) == 2
        assert s.ping()  # the pooled connection is healthy after the rollback
    finally:
        s.close()


@needs_pg
def test_unreachable_database_fails_clearly_without_leaking_credentials() -> None:
    from interview.storage import DatabaseUnavailable, PostgresReportStore

    with pytest.raises(DatabaseUnavailable) as info:
        PostgresReportStore("postgresql://someone:hunter2-secret@127.0.0.1:1/x", connect_timeout_s=1, host_label="127.0.0.1:1/x")
    assert "hunter2" not in str(info.value) and "SQLite" in str(info.value)


def test_production_refuses_sqlite_and_unverified_tls(tmp_path, monkeypatch) -> None:
    from interview.config import Settings
    from interview.storage import StorageConfigError, open_report_store

    env = tmp_path / "e.env"
    env.write_text("", encoding="utf-8")
    base = dict(_env_file=env, APP_ENV="prod", ALLOW_MOCK_PROVIDERS=False, MOCK_LLM=False, GROQ_API_KEY="k")
    with pytest.raises(StorageConfigError, match="DATABASE_URL"):
        open_report_store(Settings(**base, DATABASE_URL=""), tmp_path)  # type: ignore[arg-type]
    with pytest.raises(StorageConfigError, match="verify-full"):
        open_report_store(Settings(**base, DATABASE_URL="postgresql://u:p@db.example:5432/st?sslmode=require"), tmp_path)  # type: ignore[arg-type]
    assert not (tmp_path / "reports.sqlite").exists(), "no SQLite file is created on refusal"
    ok = Settings(**base, DATABASE_URL="", ALLOW_SQLITE_IN_PRODUCTION=True)  # type: ignore[arg-type]
    assert open_report_store(ok, tmp_path).backend == "sqlite"
    with pytest.raises(ValueError, match="postgresql://"):
        Settings(_env_file=env, DATABASE_URL="mysql://x")  # type: ignore[arg-type]


@needs_pg
def test_sqlite_import_is_dry_run_first_restartable_and_validated(pg_url, tmp_path) -> None:
    from interview.storage.ops import import_sqlite

    src = tmp_path / "reports.sqlite"
    lite = ReportStore(src)
    for c in range(4):
        for k in range(3):
            lite.record(f"cand-{c}", report(f"s{c}-{k}", score=(c + k) / 10, gaps=k))
    lite.record("cand-0", report("p0", kind="practice"))
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    target = pg_store(pg_url)
    try:
        target.record("someone-else", report("s1-1"))  # a conflicting id already in the target
        plan = import_sqlite(src, target, dry_run=True)
        assert plan["sessions"] == 13 and plan["conflicts"] == ["s1-1"] and target.counts()["session_reports"] == 1
        first = import_sqlite(src, target, dry_run=False)
        again = import_sqlite(src, target, dry_run=False)  # rerun after "interruption"
        assert first["imported_sessions"] == again["imported_sessions"] == 12
        assert target.counts()["session_reports"] == 13  # 12 imported + the conflicting original
        assert again["validation"]["ok"], again["validation"]
        assert len(target.sessions("cand-0", kind=None)) == 4
        assert hashlib.sha256(src.read_bytes()).hexdigest() == before, "source never modified"
    finally:
        target.close()


# ------------------------------------- the app on PostgreSQL, end to end

@needs_pg
def test_app_journey_backup_deletion_restore_on_postgres(pg_url, tmp_path, monkeypatch, request) -> None:
    from interview.config import reset_settings_cache

    monkeypatch.setenv("DATABASE_URL", pg_url)
    ledger = tmp_path / "ledger" / "deletion-ledger.jsonl"  # outside DATA_DIR, like production
    monkeypatch.setenv("DELETION_LEDGER_PATH", str(ledger))
    reset_settings_cache()
    server = request.getfixturevalue("srv")  # the real app, services built on PostgreSQL
    assert server.REPORT_STORE.backend == "postgres"
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "ops"))
    import backup as ops_backup
    import restore as ops_restore
    from tests.test_stage15_journey import ANSWERS, SOFTWARE_RESUME, auth, finished_report, guest, interview, run_intake

    def candidate(client):
        headers = guest(client)
        intake = run_intake(client, headers, files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")})
        session_id, _ = interview(client, headers, intake["intake_id"], ANSWERS[:2])
        finished_report(client, headers, session_id)
        return headers, client.get("/api/me", headers=auth(headers)).json()["candidate_id"], session_id

    with TestClient(server.app) as client:
        ready = client.get("/ready").json()
        assert ready["ready"] and ready["report_store_backend"] == "postgres"
        assert ready["checks"]["report_store_schema"] is True
        headers_a, cand_a, _ = candidate(client)
        headers_b, cand_b, sess_b = candidate(client)
        # History is served from PostgreSQL and is isolated per guest.
        hist_b = client.get("/api/history", headers=auth(headers_b)).json()
        assert [r["session_id"] for r in hist_b["sessions"]] == [sess_b]
        assert server.REPORT_STORE.sessions(cand_a) and server.REPORT_STORE.sessions(cand_b)

        data_root = server.REGISTRY.root.parent
        archive = ops_backup.backup(data_root, tmp_path / "backups", keep=3, pg_store=server.REPORT_STORE)
        assert client.post("/api/me/delete", headers=auth(headers_a)).json()["clean"] is True
        assert server.REPORT_STORE.sessions(cand_a, kind=None) == []
    assert cand_a in ledger.read_text(encoding="utf-8")
    assert not (data_root / "deletion-ledger.jsonl").exists(), "ledger lives outside DATA_DIR"

    # Restore into an ISOLATED database and directory, as the runbook says.
    from interview.storage import PostgresReportStore

    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute('DROP DATABASE IF EXISTS "st_restore_check" WITH (FORCE)')
        conn.execute('CREATE DATABASE "st_restore_check"')
    isolated = PostgresReportStore(_db_url("st_restore_check"), auto_migrate=False)
    try:
        summary = ops_restore.restore(archive, tmp_path / "restored", ledger=ledger, pg_store=isolated)
        assert summary["postgres_snapshot"] and summary["deletions"]["postgres_rows_removed"] > 0
        assert isolated.sessions(cand_a, kind=None) == [], "a deletion after the backup stays applied"
        assert isolated.sessions(cand_b), "other candidates are restored"
        assert not (tmp_path / "restored" / "candidates" / cand_a).exists()
        assert summary["reconcile_after"]["consistent"] is True
        assert (tmp_path / "restored" / "OPERATOR_STOP").exists(), "restored service stays in maintenance"
    finally:
        isolated.close()
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            conn.execute('DROP DATABASE IF EXISTS "st_restore_check" WITH (FORCE)')
    reset_settings_cache()


@needs_pg
def test_readiness_reports_unreachable_database_and_missing_schema(pg_url, tmp_path, monkeypatch, request) -> None:
    from interview.config import reset_settings_cache
    from interview.storage import PostgresReportStore

    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("DB_AUTO_MIGRATE", "0")
    reset_settings_cache()
    server = request.getfixturevalue("srv")
    with TestClient(server.app) as client:
        body = client.get("/ready")
        assert body.status_code == 503 and body.json()["checks"]["report_store_schema"] is False
        server.REPORT_STORE.migrate()
        assert client.get("/ready").status_code == 200
        broken = PostgresReportStore(pg_url, auto_migrate=False)
        broken.close()  # the database becomes unreachable for this store
        monkeypatch.setattr(server, "REPORT_STORE", broken)
        down = client.get("/ready")
        assert down.status_code == 503 and down.json()["checks"]["report_store"] is False
        assert "password" not in down.text.lower()
    reset_settings_cache()


from tests.test_stage15_journey import srv  # noqa: E402,F401 — fixture reuse
