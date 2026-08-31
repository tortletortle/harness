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
