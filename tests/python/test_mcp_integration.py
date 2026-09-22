# -*- coding: utf-8 -*-
"""MCP 生态集成测试：stdio 客户端全链路 + Capability 包装 + Grant 边界。"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parent.parent.parent / "python"
sys.path.insert(0, str(PYTHON_DIR))

from mcp_client import MCPStdioClient, load_mcp_server_configs  # noqa: E402
from mcp_adapter import MCPToolAdapter  # noqa: E402
from capabilities import CapabilityRequest, CapabilityRegistry  # noqa: E402
from authorization import AuthorizationManager, GrantLimits  # noqa: E402

ECHO_SERVER = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"


@pytest.fixture()
def echo_config():
    return {
        "echo": {
            "command": sys.executable,
            "args": [str(ECHO_SERVER)],
            "enabled": True,
        }
    }


def test_load_mcp_server_configs_filters_disabled(echo_config):
    cfg = dict(echo_config)
    cfg["off"] = {"command": "nope", "enabled": False}
    cfg["broken"] = {"args": []}
    enabled = load_mcp_server_configs({"mcp_servers": cfg})
    assert set(enabled.keys()) == {"echo"}


@pytest.mark.asyncio
async def test_stdio_client_full_handshake(echo_config):
    entry = echo_config["echo"]
    client = MCPStdioClient("echo", entry["command"], entry["args"], request_timeout=20)
    await client.start()
    try:
        assert client.alive
        assert client.server_info.get("name") == "echo-mcp"
        tools = client.tools
        names = {t["name"] for t in tools}
        assert {"echo_tool", "echo_fail"} <= names
        result = await client.call_tool("echo_tool", {"message": "hello"})
        text = "".join(
            c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
        )
        assert "hello" in text
    finally:
        await client.stop()
    assert not client.alive


@pytest.mark.asyncio
async def test_mcp_adapter_wraps_tools_as_capabilities(echo_config, tmp_path):
    entry = echo_config["echo"]
    client = MCPStdioClient("echo", entry["command"], entry["args"], request_timeout=20)
    await client.start()
    try:
        registry = CapabilityRegistry()
        manager = AuthorizationManager(workspace_root=tmp_path)
        for tool in client.tools:
            registry.register(MCPToolAdapter(client, "echo", tool))

        adapter = registry.get("mcp_echo_echo_tool")
        assert adapter is not None
        spec = adapter.spec()
        assert spec.kind == "deterministic_tool"
        assert spec.side_effect == "external"
        assert spec.required_permissions == ("mcp:echo/echo_tool",)

        grant = manager.issue_grant(
            "plan-mcp", ["mcp_echo_echo_tool"], limits=GrantLimits(max_tool_calls=3, max_model_calls=0)
        )
        result = await adapter.run(
            CapabilityRequest(
                work_item_id="s1",
                goal="echo test",
                inputs={"message": "via grant"},
                grant_id=grant.grant_id,
            ),
            {"authorization_manager": manager, "grant_id": grant.grant_id},
        )
        assert result.status == "completed"
        assert "via grant" in json.dumps(result.output, ensure_ascii=False)
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_mcp_adapter_denied_without_grant(echo_config, tmp_path):
    entry = echo_config["echo"]
    client = MCPStdioClient("echo", entry["command"], entry["args"], request_timeout=20)
    await client.start()
    try:
        manager = AuthorizationManager(workspace_root=tmp_path)  # enforce=True 默认
        registry = CapabilityRegistry()
        registry.register(MCPToolAdapter(client, "echo", client.tools[0]))
        adapter = registry.get("mcp_echo_echo_tool")
        result = await adapter.run(
            CapabilityRequest(work_item_id="s1", goal="x", inputs={}, grant_id=None),
            {"authorization_manager": manager, "grant_id": None},
        )
        # enforce 模式下无 Grant → MCP 调用不会发生
        assert result.status == "failed"
        assert result.error
    finally:
        await client.stop()


import json  # noqa: E402


# -*- coding: utf-8 -*-
"""追加：MCP 大单行响应回归测试（2026-09-01 根因：asyncio 流默认 64KB 行上限）。"""
import sys
from pathlib import Path

pytest = sys.modules.get("pytest")
if pytest is None:  # 直接以脚本运行时的兜底
    import pytest

PYTHON_DIR = Path(__file__).resolve().parent.parent.parent / "python"
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

from mcp_client import MCPStdioClient  # noqa: E402

BIG_SERVER = Path(__file__).resolve().parent / "fixtures" / "mcp_biglist_server.py"


@pytest.mark.asyncio
async def test_stdio_client_large_single_line_tools_list():
    """tools/list 单行响应 >64KB（notion ~76KB 场景）：放宽 limit 后应完整收到。"""
    client = MCPStdioClient("big", sys.executable, [str(BIG_SERVER)], request_timeout=20)
    await client.start()
    try:
        assert client.alive
        names = {t["name"] for t in client.tools}
        assert "big_tool" in names
        assert len(next(t for t in client.tools if t["name"] == "big_tool")["description"]) > 64 * 1024
    finally:
        await client.stop()
    assert not client.alive


@pytest.mark.asyncio
async def test_failed_send_removes_pending_request(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    client = MCPStdioClient("broken", sys.executable, [])
    client.process = SimpleNamespace(returncode=None)
    monkeypatch.setattr(client, "_send", AsyncMock(side_effect=BrokenPipeError("closed")))
    with pytest.raises(BrokenPipeError):
        await client._request("tools/list", {})
    assert not client._pending


@pytest.mark.asyncio
async def test_cancelled_initialize_reaps_process_and_pipe_tasks(tmp_path):
    import asyncio
    script = tmp_path / "never_initialize.py"
    script.write_text("import time\nwhile True: time.sleep(1)\n", encoding="utf-8")
    client = MCPStdioClient("cancel-start", sys.executable, [str(script)], request_timeout=60)
    task = asyncio.create_task(client.start())
    for _ in range(100):
        if client.process is not None:
            break
        await asyncio.sleep(0.02)
    process = client.process
    assert process is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert process.returncode is not None
    assert client.process is None
    assert client._reader_task is None and client._stderr_task is None
    assert not client._pending
    await client.stop()  # idempotent after cancellation
