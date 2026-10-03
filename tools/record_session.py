#!/usr/bin/env python3
"""
tools/record_session.py — run a live interview session and write the JSONL log.

Usage:
    python tools/record_session.py [--host HOST] [--port PORT] [--log-dir DIR]

This starts the FastAPI server and opens a browser to the minimal client.
Press Ctrl+C to stop. The session log is written to LOG_DIR/<session_id>.jsonl.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a live interview session.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--log-dir", default="logs", type=Path)
    args = parser.parse_args()

    args.log_dir.mkdir(parents=True, exist_ok=True)

    url = f"http://{args.host}:{args.port}"
    print(f"Starting server at {url}")
    print(f"Logs will be written to: {args.log_dir.resolve()}")
    print("Press Ctrl+C to stop.\n")

    # Open browser after short delay
    import threading, time
    def _open():
        time.sleep(1.5)
        webbrowser.open(url)
    threading.Thread(target=_open, daemon=True).start()

    proc = subprocess.run(
        [
            sys.executable, "-m", "uvicorn",
            "interview.transport.server:app",
            "--host", args.host,
            "--port", str(args.port),
            "--reload",
        ],
        cwd=str(ROOT),
        env={
            **__import__("os").environ,
            "SESSION_LOG_DIR": str(args.log_dir.resolve()),
            "PYTHONPATH": str(ROOT / "src"),
        },
    )
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
