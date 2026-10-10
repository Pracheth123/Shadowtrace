"""
Storage administration. Reads DATABASE_URL and DATA_DIR from the environment
(or .env); prints host names and counts, never the URL or a password.

    python -m interview.storage status
    python -m interview.storage migrate                 # with the migration role's DATABASE_URL
    python -m interview.storage reconcile [--apply]     # index ↔ report files
    python -m interview.storage import-sqlite data/reports.sqlite [--apply]
    python -m interview.storage export DIR | import DIR  # index snapshot (backup/restore)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from interview.config import get_settings
from interview.llm.env import load_dotenv


def _store():
    from interview.storage import open_report_store

    settings = get_settings()
    return settings, open_report_store(settings, Path(settings.data_dir))


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m interview.storage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("migrate")
    rec = sub.add_parser("reconcile")
    rec.add_argument("--apply", action="store_true")
    imp = sub.add_parser("import-sqlite")
    imp.add_argument("source", type=Path)
    imp.add_argument("--apply", action="store_true", help="write (default is a dry run)")
    imp.add_argument("--sample", type=int, default=5)
    ex = sub.add_parser("export")
    ex.add_argument("dir", type=Path)
    im = sub.add_parser("import")
    im.add_argument("dir", type=Path)
    args = parser.parse_args(argv)

    from interview.storage import DatabaseUnavailable, StorageConfigError

    try:
        settings, store = _store()
    except (DatabaseUnavailable, StorageConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "status":
        out = {"backend": store.backend, "schema_ready": store.schema_ready(), "reachable": store.ping(),
               "counts": store.counts(), **{k: v for k, v in store.describe().items() if k != "backend"}}
    elif args.cmd == "migrate":
        if store.backend != "postgres":
            out = {"backend": "sqlite", "note": "SQLite migrates itself on open; nothing to do."}
        else:
            out = {"applied": store.migrate(), "schema_ready": store.schema_ready()}
    elif args.cmd == "reconcile":
        from interview.storage.reconcile import reconcile

        out = reconcile(store, Path(settings.data_dir) / "candidates", apply=args.apply).as_dict()
    elif args.cmd == "import-sqlite":
        if store.backend != "postgres":
            print("error: set DATABASE_URL to the target PostgreSQL database", file=sys.stderr)
            return 2
        from interview.storage.ops import import_sqlite

        out = import_sqlite(args.source, store, dry_run=not args.apply, sample=args.sample)
    elif args.cmd in ("export", "import"):
        if store.backend != "postgres":
            print("error: snapshots apply to PostgreSQL; SQLite is backed up as a file", file=sys.stderr)
            return 2
        from interview.storage.ops import export_snapshot, import_snapshot

        out = export_snapshot(store, args.dir) if args.cmd == "export" else import_snapshot(store, args.dir)
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
