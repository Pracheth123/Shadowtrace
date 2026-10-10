#!/usr/bin/env bash
# Switch back to the previous release (code only) and restart.
#
#   sudo deploy/scripts/rollback.sh
#
# Schema: migrations are additive (new tables/columns, never drops), so the
# previous release keeps working on the newer schema. Data written by the new
# release stays. Rolling the DATABASE back to an older state is a restore
# (tools/ops/restore.py into an isolated database first), never a switch back
# to an outdated SQLite file. See docs/DEPLOYMENT.md, "Rollback".
set -euo pipefail
ROOT=/srv/shadowtrace
PREV=$(readlink -f "$ROOT/previous" || true)
[ -n "$PREV" ] && [ -d "$PREV" ] || { echo "no previous release recorded" >&2; exit 1; }
CUR=$(readlink -f "$ROOT/current")
ln -sfn "$PREV" "$ROOT/current.new" && mv -Tf "$ROOT/current.new" "$ROOT/current"
ln -sfn "$CUR" "$ROOT/previous"
systemctl restart shadowtrace
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1 && { echo "rolled back to $(basename "$PREV")"; exit 0; }; sleep 2; done
echo "previous release is not ready either; check: journalctl -u shadowtrace -n 100" >&2; exit 1
