"""Offline, repeatable performance benchmark for the EAS hot paths."""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from database import Database  # noqa: E402
from llm_client import DeterministicProvider, LLMClient, Message  # noqa: E402
from tools import super_search  # noqa: E402

N = 20


def stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50_ms": round(statistics.median(ordered) * 1000, 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)] * 1000, 3),
        "mean_ms": round(statistics.mean(ordered) * 1000, 3),
    }


async def timed_llm(provider: DeterministicProvider) -> list[float]:
    client = LLMClient()
    client.register_provider(provider)
    values = []
    for _ in range(N):
        started = time.perf_counter()
        await client.chat("deterministic", [Message(role="user", content="ping")], "bench")
        values.append(time.perf_counter() - started)
    return values


async def compression_mode(enabled: bool, llm: LLMClient) -> list[float]:
    # Measures the exact current compression gate: disabled is the optimized default;
    # enabled is the comparable legacy-style path using a deterministic provider.
    from api import server

    server.state.context_compression = {
        "enabled": enabled,
        "provider": "deterministic",
        "model": "bench",
        "min_input_chars": 0,
        "timeout_seconds": 2,
    }
    server.state.llm_client = llm
    values = []
    result = {"success": True, "check_status": "verified", "output": {"value": "x"}}
    for _ in range(N):
        started = time.perf_counter()
        await server._compress_step_context("plan", str(uuid.uuid4()), result)
        values.append(time.perf_counter() - started)
    return values


def db_benchmark() -> list[float]:
    values = []
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "bench.db"))
        for i in range(N):
            started = time.perf_counter()
            db.log("info", f"benchmark-{i}", {"i": i}, "benchmark")
            values.append(time.perf_counter() - started)
    return values


def search_benchmark() -> tuple[list[float], list[float]]:
    names = ["fake_a", "fake_b", "fake_c", "fake_d"]

    def fake_engine(query, limit, timeout):
        time.sleep(0.025)
        return [{"title": query, "url": f"https://example.com/{query}", "snippet": "ok"}]

    original = dict(super_search._ENGINE_FUNCS)
    super_search._ENGINE_FUNCS.update({name: fake_engine for name in names})
    try:
        concurrent = []
        serial = []
        for _ in range(N):
            started = time.perf_counter()
            super_search.SuperSearch.search("bench", engines=names, timeout=2)
            concurrent.append(time.perf_counter() - started)

            started = time.perf_counter()
            for name in names:
                fake_engine("bench", 10, 2)
            serial.append(time.perf_counter() - started)
        return concurrent, serial
    finally:
        super_search._ENGINE_FUNCS.clear()
        super_search._ENGINE_FUNCS.update(original)


async def main() -> None:
    provider = DeterministicProvider("fixed")
    llm = LLMClient()
    llm.register_provider(provider)
    disabled = await compression_mode(False, llm)
    enabled = await compression_mode(True, llm)
    llm_values = await timed_llm(provider)
    db_values = db_benchmark()
    search_concurrent, search_serial = search_benchmark()
    output = {
        "benchmark": {"iterations": N, "python": sys.version.split()[0], "note": "offline deterministic; milliseconds"},
        "step_context_compression": {"optimized_default_disabled": stats(disabled), "comparable_enabled_path": stats(enabled)},
        "llm_deterministic_request": stats(llm_values),
        "sqlite_log_write": stats(db_values),
        "search_four_engines": {"optimized_concurrent": stats(search_concurrent), "serial_baseline": stats(search_serial)},
        "speedup": {
            "compression_gate_p50": round(statistics.median(enabled) / max(statistics.median(disabled), 1e-9), 2),
            "search_p50": round(statistics.median(search_serial) / max(statistics.median(search_concurrent), 1e-9), 2),
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
