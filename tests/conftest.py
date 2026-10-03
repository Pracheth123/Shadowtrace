"""Pytest hooks — live Groq tests are skipped unless explicitly selected."""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "live: real Groq API calls (skipped by default; run with -m live)"
    )
