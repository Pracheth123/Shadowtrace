#!/usr/bin/env bash
# Coordinated backup: pause new work, wait for idle, snapshot DATA_DIR files and
# the PostgreSQL report index together, resume, optionally copy to private S3.
# Run by shadowtrace-backup.timer (daily) or by hand as root.
set -euo pipefail
set -a; . /etc/shadowtrace/shadowtrace.env; . /etc/shadowtrace/migrate.env; set +a
OUT=/var/lib/shadowtrace/backups
ARGS=(--data-dir "$DATA_DIR" --out-dir "$OUT" --keep "${BACKUP_KEEP:-7}" --quiesce-url http://127.0.0.1:8000)
[ -n "${BACKUP_S3_URI:-}" ] && ARGS+=(--s3-uri "$BACKUP_S3_URI")
/srv/shadowtrace/current/.venv/bin/python /srv/shadowtrace/current/tools/ops/backup.py "${ARGS[@]}"
# The deletion ledger is copied separately: it must outlive every archive.
cp -f "$DELETION_LEDGER_PATH" "$OUT/deletion-ledger.latest.jsonl" 2>/dev/null || true
[ -n "${BACKUP_S3_URI:-}" ] && [ -f "$DELETION_LEDGER_PATH" ] && \
  aws s3 cp "$DELETION_LEDGER_PATH" "${BACKUP_S3_URI%/}/deletion-ledger.jsonl" --sse AES256 --only-show-errors || true
