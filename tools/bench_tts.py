"""
tools/bench_tts.py — First-chunk latency benchmark per TTS vendor.

Uses the same 20 sentences as bench_ttft.py (first sentences from FakeLlm responses).
Reports p50/p95 first-chunk latency per vendor.

Usage:
    python tools/bench_tts.py --mock          # FakeTts, no API key
    python tools/bench_tts.py --vendors openai-tts elevenlabs  # real vendors

Output: table printed + appended to docs/decisions/inference.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

_TEST_SENTENCES = [
    "That's a solid foundation. How did you handle back-pressure?",
    "Interesting. Can you walk me through how you measured reliability in production?",
    "Got it. What was the hardest technical decision you made on that project?",
    "Thanks for sharing that. How did you communicate the rollback decision?",
    "Understood. When you say the team disagreed, what was the point of contention?",
    "Fair enough. If you were designing that system today, what would you change first?",
    "Good. How did you validate that the circuit breaker thresholds were correct?",
    "Noted. Can you describe how you detected a consumer falling behind?",
    "Solid. What observability tooling did you put in place, and why that stack?",
    "Okay. How long did the migration take and how did you manage risk during it?",
    "That's helpful context. How did you align the three PMs on priority?",
    "I see. What specifically made Kafka a better fit than RabbitMQ there?",
    "Interesting. How often did you actually need to use the disaster recovery process?",
    "Right. What does your ideal incident post-mortem process look like?",
    "Good. How did you reduce the build time so significantly?",
    "Noted. What tradeoffs did you accept with consistent hashing?",
    "Understood. How did you approach onboarding the junior engineers?",
    "Got it. How did schema evolution work across your producer and consumer deploys?",
    "That makes sense. How did you tune your canary deployment thresholds?",
    "Okay. What would you do differently if you had to lead that migration again?",
]


async def _bench_mock(sentences: list[str], runs: int) -> list[float]:
    """Benchmark FakeTts — no API, no audio hardware."""
    from interview.events.bus import EventBus
    from interview.mocks.fake_tts import FakeTts

    latencies: list[float] = []
    for sentence in sentences:
        for _ in range(runs):
            bus = EventBus()
            chunks: list[float] = []

            async def collect(event):
                if event.type == "tts_chunk":
                    chunks.append(event.t_emit or 0.0)

            bus.subscribe_all(collect)
            tts = FakeTts(bus, "bench", "turn-x", "utt-x")
            t0 = time.monotonic()
            await tts.synthesise(sentence)
            await bus.drain()
            if chunks:
                latencies.append((time.monotonic() - t0) * 1000)

    return latencies


async def _bench_openai_tts(sentences: list[str], runs: int, api_key: str) -> list[float]:
    from openai import AsyncOpenAI  # type: ignore
    client = AsyncOpenAI(api_key=api_key)
    latencies: list[float] = []
    for sentence in sentences:
        for _ in range(runs):
            t0 = time.monotonic()
            async with client.audio.speech.with_streaming_response.create(
                model="tts-1", voice="nova", input=sentence, response_format="pcm"
            ) as resp:
                first = True
                async for _ in resp.iter_bytes(chunk_size=4096):
                    if first:
                        latencies.append((time.monotonic() - t0) * 1000)
                        first = False
                    break  # only need first chunk timing
    return latencies


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return float("nan")
    s = sorted(data)
    idx = max(0, int(len(s) * p / 100) - 1)
    return round(s[idx], 1)


def _print_table(results: dict[str, list[float]]) -> None:
    print("\n| Vendor | First-chunk p50 | First-chunk p95 |")
    print("|---|---|---|")
    for vendor, lats in results.items():
        print(f"| {vendor} | {_percentile(lats, 50):.0f} ms | {_percentile(lats, 95):.0f} ms |")


async def main_async(args: argparse.Namespace) -> None:
    results: dict[str, list[float]] = {}

    if args.mock:
        print("Benchmarking mock TTS...")
        results["mock (FakeTts)"] = await _bench_mock(_TEST_SENTENCES, args.runs)
    else:
        import os
        api_key = os.environ.get("OPENAI_API_KEY", "")
        for vendor in args.vendors:
            print(f"Benchmarking {vendor}...")
            if vendor == "openai-tts":
                results[vendor] = await _bench_openai_tts(_TEST_SENTENCES, args.runs, api_key)
            else:
                print(f"  Vendor '{vendor}' not yet wired in bench_tts.py; skipping.")

    _print_table(results)

    out = ROOT / "docs" / "decisions" / "inference.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    section = "## Measured TTS first-chunk (from tools/bench_tts.py)\n\n"
    section += "| Vendor | First-chunk p50 | First-chunk p95 |\n"
    section += "|---|---|---|\n"
    for vendor, lats in results.items():
        section += (
            f"| {vendor} "
            f"| {_percentile(lats, 50):.0f} ms "
            f"| {_percentile(lats, 95):.0f} ms |\n"
        )
    _upsert_section(out, "## Measured TTS first-chunk (from tools/bench_tts.py)", section)
    print(f"\nResults written to {out}")


def _upsert_section(path: Path, heading: str, section: str) -> None:
    """Replace an existing ## section or append it. Avoids duplicate appends."""
    import re

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(
        rf"{re.escape(heading)}.*?(?=\n## |\Z)",
        re.DOTALL,
    )
    if pattern.search(text):
        text = pattern.sub(section.rstrip() + "\n\n", text)
    else:
        text = text.rstrip() + "\n\n" + section
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark TTS first-chunk latency.")
    parser.add_argument("--mock", action="store_true", help="Use FakeTts (no API key needed)")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--vendors", nargs="+", default=["openai-tts", "elevenlabs"])
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
