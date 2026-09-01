"""MCP stdio server：把 CapabilityRegistry 反向暴露给外部 MCP 客户端。

定位（对照 docs/AGENT_HARNESS_DESIGN.md「生态层」）：
- mcp_client/mcp_adapter 是「消费 MCP 生态」的方向；
- 本模块是「被 MCP 生态消费」的方向：任何 MCP 客户端
  （Claude Desktop / AutoClaw / 其他 Agent）都可以把本进程当作工具源。

协议：JSON-RPC 2.0 over stdio，换行分帧（与 mcp_client.py 对等）。
只实现必需子集：initialize / notifications/initialized / tools/list / tools/call / ping。

安全边界（沿用 Grant 模型，不新造机制）：
- 默认只暴露 side_effect != "external" 的确定性能力（manual_tool 永远排除）；
- 每次tools/call 独立发放短时 Grant（1 次工具调用、0 次模型调用、60s TTL）；
- authorize 拒绝即拒绝，不降级。

用法：
    python mcp_server.py                      # 默认白名单
    python mcp_server.py --allow file_creator,project_memory
    python mcp_server.py --allow http_api     # 显式放开外部副作用工具
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from authorization import AuthorizationManager, GrantLimits
from capabilities import (
    CapabilityRegistry,
    CapabilityRequest,
    ToolCapabilityAdapter,
)
from tools import get_registry

logger = logging.getLogger("mcp_server")

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "eas-harness"
SERVER_VERSION = "1.0.0"

# 永不通过 MCP 暴露的能力（人工介入通道不属于机器生态）。
_HARD_EXCLUDE = {"manual_tool"}


def build_default_registry() -> CapabilityRegistry:
    """构建与 api/server.py 一致的默认能力注册表（确定性工具包装）。"""
    registry = CapabilityRegistry()
    real = get_registry()
    for meta in real.list_tools():
        tool_id = meta.get("tool_id")
        if tool_id and tool_id not in _HARD_EXCLUDE:
            registry.register(ToolCapabilityAdapter(real, tool_id))
    return registry


def default_allowed_tools(registry: CapabilityRegistry) -> List[str]:
    """默认白名单：排除外部副作用能力（可用 --allow 显式放开）。"""
    allowed: List[str] = []
    for spec in registry.list_specs():
        if spec.side_effect == "external":
            continue
        allowed.append(spec.id)
    return allowed


class MCPCapabilityServer:
    """CapabilityRegistry 的 MCP stdio 服务端。"""

    def __init__(
        self,
        registry: CapabilityRegistry,
        authorization_manager: Optional[AuthorizationManager] = None,
        allowed_tools: Optional[List[str]] = None,
    ) -> None:
        self.registry = registry
        self.authorization_manager = authorization_manager or AuthorizationManager()
        self.allowed_tools = set(allowed_tools or default_allowed_tools(registry))

    # ---- MCP 方法实现 ----

    def _tool_defs(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        for spec in self.registry.list_specs():
            if spec.id not in self.allowed_tools:
                continue
            schema = spec.input_schema if isinstance(spec.input_schema, dict) and spec.input_schema.get("type") else {
                "type": "object",
                "properties": {},
            }
            tools.append(
                {
                    "name": spec.id,
                    "description": spec.description or spec.id,
                    "inputSchema": schema,
                }
            )
        return tools

    async def _call_capability(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """单次调用 = 单次 Grant：1 次工具调用、0 次模型调用、60s TTL。"""
        cap = self.registry.get(name)
        if cap is None or name not in self.allowed_tools:
            return {"isError": True, "text": f"工具不可用或未授权: {name}"}
        grant = self.authorization_manager.issue_grant(
            subject="mcp-server",
            allowed_tools=[name],
            limits=GrantLimits(max_tool_calls=1, max_model_calls=0),
            ttl_seconds=60,
        )
        grant_id = grant.grant_id
        # 鉴权由 Capability 内部执行（ToolCapabilityAdapter.run 会用本 grant
        # 重新 authorize 并消耗 1 次预算），此处不预检以免双重扣减。
        try:
            cap_result = await self.registry.run(
                name,
                CapabilityRequest(
                    work_item_id="mcp-server",
                    goal=f"MCP tools/call {name}",
                    inputs=dict(arguments or {}),
                    grant_id=grant_id,
                ),
                {"authorization_manager": self.authorization_manager, "grant_id": grant_id},
            )
        except Exception as exc:  # noqa: BLE001 - 协议边界必须兜住
            return {"isError": True, "text": f"工具执行异常: {exc}"}
        payload = json.dumps(
            {
                "success": cap_result.status == "completed",
                "output": cap_result.output,
                "error": cap_result.error,
            },
            ensure_ascii=False,
            default=str,
        )
        return {"isError": cap_result.status != "completed", "text": payload}

    async def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """处理一条 JSON-RPC 请求/通知；通知返回 None。"""
        method = str(msg.get("method") or "")
        has_id = "id" in msg
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self._tool_defs()}
            elif method == "tools/call":
                params = msg.get("params") or {}
                name = str(params.get("name") or "").strip()
                arguments = params.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}
                call = await self._call_capability(name, arguments)
                result = {
                    "content": [{"type": "text", "text": call["text"]}],
                    "isError": call["isError"],
                }
            elif method.startswith("notifications/"):
                return None
            else:
                if not has_id:
                    return None
                return {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
        except Exception as exc:  # noqa: BLE001 - 协议边界必须兜住
            if not has_id:
                return None
            return {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": f"Internal error: {exc}"},
            }
        if not has_id:
            return None
        return {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}

    # ---- stdio 服务循环 ----

    async def serve_stdio(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.buffer.readline)
            if not line:
                break  # EOF：客户端关闭
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("无法解析的请求已丢弃: {}", exc)
                continue
            if not isinstance(msg, dict):
                continue
            reply = await self.handle(msg)
            if reply is None:
                continue
            out = (json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8")
            sys.stdout.buffer.write(out)
            sys.stdout.buffer.flush()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="EAS CapabilityRegistry → MCP stdio server")
    parser.add_argument(
        "--allow",
        default="",
        help="额外允许的工具 id（逗号分隔），可放开 external 副作用工具",
    )
    args = parser.parse_args(argv)

    # stdout 是协议通道：日志一律走 stderr。
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    registry = build_default_registry()
    allowed = default_allowed_tools(registry)
    extra = {t.strip() for t in str(args.allow or "").split(",") if t.strip()}
    allowed = [t for t in dict.fromkeys([*allowed, *extra]) if t not in _HARD_EXCLUDE]
    server = MCPCapabilityServer(registry, allowed_tools=allowed)
    logger.info(
        "EAS MCP server 启动：{} 个可调用能力", len(server._tool_defs())
    )
    asyncio.run(server.serve_stdio())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
