"""
tools/bench_ttft.py — Time-to-first-token and time-to-first-sentence benchmark.

Feeds 20 realistic candidate transcripts to each configured LLM model, 5 runs each.
Reports p50/p95 TTFT and time-to-first-sentence per model.
Context includes a Claims File-sized blob (~8 claims) plus 6 prior turns to simulate
real session pressure on the prompt.

Usage:
    python tools/bench_ttft.py [--models gpt-4o-mini gpt-4o claude-3-haiku-20240307]
    python tools/bench_ttft.py --mock   # run with fake_llm, no API keys needed

Output: a markdown table + writes results to docs/decisions/inference.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ── Realistic claims blob (8 claims, as would come from stage 6 indexer) ────
_CLAIMS_BLOCK = """\
<<CLAIMS>>
1. Built a Kafka pipeline handling 2M events/second at a fintech startup.
2. Led a team of 6 engineers through a major production outage in 2023.
3. Designed a circuit breaker + exponential backoff fault-tolerance layer.
4. Reduced P99 API latency by 40% via connection pooling and read replicas.
5. Migrated a monolith to microservices over 18 months with zero downtime.
6. Wrote the incident post-mortem adopted as a template across the org.
7. Achieved 99.95% uptime SLA measured via Prometheus + Grafana dashboards.
8. Mentored 3 junior engineers who were promoted within 12 months.
<</CLAIMS>>"""

# ── 6 prior turns to create realistic context pressure ───────────────────────
_PRIOR_TURNS = [
    {"role": "assistant", "content": "Tell me about a technically challenging project you've led."},
    {"role": "user",      "content": "I built a distributed Kafka pipeline at my last company that processed about two million events per second for a payments platform."},
    {"role": "assistant", "content": "What was the hardest engineering problem you had to solve in that pipeline?"},
    {"role": "user",      "content": "Back-pressure. When a downstream service slowed down, the whole pipeline would back up. We solved it with consumer group lag metrics and throttling the producers dynamically."},
    {"role": "assistant", "content": "How did you validate that the throttling thresholds were correct?"},
    {"role": "user",      "content": "We load-tested with synthetic traffic and measured queue depth versus throughput. It took about three iterations to tune it properly."},
]

# ── 20 realistic candidate answers (fixtures/transcripts/bench_*.json) ───────
_FIXTURE_DIR = ROOT / "fixtures" / "transcripts"


def _load_transcripts() -> list[str]:
    paths = sorted(_FIXTURE_DIR.glob("bench_*.json"))
    if len(paths) < 20:
        raise FileNotFoundError(
            f"Expected 20 bench_*.json fixtures in {_FIXTURE_DIR}, found {len(paths)}"
        )
    texts: list[str] = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        text = (data.get("text") or "").strip()
        if not text:
            raise ValueError(f"Empty text in {path.name}")
        texts.append(text)
    return texts


_TRANSCRIPTS = _load_transcripts()


async def _benchmark_mock(
    transcripts: list[str],
    runs: int,
    delay_ms: int,
    tokens_per_second: float,
) -> dict:
    """Run with FakeLlm — no API key needed."""
    from interview.mocks.fake_llm import FakeLlm
    import re

    sentence_end = re.compile(r"(?<=[.!?])\s")
    ttfts: list[float] = []
    ttfs_list: list[float] = []

    for transcript in transcripts:
        history = _PRIOR_TURNS + [{"role": "user", "content": _CLAIMS_BLOCK + "\n\n" + transcript}]
        for _ in range(runs):
            llm = FakeLlm(delay_ms=delay_ms, tokens_per_second=tokens_per_second)
            t0 = time.monotonic()
            first_token_t = None
            first_sentence_t = None
            buf = ""
            async for token in llm.stream(history):
                if first_token_t is None:
                    first_token_t = time.monotonic()
                buf += token
                if first_sentence_t is None and sentence_end.search(buf):
                    first_sentence_t = time.monotonic()

            if first_token_t:
                ttfts.append((first_token_t - t0) * 1000)
            if first_sentence_t:
                ttfs_list.append((first_sentence_t - t0) * 1000)

    return {"ttft": ttfts, "ttfs": ttfs_list}


def _percentile(data: list[float], p: int) -> float:
    if not data:
        return float("nan")
    s = sorted(data)
    idx = max(0, int(len(s) * p / 100) - 1)
    return round(s[idx], 1)


def _print_table(results: dict[str, dict]) -> None:
    print("\n| Model | TTFT p50 | TTFT p95 | First-sentence p50 | First-sentence p95 |")
    print("|---|---|---|---|---|")
    for model, r in results.items():
        print(
            f"| {model} "
            f"| {_percentile(r['ttft'], 50):.0f} ms "
            f"| {_percentile(r['ttft'], 95):.0f} ms "
            f"| {_percentile(r['ttfs'], 50):.0f} ms "
            f"| {_percentile(r['ttfs'], 95):.0f} ms |"
        )


async def main_async(args: argparse.Namespace) -> None:
    results: dict[str, dict] = {}

    if args.mock:
        configs = [
            ("mock-fast (50ms delay, 60 t/s)",    50,  60.0),
            ("mock-medium (150ms delay, 30 t/s)", 150, 30.0),
            ("mock-slow (300ms delay, 15 t/s)",   300, 15.0),
        ]
        for name, delay, rate in configs:
            print(f"Benchmarking {name}...")
            results[name] = await _benchmark_mock(
                _TRANSCRIPTS, args.runs, delay, rate
            )
    else:
        try:
            from openai import AsyncOpenAI  # type: ignore
        except ImportError:
            print("openai not installed. Use --mock for offline benchmarking.")
            sys.exit(1)

        import re

        from interview.llm.client import GROQ_BASE_URL
        from interview.llm.env import groq_api_key, load_dotenv

        load_dotenv()
        sentence_end = re.compile(r"(?<=[.!?])\s")
        api_key = groq_api_key()
        if not api_key:
            print("GROQ_API_KEY missing. Use --mock or set it in .env.")
            sys.exit(1)
        # All live model benches go to Groq (OpenAI-compatible base_url).
        client = AsyncOpenAI(api_key=api_key, base_url=GROQ_BASE_URL)

        for model in args.models:
            print(f"Benchmarking {model}...")
            ttfts: list[float] = []
            ttfs_list: list[float] = []

            for transcript in _TRANSCRIPTS:
                history = (
                    _PRIOR_TURNS
                    + [{"role": "user", "content": _CLAIMS_BLOCK + "\n\n" + transcript}]
                )
                messages = [{"role": "system", "content": "You are a professional technical interviewer. Ask one focused follow-up question. Be concise."}] + history

                for _ in range(args.runs):
                    t0 = time.monotonic()
                    first_token_t = None
                    first_sentence_t = None
                    buf = ""
                    stream = await client.chat.completions.create(
                        model=model, messages=messages, max_tokens=120, stream=True
                    )
                    async for chunk in stream:
                        delta = chunk.choices[0].delta.content or ""
                        if delta:
                            if first_token_t is None:
                                first_token_t = time.monotonic()
                            buf += delta
                            if first_sentence_t is None and sentence_end.search(buf):
                                first_sentence_t = time.monotonic()

                    if first_token_t:
                        ttfts.append((first_token_t - t0) * 1000)
                    if first_sentence_t:
                        ttfs_list.append((first_sentence_t - t0) * 1000)

            results[model] = {"ttft": ttfts, "ttfs": ttfs_list}

    _print_table(results)

    # Replace the measured TTFT section in docs/decisions/inference.md
    out = ROOT / "docs" / "decisions" / "inference.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    section = "## Measured TTFT (from tools/bench_ttft.py)\n\n"
    section += "| Model | TTFT p50 | TTFT p95 | First-sentence p50 | First-sentence p95 |\n"
    section += "|---|---|---|---|---|\n"
    for model, r in results.items():
        section += (
            f"| {model} "
            f"| {_percentile(r['ttft'], 50):.0f} ms "
            f"| {_percentile(r['ttft'], 95):.0f} ms "
            f"| {_percentile(r['ttfs'], 50):.0f} ms "
            f"| {_percentile(r['ttfs'], 95):.0f} ms |\n"
        )
    _upsert_section(out, "## Measured TTFT (from tools/bench_ttft.py)", section)
    print(f"\nResults written to {out}")


def _upsert_section(path: Path, heading: str, section: str) -> None:
    """Replace an existing ## section or append it. Avoids duplicate appends."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    import re

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
    parser = argparse.ArgumentParser(description="Benchmark LLM TTFT across models.")
    parser.add_argument("--mock", action="store_true", help="Use FakeLlm (no API key needed)")
    parser.add_argument(
        "--groq",
        action="store_true",
        help="Benchmark Groq models via OpenAI SDK (GROQ_API_KEY)",
    )
    parser.add_argument("--runs", type=int, default=5, help="Runs per transcript per model")
    parser.add_argument(
        "--models",
        nargs="+",
        default=["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
        help="Model names to benchmark (Groq IDs when --groq)",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
