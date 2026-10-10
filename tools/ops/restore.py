#!/usr/bin/env python3
"""
Verify and restore a Shadowtrace backup, then re-apply deletions.

    python tools/ops/restore.py backups/shadowtrace-20261010T020000Z.tar.gz --verify-only
    python tools/ops/restore.py ARCHIVE --target /var/lib/shadowtrace/data --ledger /var/lib/shadowtrace/data/deletion-ledger.jsonl

Run with the server STOPPED (systemctl stop shadowtrace). Steps:

  1. Extract to a staging directory; refuse unsafe paths in the archive.
  2. Verify every file's SHA-256 and size against manifest.json and run
     `PRAGMA integrity_check` on each SQLite database. Any mismatch aborts.
  3. Replay deletions: the union of the archive's deletion ledger and the
     current one (--ledger, default TARGET/deletion-ledger.jsonl if it exists).
     Each listed candidate's directory and report-store rows are removed, so a
     candidate who deleted their data after the backup was taken stays deleted.
  4. Move an existing TARGET aside to TARGET.pre-restore-<time> (never deleted
     by this script), then move the verified staging copy into place.

Interrupted evaluation jobs in the restored data are reported honestly by the
server on first read ("interrupted — retry"); completed rounds are kept.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")


class RestoreRefused(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def extract(archive: Path, into: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            target = (into / member.name).resolve()
            if not str(target).startswith(str(into.resolve())) or member.issym() or member.islnk():
                raise RestoreRefused(f"unsafe path in archive: {member.name}")
        tar.extractall(into, filter="data")  # members validated above as well
    manifest_path = into / "manifest.json"
    if not manifest_path.is_file():
        raise RestoreRefused("archive has no manifest.json")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def verify(stage: Path, manifest: dict) -> list[str]:
    problems = []
    if manifest.get("format") != "shadowtrace-backup/1":
        problems.append(f"unknown format {manifest.get('format')!r}")
    listed = manifest.get("files") or {}
    present = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
    for rel in sorted(set(listed) - present):
        problems.append(f"missing: {rel}")
    for rel in sorted(present - set(listed)):
        problems.append(f"not in manifest: {rel}")
    for rel, info in listed.items():
        path = stage / rel
        if not path.is_file():
            continue
        if path.stat().st_size != info["bytes"] or sha256(path) != info["sha256"]:
            problems.append(f"checksum mismatch: {rel}")
        if path.suffix in SQLITE_SUFFIXES:
            with closing(sqlite3.connect(path)) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                problems.append(f"sqlite integrity: {rel}: {result}")
    return problems


def read_ledger(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("candidate_id"):
            entries.append(entry)
    return entries


def _delete_store_rows(db: Path, candidate_id: str) -> int:
    """Remove a candidate's rows from every table that has a candidate_id column."""
    removed = 0
    with closing(sqlite3.connect(db)) as conn:
        tables = [r[0] for r in conn.execute("select name from sqlite_master where type='table'")]
        for table in tables:
            columns = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            if "candidate_id" in columns:
                removed += conn.execute(f'delete from "{table}" where candidate_id = ?', (candidate_id,)).rowcount
        conn.commit()
    return removed


def replay_deletions(data: Path, entries: list[dict]) -> dict:
    """Remove each ledger candidate from a (not running) data directory."""
    from interview.candidates import CandidateRegistry

    registry = CandidateRegistry(data / "candidates")
    databases = [p for p in data.rglob("*") if p.is_file() and p.suffix in SQLITE_SUFFIXES]
    removed_dirs = rows = 0
    ids = sorted({e["candidate_id"] for e in entries})
    for candidate_id in ids:
        try:
            directory = registry.candidate_dir(candidate_id)
        except Exception:  # noqa: BLE001 — an unsafe id is skipped, never followed
            continue
        if directory.exists():
            registry.delete(candidate_id)
            removed_dirs += 1
        for db in databases:
            rows += _delete_store_rows(db, candidate_id)
    return {"ledger_candidates": len(ids), "dirs_removed": removed_dirs, "store_rows_removed": rows}


def restore(archive: Path, target: Path | None, *, ledger: Path | None = None, verify_only: bool = False) -> dict:
    with tempfile.TemporaryDirectory(prefix="st-restore-") as tmp:
        root = Path(tmp)
        manifest = extract(archive, root)
        stage = root / "data"
        problems = verify(stage, manifest)
        if problems:
            raise RestoreRefused("verification failed: " + "; ".join(problems[:10]))
        summary = {"archive": str(archive), "created_at": manifest.get("created_at"),
                   "files": len(manifest.get("files") or {}), "verified": True}
        if verify_only:
            return summary
        assert target is not None
        current_ledger = ledger or (target / "deletion-ledger.jsonl")
        entries = read_ledger(stage / "deletion-ledger.jsonl") + read_ledger(current_ledger)
        summary["deletions"] = replay_deletions(stage, entries)
        merged = {(e["candidate_id"], e.get("deleted_at", "")): e for e in entries}
        if merged:
            (stage / "deletion-ledger.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in merged.values()), encoding="utf-8")
        if target.exists() and any(target.iterdir()):
            aside = target.with_name(f"{target.name}.pre-restore-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
            target.rename(aside)
            summary["previous_data_moved_to"] = str(aside)
        elif target.exists():
            target.rmdir()
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage), str(target))
        summary["restored_to"] = str(target)
        return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("archive", type=Path)
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument("--ledger", type=Path, default=None, help="current deletion ledger to replay as well")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not args.verify_only and args.target is None:
        parser.error("--target is required unless --verify-only")
    try:
        summary = restore(args.archive, args.target, ledger=args.ledger, verify_only=args.verify_only)
    except RestoreRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
