#!/usr/bin/env python3
"""
Consistent backup of the Shadowtrace data directory.

    python tools/ops/backup.py --data-dir /var/lib/shadowtrace/data --out-dir /var/backups/shadowtrace --keep 7
    python tools/ops/backup.py --data-dir data --out-dir backups --quiesce-url http://127.0.0.1:8000

What it does:

  1. Optionally quiesces the server: creates DATA_DIR/OPERATOR_STOP (new work
     is refused with 503; live sessions continue) and waits, up to a limit,
     until /api/diagnostics reports no live sessions and no evaluation jobs.
  2. Copies every SQLite database with sqlite3's online backup API (a
     consistent snapshot even while the server writes) and runs
     `PRAGMA integrity_check` on each copy.
  3. Copies every other file (JSON/JSONL written atomically by the app).
  4. Writes manifest.json: SHA-256 and size of every file, integrity results,
     the deletion-ledger length, and the app version from the environment.
  5. Packs a .tar.gz with owner-only permissions, prunes to --keep archives,
     and optionally copies the archive to a PRIVATE S3 prefix with the AWS CLI.
  6. Removes OPERATOR_STOP if this run created it.

Retention: an archive keeps data that a candidate later deletes, until the
archive is pruned. Keep --keep (days of daily archives) no longer than the
documented retention, and always restore with tools/ops/restore.py, which
replays the deletion ledger so erased candidates stay erased.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from contextlib import closing
from pathlib import Path

SKIP_NAMES = {"OPERATOR_STOP", ".ready-probe"}
SKIP_SUFFIXES = ("-journal", "-wal", "-shm")
SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def quiesce(url: str, data_dir: Path, wait_s: float) -> bool:
    """Create OPERATOR_STOP and wait for idle. Returns True if this call created the file."""
    stop = data_dir / "OPERATOR_STOP"
    created = not stop.exists()
    stop.touch()
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/api/diagnostics", timeout=5) as resp:
                runtime = json.load(resp).get("runtime", {})
            if not runtime.get("live_sessions") and not runtime.get("evaluation_jobs_active"):
                return created
        except Exception as exc:  # noqa: BLE001
            print(f"quiesce: diagnostics unavailable ({exc.__class__.__name__}); backing up anyway", file=sys.stderr)
            return created
        time.sleep(2)
    print(f"quiesce: still busy after {wait_s:.0f}s; SQLite copies stay consistent, files may be mid-job", file=sys.stderr)
    return created


def backup(data_dir: Path, out_dir: Path, *, keep: int = 7, label: str = "") -> Path:
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        raise SystemExit(f"data dir not found: {data_dir}")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"shadowtrace-{stamp}{('-' + label) if label else ''}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="st-backup-") as tmp:
        stage = Path(tmp) / "data"
        stage.mkdir()
        files: dict[str, dict] = {}
        integrity: dict[str, str] = {}
        for src in sorted(p for p in data_dir.rglob("*") if p.is_file()):
            rel = src.relative_to(data_dir).as_posix()
            if src.name in SKIP_NAMES or src.name.endswith(SKIP_SUFFIXES):
                continue
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.suffix in SQLITE_SUFFIXES:
                source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
                target = sqlite3.connect(dst)
                try:
                    source.backup(target)
                finally:
                    source.close()
                    target.close()
                with closing(sqlite3.connect(dst)) as check:
                    integrity[rel] = check.execute("PRAGMA integrity_check").fetchone()[0]
            else:
                shutil.copy2(src, dst)
            files[rel] = {"sha256": sha256(dst), "bytes": dst.stat().st_size}
        bad = {k: v for k, v in integrity.items() if v != "ok"}
        if bad:
            raise SystemExit(f"integrity check failed, no archive written: {bad}")
        ledger = stage / "deletion-ledger.jsonl"
        manifest = {
            "format": "shadowtrace-backup/1",
            "created_at": stamp,
            "source": str(data_dir),
            "app_version": os.environ.get("APP_VERSION", ""),
            "git_sha": os.environ.get("GIT_SHA", ""),
            "files": files,
            "sqlite_integrity": integrity,
            "deletion_ledger_entries": sum(1 for _ in ledger.open(encoding="utf-8")) if ledger.exists() else 0,
        }
        (Path(tmp) / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        archive = out_dir / f"{name}.tar.gz"
        partial = archive.with_suffix(".partial")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(Path(tmp) / "manifest.json", arcname="manifest.json")
            tar.add(stage, arcname="data")
        os.chmod(partial, 0o600)
        partial.replace(archive)
    archives = sorted(out_dir.glob("shadowtrace-*.tar.gz"))
    for old in archives[:-keep] if keep > 0 else []:
        old.unlink()
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    parser.add_argument("--out-dir", type=Path, default=Path("backups"))
    parser.add_argument("--keep", type=int, default=7, help="archives to keep (0 = keep all)")
    parser.add_argument("--label", default="")
    parser.add_argument("--quiesce-url", default="", help="pause new work via OPERATOR_STOP and wait for idle")
    parser.add_argument("--quiesce-wait-s", type=float, default=600.0)
    parser.add_argument("--s3-uri", default="", help="optional private s3://bucket/prefix/ (uses the AWS CLI)")
    args = parser.parse_args()
    created_stop = quiesce(args.quiesce_url, args.data_dir, args.quiesce_wait_s) if args.quiesce_url else False
    try:
        archive = backup(args.data_dir, args.out_dir, keep=args.keep, label=args.label)
    finally:
        if created_stop:
            (args.data_dir / "OPERATOR_STOP").unlink(missing_ok=True)
    print(f"wrote {archive} ({archive.stat().st_size} bytes)")
    if args.s3_uri:
        if not shutil.which("aws"):
            print("aws CLI not found; archive kept locally only", file=sys.stderr)
            return 1
        subprocess.run(["aws", "s3", "cp", str(archive), args.s3_uri.rstrip("/") + "/" + archive.name,
                        "--sse", "AES256", "--only-show-errors"], check=True)
        print(f"copied to {args.s3_uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
