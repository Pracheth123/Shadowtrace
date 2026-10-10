#!/usr/bin/env bash
# Pause and resume new work without a restart (the server's operator stop file).
#
#   maintenance.sh on      # new guests/intakes/sessions/practice/retries get 503
#   maintenance.sh drain   # on, then wait for live sessions and evaluation jobs to finish
#   maintenance.sh status
#   maintenance.sh off
set -euo pipefail
DATA_DIR=${DATA_DIR:-/var/lib/shadowtrace/data}
STOP="$DATA_DIR/OPERATOR_STOP"
URL=${URL:-http://127.0.0.1:8000}
case "${1:-status}" in
  on)    touch "$STOP"; echo "maintenance ON ($STOP)";;
  off)   rm -f "$STOP"; echo "maintenance OFF";;
  status)
    [ -e "$STOP" ] && echo "maintenance ON" || echo "maintenance OFF"
    curl -fsS "$URL/api/diagnostics" | python3 -c 'import json,sys;r=json.load(sys.stdin)["runtime"];print({k:r[k] for k in ("live_sessions","evaluation_jobs_active")})' || true;;
  drain)
    touch "$STOP"; echo "maintenance ON; draining (max ${DRAIN_MAX_S:-1500}s)"
    end=$(( $(date +%s) + ${DRAIN_MAX_S:-1500} ))
    while [ "$(date +%s)" -lt "$end" ]; do
      busy=$(curl -fsS "$URL/api/diagnostics" | python3 -c 'import json,sys;r=json.load(sys.stdin)["runtime"];print(r["live_sessions"]+r["evaluation_jobs_active"])')
      [ "$busy" = 0 ] && { echo "drained"; exit 0; }
      echo "waiting: $busy live session(s)/job(s)"; sleep 10
    done
    echo "still busy after ${DRAIN_MAX_S:-1500}s (MAX_SESSION_SECONDS bounds a session; check before continuing)" >&2; exit 1;;
  *) echo "usage: $0 on|off|status|drain" >&2; exit 2;;
esac
