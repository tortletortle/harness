"""Correlation ID（P2 可观测性）回归测试。

覆盖三层：
1. correlation.py 的 contextvar 语义（默认值 / 绑定 / 协程隔离）
2. logging_config 的 loguru patcher（日志自动带上上下文值，bind 显式值优先）
3. api/server.py 的 HTTP middleware（生成 / 透传 / 响应头回传）
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from correlation import get_correlation_id, new_correlation_id, set_correlation_id


class TestContextVar:
    def test_default_is_dash(self):
        assert get_correlation_id() == "-"

    def test_set_and_get(self):
        set_correlation_id("abc123")
        assert get_correlation_id() == "abc123"
        set_correlation_id("-")  # 还原，避免污染其它用例

    def test_new_id_shape(self):
        cid = new_correlation_id()
        assert len(cid) == 12
        assert all(c in "0123456789abcdef" for c in cid)
        assert new_correlation_id() != cid

    def test_isolated_between_tasks(self):
        """两个并发 asyncio 任务各自绑定，互不串扰。"""

        async def worker(tag, results):
            set_correlation_id(tag)
            await asyncio.sleep(0.01)
            results[tag] = get_correlation_id()

        async def main():
            results = {}
            await asyncio.gather(worker("task-a", results), worker("task-b", results))
            return results

        results = asyncio.run(main())
        assert results == {"task-a": "task-a", "task-b": "task-b"}


class TestLoguruPatcher:
    def test_log_carries_context_id(self):
        from logging_config import get_logger

        logger = get_logger(__name__)
        captured = []
        sink_id = logger.add(
            lambda msg: captured.append(msg.record["extra"]["trace_id"]),
            level="INFO",
        )
        try:
            set_correlation_id("ctx-42")
            logger.info("hello")
            set_correlation_id("-")
            logger.info("after-reset")
        finally:
            logger.remove(sink_id)
        assert captured[0] == "ctx-42"
        assert captured[1] == "-"

    def test_explicit_bind_wins(self):
        from logging_config import get_logger

        logger = get_logger(__name__)
        captured = []
        sink_id = logger.add(
            lambda msg: captured.append(msg.record["extra"]["trace_id"]),
            level="INFO",
        )
        try:
            set_correlation_id("ctx-value")
            logger.bind(trace_id="explicit-value").info("bound")
        finally:
            logger.remove(sink_id)
            set_correlation_id("-")
        assert captured[0] == "explicit-value"


class TestHttpMiddleware:
    def _client(self):
        from fastapi.testclient import TestClient
        import api.server as server

        return TestClient(server.app), server

    def test_generates_id_when_absent(self):
        client, server = self._client()
        resp = client.get("/health")
        cid = resp.headers.get("X-Correlation-ID")
        assert cid and len(cid) == 12

    def test_passthrough_when_present(self):
        client, server = self._client()
        resp = client.get("/health", headers={"X-Correlation-ID": "my-trace-001"})
        assert resp.headers.get("X-Correlation-ID") == "my-trace-001"

    def test_rejects_unprintable_header(self):
        client, server = self._client()
        resp = client.get("/health", headers={"X-Correlation-ID": "bad\x01value"})
        cid = resp.headers.get("X-Correlation-ID")
        assert cid != "bad\x01value"
        assert cid and len(cid) == 12

