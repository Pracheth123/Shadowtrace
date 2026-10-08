"""
Readiness diagnostics that never print a secret.

Two different questions, answered separately:

  - **configured** — a credential value is present in the settings. Says
    nothing about whether it works.
  - **verified** — an authenticated request to the provider succeeded just now
    (Groq: `GET /models`; Deepgram: `GET /v1/projects`). Neither request
    generates text or audio, so neither is billed as usage. The result is
    cached for `CACHE_S` so this endpoint cannot be used to hammer a provider.

For Groq the check also reports whether each configured model id is in the
list this key can reach — the failure mode that previously only surfaced as a
404 in the middle of an interview.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any

CACHE_S = 600.0
_TIMEOUT_S = 8.0
_cache: dict[str, tuple[float, dict]] = {}


def _get(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    # An explicit User-Agent: Groq's edge answers 403 to urllib's default
    # "Python-urllib/x.y", which made a working key look rejected.
    headers = {"User-Agent": "shadowtrace-diagnostics/1.0", "Accept": "application/json", **headers}
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310 — fixed https URLs from settings
            body = response.read(2_000_000)
            try:
                return response.status, json.loads(body.decode("utf-8"))
            except ValueError:
                return response.status, None
    except urllib.error.HTTPError as exc:
        return exc.code, None


def _verify_groq(settings) -> dict:
    if not settings.has_groq:
        return {"configured": False, "verified": False, "detail": "GROQ_API_KEY is not set."}
    url = settings.groq_base_url.rstrip("/") + "/models"
    try:
        status, body = _get(
            url, {"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"}
        )
    except Exception as exc:  # noqa: BLE001 — network failure is a result, not a crash
        return {"configured": True, "verified": False, "detail": f"Request failed: {exc.__class__.__name__}"}
    if status != 200:
        detail = {401: "The key was rejected (401).", 403: "The key is not allowed (403)."}.get(
            status, f"Provider answered HTTP {status}."
        )
        return {"configured": True, "verified": False, "status": status, "detail": detail}
    available = {item.get("id") for item in (body or {}).get("data", []) if isinstance(item, dict)}
    wanted = {
        "live_interviewer": settings.model_live_interviewer,
        "evaluator": settings.model_evaluator,
        "fallback_fast": settings.model_fallback_fast,
        "fallback_quality": settings.model_fallback_quality,
    }
    models = {role: {"id": mid, "available": mid in available} for role, mid in wanted.items()}
    missing = [m["id"] for m in models.values() if not m["available"]]
    return {
        "configured": True,
        "verified": True,
        "status": status,
        "models": models,
        "detail": (
            "Authenticated request succeeded; all configured models are available."
            if not missing
            else "Authenticated, but these configured models are not available to this key: "
            + ", ".join(sorted(set(missing)))
        ),
    }


def _verify_deepgram(settings) -> dict:
    if not settings.has_deepgram:
        return {"configured": False, "verified": False, "detail": "DEEPGRAM_API_KEY is not set."}
    try:
        status, _ = _get(
            "https://api.deepgram.com/v1/projects",
            {"Authorization": f"Token {settings.deepgram_api_key.get_secret_value()}"},
        )
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "verified": False, "detail": f"Request failed: {exc.__class__.__name__}"}
    if status != 200:
        return {"configured": True, "verified": False, "status": status, "detail": f"Provider answered HTTP {status}."}
    return {"configured": True, "verified": True, "status": status, "detail": "Authenticated request succeeded."}


async def verify(settings, *, force: bool = False) -> dict:
    """Authenticated checks for both providers, cached."""
    now = time.time()
    hit = _cache.get("all")
    if hit and not force and now - hit[0] < CACHE_S:
        return {**hit[1], "cached": True}
    groq, deepgram = await asyncio.gather(
        asyncio.to_thread(_verify_groq, settings), asyncio.to_thread(_verify_deepgram, settings)
    )
    result = {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "groq": groq,
        "deepgram": deepgram,
        "cached": False,
    }
    _cache["all"] = (now, result)
    return result


def last_verification() -> dict | None:
    hit = _cache.get("all")
    return {**hit[1], "cached": True} if hit else None


def readiness(settings) -> dict:
    """Configured-only view plus the last authenticated check, if any."""
    last = last_verification()
    return {
        "settings": settings.public_dict(),
        "groq": {
            "configured": settings.has_groq,
            "verified": (last or {}).get("groq", {}).get("verified"),
        },
        "deepgram": {
            "configured": settings.has_deepgram,
            "verified": (last or {}).get("deepgram", {}).get("verified"),
        },
        "last_verification": last,
        "note": (
            "'configured' means a credential value is present. 'verified' is only "
            "true after an authenticated provider request succeeded (POST "
            "/api/diagnostics/verify); null means not checked yet."
        ),
    }


__all__ = ["CACHE_S", "last_verification", "readiness", "verify"]
