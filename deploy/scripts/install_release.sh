#!/usr/bin/env bash
# Install a release on the host (run as root, e.g. via SSM Session Manager).
#
#   sudo deploy/scripts/install_release.sh /tmp/shadowtrace-<sha>.tar.gz [--maintenance]
#
# 1. verify the tarball checksum (if the .sha256 is beside it)
# 2. unpack to /srv/shadowtrace/releases/<sha>, build its own venv from the locks
# 3. optional: pause new work (OPERATOR_STOP) and drain live sessions/evaluations
# 4. apply schema migrations with the MIGRATION role (/etc/shadowtrace/migrate.env)
# 5. switch /srv/shadowtrace/current atomically (previous kept as /srv/shadowtrace/previous)
# 6. restart the one-worker service and wait for /ready; on failure switch back
#    and restart the previous release (migrations are additive, see DEPLOYMENT.md)
set -euo pipefail
TARBALL=${1:?usage: install_release.sh <tarball> [--maintenance]}
MAINT=${2:-}
ROOT=/srv/shadowtrace
DATA_DIR=/var/lib/shadowtrace/data
PY=${PYTHON:-python3.12}  # requirements-lock.txt needs Python >= 3.12 (numpy 2.5.x)

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
if [ -f "$TARBALL.sha256" ]; then (cd "$(dirname "$TARBALL")" && sha256sum -c "$(basename "$TARBALL").sha256"); fi

NAME=$(tar -tzf "$TARBALL" | sed -n '1s#/.*##p')
SHA=${NAME#shadowtrace-}
DEST=$ROOT/releases/$SHA
if [ ! -d "$DEST" ]; then
  tar -xzf "$TARBALL" -C "$ROOT/releases"
  mv "$ROOT/releases/$NAME" "$DEST"
  "$PY" -m venv "$DEST/.venv"
  "$DEST/.venv/bin/python" -m pip install -q --upgrade pip
  "$DEST/.venv/bin/python" -m pip install -q -r "$DEST/requirements-lock.txt" -r "$DEST/requirements-postgres.txt"
  "$DEST/.venv/bin/python" -m pip install -q --no-deps "$DEST"
  chown -R root:shadowtrace "$DEST"; chmod -R g+rX,o-rwx "$DEST"
  # Caddy (its own user) serves the public static build: traverse the release
  # directory without listing it, and read client/ only. Code and venv stay private.
  chmod o+x "$DEST"; chmod -R o+rX "$DEST/client"
fi
sed -i "s/^GIT_SHA=.*/GIT_SHA=$SHA/" /etc/shadowtrace/shadowtrace.env

if [ "$MAINT" = "--maintenance" ]; then
  "$DEST/deploy/scripts/maintenance.sh" drain
fi

# Migrations with the migration role. The runtime role cannot create tables.
set -a; . /etc/shadowtrace/shadowtrace.env; . /etc/shadowtrace/migrate.env; set +a
"$DEST/.venv/bin/python" -m interview.storage migrate

# Only an existing symlink names a previous release (readlink -f of a missing
# path returns the path itself, which would make rollback point at itself).
PREV=""
[ -L "$ROOT/current" ] && PREV=$(readlink -f "$ROOT/current")
ln -sfn "$DEST" "$ROOT/current.new" && mv -Tf "$ROOT/current.new" "$ROOT/current"
[ -n "$PREV" ] && [ "$PREV" != "$DEST" ] && ln -sfn "$PREV" "$ROOT/previous"
systemctl restart shadowtrace

ok=0
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [ $ok != 1 ]; then
  echo "release $SHA did not become ready; rolling back" >&2
  curl -sS http://127.0.0.1:8000/ready || true
  if [ -n "$PREV" ]; then
    ln -sfn "$PREV" "$ROOT/current.new" && mv -Tf "$ROOT/current.new" "$ROOT/current"
    systemctl restart shadowtrace
  fi
  exit 1
fi
echo "release $SHA is live"
[ "$MAINT" = "--maintenance" ] && echo "maintenance is still ON: run deploy/scripts/maintenance.sh off after checking"
# Keep the three newest releases plus whatever current/previous point at.
cd "$ROOT/releases" && ls -1t | tail -n +4 | while read -r old; do
  [ "$ROOT/releases/$old" = "$(readlink -f "$ROOT/current")" ] && continue
  [ "$ROOT/releases/$old" = "$(readlink -f "$ROOT/previous" 2>/dev/null)" ] && continue
  rm -rf "${ROOT:?}/releases/$old"
done
