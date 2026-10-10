#!/usr/bin/env bash
# Local production rehearsal (Docker). No AWS account is used.
#
#   deploy/rehearsal/rehearse.sh dist-release/shadowtrace-<sha>.tar.gz
#
# Stands up, on a private Docker network:
#   - "db.rehearsal.internal": PostgreSQL 16.4 with TLS from a throwaway CA
#     (stands in for RDS + its CA bundle; the app connects with verify-full);
#   - "app": Amazon Linux 2023 with python3.12, the pinned Caddy binary
#     (checksum verified), and the release installed by the REAL
#     deploy/scripts/install_release.sh, rollback.sh, maintenance.sh and backup.sh.
# systemd does not run in a container, so a small `systemctl` shim starts the
# service from the unit's ExecStart line (deploy/systemd/shadowtrace.service).
#
# Checks: roles.sql least privilege, migrations by the migration role, prod
# readiness, HTTPS + WSS through Caddy, FastAPI not reachable on 8000 from
# outside, backup with quiesce, maintenance on/off, rollback, and automatic
# rollback of a release that fails readiness. Writes a log under logs/rehearsal/.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: do not rewrite "/CN=..." or container paths
# Host paths in a form native tools (openssl.exe, docker.exe) accept; no-op on Linux.
hostpath() { cygpath -m "$1" 2>/dev/null || printf '%s' "$1"; }
TARBALL=$(hostpath "$(realpath "${1:?usage: rehearse.sh <release tarball>}")")
HERE=$(hostpath "$(cd "$(dirname "$0")" && pwd)")
REPO=$(hostpath "$(cd "$HERE/../.." && pwd)")
WORK=$(hostpath "$(mktemp -d)")
NET=st-rehearsal
LOG="$REPO/logs/rehearsal"; mkdir -p "$LOG"
containers_down() { docker rm -f st-reh-db st-reh-app >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
cleanup() { containers_down; rm -rf "$WORK"; }
containers_down   # leftovers from an interrupted earlier run
trap cleanup EXIT

rand() { openssl rand -hex 16; }
ADMIN_PW=$(rand); APP_PW=$(rand); MIG_PW=$(rand)

echo "== throwaway CA and server certificate for db.rehearsal.internal"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=rehearsal-ca" \
  -keyout "$WORK/ca.key" -out "$WORK/ca.pem" </dev/null 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=db.rehearsal.internal" \
  -keyout "$WORK/server.key" -out "$WORK/server.csr" </dev/null 2>/dev/null
printf "subjectAltName=DNS:db.rehearsal.internal\n" > "$WORK/san.ext"
openssl x509 -req -in "$WORK/server.csr" -CA "$WORK/ca.pem" -CAkey "$WORK/ca.key" -CAcreateserial \
  -days 2 -extfile "$WORK/san.ext" -out "$WORK/server.crt" </dev/null 2>/dev/null

docker network create "$NET" >/dev/null
MSYS_NO_PATHCONV=1 docker run -d --name st-reh-db --network "$NET" --network-alias db.rehearsal.internal \
  -e POSTGRES_USER=st_admin -e POSTGRES_PASSWORD="$ADMIN_PW" -e POSTGRES_DB=shadowtrace \
  postgres:16.4 >/dev/null
for f in server.crt server.key ca.pem; do docker cp "$WORK/$f" st-reh-db:/tmp/$f; done
until docker exec st-reh-db pg_isready -U st_admin -d shadowtrace >/dev/null 2>&1; do sleep 1; done
sleep 2
docker exec st-reh-db bash -c 'cp /tmp/server.crt /tmp/server.key /var/lib/postgresql/data/ && chown postgres /var/lib/postgresql/data/server.* && chmod 600 /var/lib/postgresql/data/server.key'
docker exec -u postgres st-reh-db bash -c "psql -U st_admin -d shadowtrace -qc \"ALTER SYSTEM SET ssl = on\" && pg_ctl reload -D /var/lib/postgresql/data" >/dev/null
sleep 2

echo "== roles.sql as the master user, then passwords (rehearsal values only)"
docker cp "$REPO/deploy/sql/roles.sql" st-reh-db:/tmp/roles.sql
docker exec st-reh-db psql -U st_admin -d shadowtrace -q -f /tmp/roles.sql
docker exec st-reh-db psql -U st_admin -d shadowtrace -qc "ALTER ROLE shadowtrace_app PASSWORD '$APP_PW'; ALTER ROLE shadowtrace_migrator PASSWORD '$MIG_PW';"

echo "== Amazon Linux 2023 app host"
MSYS_NO_PATHCONV=1 docker run -d --name st-reh-app --network "$NET" amazonlinux:2023 sleep infinity >/dev/null
docker cp "$TARBALL" st-reh-app:/tmp/
[ -f "$TARBALL.sha256" ] && docker cp "$TARBALL.sha256" st-reh-app:/tmp/
docker cp "$WORK/ca.pem" st-reh-app:/tmp/rds-ca.pem
docker cp "$HERE/host_steps.sh" st-reh-app:/tmp/host_steps.sh
docker exec -e APP_PW="$APP_PW" -e MIG_PW="$MIG_PW" -e TARBALL="/tmp/$(basename "$TARBALL")" \
  st-reh-app bash /tmp/host_steps.sh 2>&1 | tee "$LOG/rehearsal-$(date +%Y%m%dT%H%M%S).log"
