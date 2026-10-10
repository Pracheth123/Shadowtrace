#!/usr/bin/env python3
"""
Measure the API process's memory while it runs real work (instance sizing).

    python tools/ops/measure_memory.py --sessions 3

Starts `uvicorn interview.server:app` (one worker, offline mocks, temp data
dir), drives intake → full typed interview → evaluation over real HTTP and
WebSocket for N sessions in parallel, and samples the server's resident memory
every 0.25 s. Prints idle and peak RSS. Offline mocks make no model calls, so
this measures the app itself; a live session adds provider-client buffers, and
voice adds PCM buffers (roughly 32 kB/s per active stream at 16 kHz mono).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESUME = (
    "I built a Python reconciliation service for payment settlements. I designed the PostgreSQL "
    "schema, wrote retry logic and worked with the support team on the launch."
)
ANSWER = ("I owned the reconciliation service. I chose PostgreSQL for transactions, added idempotency "
          "keys to prevent duplicate settlements, and checked the launch with support.")


def rss_bytes(pid: int) -> int:
    if sys.platform == "win32":
        # The venv python.exe is a launcher; the interpreter is its child. Sum the tree.
        script = (f"$ids=@({pid}); $kids=Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}' | "
                  "ForEach-Object {$_.ProcessId}; $ids+=$kids; "
                  "(Get-Process -Id $ids -ErrorAction SilentlyContinue | Measure-Object WorkingSet64 -Sum).Sum")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True).stdout.strip()
        return int(float(out or 0))
    with open(f"/proc/{pid}/status", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return 0


def post(url, data=None, headers=None, form=None):
    import urllib.parse

    body = urllib.parse.urlencode(form).encode() if form else (json.dumps(data).encode() if data is not None else b"")
    req = urllib.request.Request(url, data=body, method="POST", headers=headers or {})
    if form:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    return json.load(urllib.request.urlopen(req, timeout=30))


def get(url, headers=None):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=30))


async def one_session(base: str) -> None:
    import websockets

    guest = await asyncio.to_thread(post, f"{base}/api/guest")
    auth = {"Authorization": f"Bearer {guest['token']}"}
    intake = await asyncio.to_thread(post, f"{base}/api/intake", headers=auth, form={
        "target_role": "Backend engineer", "role_family": "software", "round": "full", "lane": "text",
        "background_text": RESUME, "consent": "1"})
    iid = intake["intake_id"]
    while (await asyncio.to_thread(get, f"{base}/api/intake/{iid}", auth))["status"]["state"] not in ("ready", "failed"):
        await asyncio.sleep(0.2)
    async with websockets.connect(base.replace("http", "ws") + "/ws/session", max_size=None) as ws:
        await ws.send(json.dumps({"type": "session_start", "auth_token": guest["token"], "intake_id": iid}))
        session_id = None
        answered = 0
        while True:
            raw = await ws.recv()
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            if msg["type"] == "session_ready":
                session_id = msg["session_id"]
            if msg["type"] == "agent_utterance_end" and answered < 30:
                answered += 1
                await ws.send(json.dumps({"type": "candidate_text", "text": ANSWER}))
            if msg["type"] == "session_complete":
                break
    while (await asyncio.to_thread(get, f"{base}/api/sessions/{session_id}", auth)).get("state") not in ("complete", "failed"):
        await asyncio.sleep(0.3)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sessions", type=int, default=3)
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    tmp = tempfile.mkdtemp(prefix="st-mem-")
    env = {**os.environ, "APP_ENV": "test", "ALLOW_MOCK_PROVIDERS": "1", "MOCK_LLM": "1", "GROQ_API_KEY": "",
           "DEEPGRAM_API_KEY": "", "DATA_DIR": f"{tmp}/data", "SESSION_LOG_DIR": f"{tmp}/logs",
           "GUEST_RETENTION_DAYS": "0", "PYTHONPATH": str(ROOT / "src")}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "interview.server:app", "--host", "127.0.0.1",
                             "--port", str(a.port), "--workers", "1"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{a.port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/health", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.25)
        time.sleep(1.0)
        idle = rss_bytes(proc.pid)
        samples: list[int] = []
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                samples.append(rss_bytes(proc.pid))
                time.sleep(0.25)

        t = threading.Thread(target=sample, daemon=True)
        t.start()
        started = time.time()

        async def run():
            await asyncio.gather(*(one_session(base) for _ in range(a.sessions)))

        asyncio.run(run())
        stop.set()
        t.join()
        result = {
            "platform": sys.platform, "python": sys.version.split()[0],
            "parallel_sessions": a.sessions, "seconds": round(time.time() - started, 1),
            "idle_rss_mb": round(idle / 2**20, 1), "peak_rss_mb": round(max(samples or [idle]) / 2**20, 1),
            "samples": len(samples), "note": "offline mocks: no model calls; live/voice add provider buffers",
        }
        print(json.dumps(result, indent=2))
    finally:
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
