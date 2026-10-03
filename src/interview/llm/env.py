"""Load `.env` from the repo root without committing secrets."""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_LOADED = False


def load_dotenv(path: Path | None = None) -> None:
    """
    Minimal .env loader (KEY=VALUE lines). Does not override existing env vars.
    Prefer python-dotenv when installed; fall back to a tiny parser otherwise.
    """
    global _LOADED
    if _LOADED:
        return
    env_path = path or (_ROOT / ".env")
    if not env_path.exists():
        _LOADED = True
        return
    try:
        from dotenv import load_dotenv as _load  # type: ignore

        _load(env_path, override=False)
    except ImportError:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            os.environ.setdefault(key, val)
    _LOADED = True


def groq_api_key() -> str:
    load_dotenv()
    return os.environ.get("GROQ_API_KEY", "").strip()
