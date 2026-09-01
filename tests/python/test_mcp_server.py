# -*- coding: utf-8 -*-
"""MCP server 暴露测试：用自己的 MCPStdioClient 反向连自己的 mcp_server.py。

覆盖：
1. initialize/tools/list 全握手，工具清单 = 默认白名单（排除 external/manual）；
2. tools/call project_memory 真实写盘成功；
3. 未在白名单的 external 工具（http_api）被拒；
4. 未知工具报 isError。
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parent.parent.parent / "python"
sys.path.insert(0, str(PYTHON_DIR))

from mcp_client import MCPStdioClient  # noqa: E402


@pytest.fixture()
def server_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def _client(tmp_path: Path) -> MCPStdioClient:
    return MCPStdioClient(
        "self",
        sys.executable,
        ["-u", str(PYTHON_DIR / "mcp_server.py")],
        env={"EAS_WORKSPACE_ROOT": str(tmp_path), "PYTHONIOENCODING": "utf-8"},
        request_timeout=30,
    )


def _result_text(result: dict) -> str:
    return "".join(
        c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
    )


@pytest.mark.asyncio
async def test_mcp_server_handshake_and_tools(server_env):
    client = _client(server_env)
    await client.start()
    try:
        assert client.alive
        assert client.server_info.get("name") == "eas-harness"
        names = {t["name"] for t in client.tools}
        # 确定性工具在白名单里
        assert {"project_memory", "file_creator", "text_editor"} <= names
        # external 副作用工具默认不暴露
        assert "http_api" not in names
        assert "github_operator" not in names
        assert "manual_tool" not in names
        # 每个工具都有合法 inputSchema
        for tool in client.tools:
            assert tool["inputSchema"].get("type") == "object"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_mcp_server_call_writes_memory(server_env):
    client = _client(server_env)
    await client.start()
    try:
        result = await client.call_tool(
            "project_memory",
            {"title": "冒烟测试", "content": "mcp-server 暴露成功"},
        )
        assert result.get("isError") is False
        payload = json.loads(_result_text(result))
        assert payload["success"] is True
        mem_file = server_env / "PROJECT_MEMORY.md"
        assert mem_file.exists()
        assert "mcp-server 暴露成功" in mem_file.read_text(encoding="utf-8")
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_mcp_server_rejects_unlisted_tool(server_env):
    client = _client(server_env)
    await client.start()
    try:
        result = await client.call_tool("http_api", {"url": "https://example.com"})
        assert result.get("isError") is True
        assert "不可用或未授权" in _result_text(result)

        result = await client.call_tool("no_such_tool", {})
        assert result.get("isError") is True
    finally:
        await client.stop()
