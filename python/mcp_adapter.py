"""MCP 工具适配器：把外部 MCP server 的工具包装成 Capability（走 Grant 边界）。"""

from __future__ import annotations

from typing import Any, Dict

from capabilities import CapabilityRequest, CapabilityResult, CapabilitySpec


class MCPToolAdapter:
    """把一个 MCP 工具包装成 Capability。

    授权边界：与普通工具一致——executor 先按 Grant.authorize 校验（工具白名单 +
    参数资源键 + 预算），denied 时 MCP 调用不会发生。注意 MCP 工具参数多为非路径
    键，资源校验主要落在"工具白名单"粒度；主机级约束由 MCP server 自身实现。
    """

    def __init__(self, client: Any, server_name: str, tool: Dict[str, Any]) -> None:
        self.client = client
        self.server_name = server_name
        self.tool = tool
        self.remote_name = str(tool.get("name") or "")
        from mcp_client import sanitize_tool_id

        self.tool_id = sanitize_tool_id("mcp", server_name, self.remote_name)
        self.description = str(tool.get("description") or f"MCP 工具（{server_name}）")
        self.input_schema = tool.get("inputSchema") or {}

    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            id=self.tool_id,
            version="1.0.0",
            kind="deterministic_tool",
            input_schema=self.input_schema if isinstance(self.input_schema, dict) else {},
            required_permissions=(f"mcp:{self.server_name}/{self.remote_name}",),
            side_effect="external",
            idempotent=False,
            description=f"[MCP:{self.server_name}] {self.description}"[:500],
        )

    async def run(self, request: CapabilityRequest, runtime_context: Any) -> CapabilityResult:
        manager = _context_value(runtime_context, "authorization_manager")
        grant_id = request.grant_id or _context_value(runtime_context, "grant_id")
        inputs = dict(request.inputs or {})
        instruction = str(inputs.pop("instruction", inputs.pop("action", "call")))

        if manager is not None:
            decision = manager.authorize(grant_id, self.tool_id, instruction, inputs)
            if not decision.allowed:
                return CapabilityResult(
                    status="failed",
                    error=decision.message,
                    usage=decision.usage,
                )

        arguments = dict(inputs)
        arguments.pop("instruction", None)
        arguments.pop("action", None)
        try:
            result = await self.client.call_tool(self.remote_name, arguments)
        except Exception as exc:
            return CapabilityResult(
                status="failed",
                output={"tool_id": self.tool_id, "error_detail": str(exc)[:500]},
                error=f"MCP 调用失败: {exc}",
            )

        text_parts = []
        for item in result.get("content", []) or []:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        joined = "\n".join(text_parts)
        is_error = bool(result.get("isError", False))

        return CapabilityResult(
            status="failed" if is_error else "completed",
            output={
                "tool_id": self.tool_id,
                "server": self.server_name,
                "remote_tool": self.remote_name,
                "content": joined[:20000],
                "is_error": is_error,
            },
            error=(joined[:500] if is_error else None),

        )


def _context_value(context: Any, name: str, default: Any = None) -> Any:
    if context is None:
        return default
    if isinstance(context, dict):
        return context.get(name, default)
    return getattr(context, name, default)


__all__ = ["MCPToolAdapter"]
