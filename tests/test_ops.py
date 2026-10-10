"""
Operations (roadmap item 8): readiness, operator stop, backup/restore with
deletion replay. Synthetic data in tmp_path; the server is the real app.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tarfile
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "ops"))

import backup as ops_backup  # noqa: E402
import restore as ops_restore  # noqa: E402
from tests.test_stage15_journey import (  # noqa: E402,F401 — `srv` is a fixture
    ANSWERS,
    SOFTWARE_RESUME,
    auth,
    finished_report,
    guest,
    interview,
    run_intake,
    srv,
)


# These two round trips read the SQLite report file directly. When the suite
# runs on PostgreSQL, the equivalent PostgreSQL round trip (snapshot, later
# deletion, isolated restore, reconcile) is tests/test_postgres_store.py.
sqlite_only = pytest.mark.skipif(
    os.environ.get("DATABASE_URL", "").startswith("postgres"),
    reason="SQLite-file round trip; PostgreSQL covered in test_postgres_store.py",
)


def _data_root(srv) -> Path:
    return srv.REGISTRY.root.parent


def _store_rows(db: Path, candidate_id: str) -> int:
    with closing(sqlite3.connect(db)) as conn:
        tables = [r[0] for r in conn.execute("select name from sqlite_master where type='table'")]
        total = 0
        for table in tables:
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            if "candidate_id" in columns:
                total += conn.execute(f"select count(*) from {table} where candidate_id=?", (candidate_id,)).fetchone()[0]
        return total


def _candidate_with_report(client) -> tuple[dict, str]:
    headers = guest(client)
    intake = run_intake(client, headers, files={"resume": ("ada.txt", SOFTWARE_RESUME.encode(), "text/plain")})
    session_id, _ = interview(client, headers, intake["intake_id"], ANSWERS[:2])
    finished_report(client, headers, session_id)
    return headers, client.get("/api/me", headers=auth(headers)).json()["candidate_id"]


def test_ready_reports_checks_and_operator_stop_pauses_new_work(srv) -> None:
    with TestClient(srv.app) as client:
        ok = client.get("/ready")
        assert ok.status_code == 200, ok.json()
        assert ok.json()["checks"]["data_dir_writable"] and ok.json()["checks"]["report_store"]
        headers = guest(client)
        stop = _data_root(srv) / "OPERATOR_STOP"
        stop.touch()
        try:
            assert client.get("/ready").json()["operator_stop"] is True
            paused = client.post("/api/guest")
            assert paused.status_code == 503 and "paused" in paused.json()["error"]
            intake = client.post("/api/intake", data={"consent": "1", "target_role": "x", "background_text": "y"},
                                 headers=auth(headers))
            assert intake.status_code == 503
            with client.websocket_connect("/ws/session") as ws:
                ws.send_text(json.dumps({"type": "session_start", "pack_id": "behavioral-core"}))
                rejected = json.loads(ws.receive_text())
            assert rejected["type"] == "session_rejected" and "paused" in rejected["reason"]
        finally:
            stop.unlink()
        assert client.post("/api/guest").status_code == 200


@sqlite_only
def test_backup_restore_round_trip_replays_later_deletions(srv, tmp_path: Path) -> None:
    with TestClient(srv.app) as client:
        headers_a, candidate_a = _candidate_with_report(client)
        _, candidate_b = _candidate_with_report(client)
        data = _data_root(srv)
        store = data / "reports.sqlite"
        assert _store_rows(store, candidate_a) > 0 and _store_rows(store, candidate_b) > 0

        archive = ops_backup.backup(data, tmp_path / "backups", keep=3)
        # After the backup, candidate A deletes their data.
        manifest = client.post("/api/me/delete", headers=auth(headers_a)).json()
        assert manifest["clean"] is True
    ledger = data / "deletion-ledger.jsonl"
    assert candidate_a in ledger.read_text(encoding="utf-8")

    # The archive itself verifies.
    assert ops_restore.restore(archive, None, verify_only=True)["verified"] is True
    target = tmp_path / "restored"
    summary = ops_restore.restore(archive, target, ledger=ledger)
    assert summary["deletions"]["dirs_removed"] == 1
    assert summary["deletions"]["store_rows_removed"] > 0
    # A stays deleted even though the archive predates the deletion; B survives intact.
    assert not (target / "candidates" / candidate_a).exists()
    assert _store_rows(target / "reports.sqlite", candidate_a) == 0
    assert (target / "candidates" / candidate_b / "identity.json").is_file()
    assert _store_rows(target / "reports.sqlite", candidate_b) > 0
    assert candidate_a in (target / "deletion-ledger.jsonl").read_text(encoding="utf-8")


@sqlite_only
def test_restore_refuses_a_tampered_archive_and_keeps_existing_data(srv, tmp_path: Path) -> None:
    with TestClient(srv.app) as client:
        _candidate_with_report(client)
    archive = ops_backup.backup(_data_root(srv), tmp_path / "b", keep=0)
    # Rewrite the archive with one file altered.
    work = tmp_path / "x"
    with tarfile.open(archive) as tar:
        tar.extractall(work, filter="data")
    victim = next(p for p in (work / "data").rglob("*.json"))
    victim.write_text(victim.read_text(encoding="utf-8") + " ", encoding="utf-8")
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        tar.add(work / "manifest.json", arcname="manifest.json")
        tar.add(work / "data", arcname="data")
    target = tmp_path / "live"
    target.mkdir()
    (target / "keep.txt").write_text("existing", encoding="utf-8")
    with pytest.raises(ops_restore.RestoreRefused, match="checksum mismatch"):
        ops_restore.restore(bad, target)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "existing"


def test_backup_prunes_to_keep_and_skips_operator_files(srv, tmp_path: Path) -> None:
    data = _data_root(srv)
    data.mkdir(parents=True, exist_ok=True)
    (data / "OPERATOR_STOP").touch()
    out = tmp_path / "b"
    names = []
    for label in ("a", "b", "c"):
        names.append(ops_backup.backup(data, out, keep=2, label=label).name)
    assert sorted(p.name for p in out.glob("*.tar.gz")) == sorted(names[1:])
    with tarfile.open(out / names[-1]) as tar:
        assert not any(m.name.endswith("OPERATOR_STOP") for m in tar.getmembers())
