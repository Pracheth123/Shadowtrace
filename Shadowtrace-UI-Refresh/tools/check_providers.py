"""
Authenticated, non-generating provider checks. Prints no secret.

    python tools/check_providers.py

Groq: GET /models with the configured key, and whether every configured model
id is reachable by that key. Deepgram: GET /v1/projects. Neither generates text
or audio. Exit code 0 only when both providers verified and every configured
Groq model is available.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from interview.config import get_settings  # noqa: E402
from interview.services.diagnostics import verify  # noqa: E402


def main() -> int:
    settings = get_settings()
    result = asyncio.run(verify(settings, force=True))
    print(json.dumps(result, indent=2))
    groq = result["groq"]
    models_ok = all(m["available"] for m in (groq.get("models") or {}).values())
    return 0 if groq.get("verified") and models_ok and result["deepgram"].get("verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
