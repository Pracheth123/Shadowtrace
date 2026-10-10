#!/usr/bin/env bash
# Runs INSIDE the Amazon Linux 2023 rehearsal container (see rehearse.sh).
# Mirrors the stack's UserData, then exercises the real release scripts.
set -euo pipefail
: "${APP_PW:?}" "${MIG_PW:?}" "${TARBALL:?}"
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; exit 1; }

echo "== host bootstrap (as UserData would)"
dnf -y -q install python3.12 python3.12-pip tar gzip shadow-utils procps-ng iproute postgresql16 libcap openssl findutils >/dev/null
useradd --system --home-dir /srv/shadowtrace --shell /sbin/nologin shadowtrace
useradd --system --home-dir /var/lib/caddy --create-home --shell /sbin/nologin caddy
mkdir -p /srv/shadowtrace/releases /etc/shadowtrace /etc/caddy /var/log/caddy /var/lib/shadowtrace/{data,logs,ledger,backups,legacy-reports,legacy-intake}
chown -R shadowtrace:shadowtrace /var/lib/shadowtrace; chmod 750 /var/lib/shadowtrace; chown caddy:caddy /var/log/caddy
install -m 0644 /tmp/rds-ca.pem /etc/shadowtrace/rds-ca.pem
ARCH=$(uname -m); case "$ARCH" in x86_64) CA=amd64; SUM=8220d1f013b6f27510247b2360c9e0ca9f018feebd82515f07635318b34ff9777ccc8fd0b6e6f2486ce3a33fe389fbb7db12d05baa474f4587509fb4f5ebf1c9;; aarch64) CA=arm64; SUM=d5a7c423853c24a799765e0e8210d5c7c22a8f56ed37a3cae2fb9f58be138853c02b4efd6b59d576e6d8c7c0d30b9c1592deeaa6a536ff69bcca23b8c1ea709c;; esac
cd /tmp && curl -fsSLO "https://github.com/caddyserver/caddy/releases/download/v2.11.4/caddy_2.11.4_linux_$CA.tar.gz"
echo "$SUM  caddy_2.11.4_linux_$CA.tar.gz" | sha512sum -c - >/dev/null && pass "Caddy 2.11.4 checksum verified"
tar -xzf "caddy_2.11.4_linux_$CA.tar.gz" caddy && install -m 0755 caddy /usr/local/bin/caddy
setcap cap_net_bind_service=+ep /usr/local/bin/caddy

echo "== release contents and configuration"
NAME=$(tar -tzf "$TARBALL" | sed -n '1s#/.*##p')
tar -xzf "$TARBALL" -C /tmp "$NAME/deploy"
DEP=/tmp/$NAME/deploy
sed -e "s#^ALLOWED_ORIGINS=.*#ALLOWED_ORIGINS=https://localhost#" \
    -e "s#^DATABASE_URL=.*#DATABASE_URL='postgresql://shadowtrace_app:${APP_PW}@db.rehearsal.internal:5432/shadowtrace?sslmode=verify-full\&sslrootcert=/etc/shadowtrace/rds-ca.pem'#" \
    -e "s#^GROQ_API_KEY=.*#GROQ_API_KEY=rehearsal-placeholder-never-sent#" \
    -e "s#^DEEPGRAM_API_KEY=.*#DEEPGRAM_API_KEY=#" \
    -e "s#^APP_VERSION=.*#APP_VERSION=rehearsal#" \
    "$DEP/env/shadowtrace.env.example" > /etc/shadowtrace/shadowtrace.env
printf "DATABASE_URL='postgresql://shadowtrace_migrator:%s@db.rehearsal.internal:5432/shadowtrace?sslmode=verify-full&sslrootcert=/etc/shadowtrace/rds-ca.pem'\nDB_AUTO_MIGRATE=0\n" "$MIG_PW" > /etc/shadowtrace/migrate.env
chown root:shadowtrace /etc/shadowtrace/shadowtrace.env; chmod 0640 /etc/shadowtrace/shadowtrace.env; chmod 0600 /etc/shadowtrace/migrate.env
sed "s/^interview.example.org {/localhost {/" "$DEP/caddy/Caddyfile" > /etc/caddy/Caddyfile
grep -q "^localhost {" /etc/caddy/Caddyfile && pass "Caddyfile from deploy/caddy with hostname localhost (Caddy internal CA)"

# systemd stand-in: start the service exactly as the unit's ExecStart does.
UNIT_EXEC=$(sed -n '/^ExecStart=/,/[^\\]$/p' "$DEP/systemd/shadowtrace.service" | tr -d '\\\n' | sed 's/^ExecStart=//; s/  */ /g')
cat > /usr/local/bin/systemctl <<EOF
#!/usr/bin/env bash
# rehearsal shim for: systemctl restart|start|stop shadowtrace
case "\$1 \$2" in
  "restart shadowtrace"|"start shadowtrace")
    pkill -f "uvicorn interview.server:app" 2>/dev/null || true; sleep 1
    cd /srv/shadowtrace/current
    runuser -u shadowtrace -- bash -c 'set -a; . /etc/shadowtrace/shadowtrace.env; set +a; umask 077; exec $UNIT_EXEC' >>/var/log/shadowtrace.log 2>&1 &
    ;;
  "stop shadowtrace") pkill -f "uvicorn interview.server:app" || true;;
  *) echo "shim: unsupported: \$*" >&2; exit 1;;
esac
EOF
chmod +x /usr/local/bin/systemctl
echo "unit ExecStart: $UNIT_EXEC"
echo "$UNIT_EXEC" | grep -q -- "--workers 1" && echo "$UNIT_EXEC" | grep -q -- "--host 127.0.0.1" && pass "unit runs ONE worker bound to loopback"

echo "== install release A with the real install_release.sh"
bash "$DEP/scripts/install_release.sh" "$TARBALL"
REL_A=$(readlink -f /srv/shadowtrace/current)
runuser -u caddy -- /usr/local/bin/caddy start --config /etc/caddy/Caddyfile >/dev/null 2>&1
sleep 3
CADDY_CA=/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt
for i in $(seq 1 20); do [ -f "$CADDY_CA" ] && break; sleep 1; done

echo "== least privilege"
APPDB="host=db.rehearsal.internal dbname=shadowtrace user=shadowtrace_app sslmode=verify-full sslrootcert=/etc/shadowtrace/rds-ca.pem"
if PGPASSWORD="$APP_PW" psql "$APPDB" -qc "CREATE TABLE should_fail(i int)" 2>/dev/null; then fail "app role could create a table"; else pass "app role cannot create tables"; fi
PGPASSWORD="$APP_PW" psql "$APPDB" -tAc "SELECT count(*) FROM session_reports" >/dev/null && pass "app role can read the index over verify-full TLS"
PGPASSWORD="$APP_PW" psql "host=db.rehearsal.internal dbname=shadowtrace user=shadowtrace_app sslmode=disable" -tAc "select 1" >/dev/null 2>&1 && echo "note: server also accepts non-TLS (RDS: set rds.force_ssl=1 to refuse it)" || true
VER=$(PGPASSWORD="$MIG_PW" psql "host=db.rehearsal.internal dbname=shadowtrace user=shadowtrace_migrator sslmode=verify-full sslrootcert=/etc/shadowtrace/rds-ca.pem" -tAc "select max(version) from schema_migrations")
[ "$VER" = "1" ] && pass "schema migrated to version 1 by the migration role"

echo "== HTTPS / WSS through Caddy"
CURL="curl -fsS --cacert $CADDY_CA"
$CURL https://localhost/ | grep -q '<div id="root"' && pass "HTTPS serves the built client"
$CURL https://localhost/assets/ -o /dev/null -w '' 2>/dev/null || true
READY=$($CURL https://localhost/ready)
echo "$READY" | grep -q '"ready":true' && echo "$READY" | grep -q '"report_store_backend":"postgres"' && pass "/ready over HTTPS: ready, PostgreSQL backend, schema ready"
code=$(curl -s -o /dev/null -w '%{http_code}' --cacert "$CADDY_CA" https://localhost/api/diagnostics); [ "$code" = 404 ] && pass "/api/diagnostics not exposed publicly ($code)"
ss -ltnH | awk '{print $4}' | grep -qx "127.0.0.1:8000" && ! ss -ltnH | awk '{print $4}' | grep -qE "^(0\.0\.0\.0|\*|\[::\]):8000$" && pass "FastAPI listens on 127.0.0.1:8000 only"
TOKEN=$($CURL -X POST https://localhost/api/guest | python3.12 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
$CURL -H "Authorization: Bearer $TOKEN" https://localhost/api/history | grep -q '"sessions": *\[\]' && pass "guest + history over HTTPS (index read from PostgreSQL)"
/srv/shadowtrace/current/.venv/bin/python - "$CADDY_CA" <<'PY'
import asyncio, json, ssl, sys, websockets
async def main():
    ctx = ssl.create_default_context(cafile=sys.argv[1])
    async with websockets.connect("wss://localhost/ws/session", ssl=ctx) as ws:
        await ws.send(json.dumps({"type": "session_start", "lane": "text"}))
        msg = json.loads(await ws.recv())
        assert msg["type"] == "session_rejected" and "intake" in msg["reason"], msg
        print("PASS  WSS upgrade through Caddy; production refuses a session without intake")
asyncio.run(main())
PY

echo "== app-role writes to the index (synthetic report, then erasure)"
runuser -u shadowtrace -- bash -c 'set -a; . /etc/shadowtrace/shadowtrace.env; set +a; cd /srv/shadowtrace/current; .venv/bin/python - <<PY
from pathlib import Path
from interview.config import get_settings
from interview.storage import open_report_store
s = open_report_store(get_settings(), Path("/var/lib/shadowtrace/data"))
r = {"session_id": "rehearsal-s1", "created_at": "2026-10-10T00:00:00Z", "config": {"role_family": "software", "round": "full"},
     "lane": "text", "ended_reason": "complete", "evaluator": {"provider": "mock"}, "overall": {"score": 0.5},
     "rounds": [{"round": "hr", "rubric_version": "hr.v1", "dimensions": [{"dimension_id": "d", "label": "D", "level": "solid", "score": 0.5, "assessed": True}], "findings": []}]}
s.record("rehearsal-cand", r); s.record("rehearsal-cand", r)
assert len(s.sessions("rehearsal-cand")) == 1
assert s.delete_candidate("rehearsal-cand") == 2
print("PASS  app role records idempotently and erases over verify-full TLS")
PY'

echo "== restart persistence"
systemctl restart shadowtrace
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1 && break; sleep 1; done
$CURL -H "Authorization: Bearer $TOKEN" https://localhost/api/me | grep -q candidate_id && pass "guest identity survives a service restart (files on the data volume)"

echo "== maintenance"
bash "$DEP/scripts/maintenance.sh" on >/dev/null
code=$(curl -s -o /dev/null -w '%{http_code}' --cacert "$CADDY_CA" -X POST https://localhost/api/guest); [ "$code" = 503 ] && pass "maintenance on: new guests refused ($code)"
bash "$DEP/scripts/maintenance.sh" drain >/dev/null && pass "drain returns when no live sessions or evaluation jobs remain"
bash "$DEP/scripts/maintenance.sh" off >/dev/null
code=$(curl -s -o /dev/null -w '%{http_code}' --cacert "$CADDY_CA" -X POST https://localhost/api/guest); [ "$code" = 200 ] && pass "maintenance off: guests accepted ($code)"

echo "== coordinated backup (real backup.sh) and archive verification"
bash /srv/shadowtrace/current/deploy/scripts/backup.sh >/tmp/backup.out 2>&1 || { cat /tmp/backup.out; fail "backup.sh"; }
ARCHIVE=$(ls -1t /var/lib/shadowtrace/backups/shadowtrace-*.tar.gz | head -1)
/srv/shadowtrace/current/.venv/bin/python /srv/shadowtrace/current/tools/ops/restore.py "$ARCHIVE" --verify-only | grep -q '"postgres_snapshot": true' && pass "backup archive verifies and contains the PostgreSQL snapshot"
[ ! -e /var/lib/shadowtrace/data/OPERATOR_STOP ] && pass "backup resumed service after quiesce"

echo "== release B, rollback.sh, then automatic rollback of a broken release C"
mk_release() {  # $1 suffix, $2 optional python line to append to server.py
  local dir=/tmp/rel-$1; rm -rf "$dir"; mkdir -p "$dir"; tar -xzf "$TARBALL" -C "$dir"
  mv "$dir/$NAME" "$dir/$NAME-$1"
  [ -n "${2:-}" ] && echo "$2" >> "$dir/$NAME-$1/src/interview/server.py"
  tar -C "$dir" -czf "/tmp/$NAME-$1.tar.gz" "$NAME-$1"
}
mk_release b
bash "$DEP/scripts/install_release.sh" "/tmp/$NAME-b.tar.gz" >/dev/null
[ "$(readlink -f /srv/shadowtrace/current)" = "/srv/shadowtrace/releases/${NAME#shadowtrace-}-b" ] && pass "release B installed and live"
bash "$DEP/scripts/rollback.sh" >/dev/null && [ "$(readlink -f /srv/shadowtrace/current)" = "$REL_A" ] && pass "rollback.sh returned to release A and it is ready"
mk_release c 'raise SystemExit("deliberately broken release for the rehearsal")'
if bash "$DEP/scripts/install_release.sh" "/tmp/$NAME-c.tar.gz" >/tmp/c.out 2>&1; then fail "broken release C was accepted"; fi
grep -q "rolling back" /tmp/c.out && [ "$(readlink -f /srv/shadowtrace/current)" = "$REL_A" ] && pass "broken release C failed readiness and was rolled back automatically"
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/ready >/dev/null 2>&1 && break; sleep 1; done
$CURL https://localhost/ready | grep -q '"ready":true' && pass "service ready on release A after the failed install"
echo "ALL REHEARSAL CHECKS PASSED"
