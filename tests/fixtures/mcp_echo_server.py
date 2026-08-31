"""最小 stdio MCP server（测试桩）：initialize / tools/list / tools/call。

用法：python tests/fixtures/mcp_echo_server.py
行为：echo_tool（回显 arguments 的 JSON）、echo_fail（isError=True）。
"""
import json
import sys

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "echo_tool",
        "description": "回显 arguments 的 JSON（测试桩）",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
    {
        "name": "echo_fail",
        "description": "总是返回 isError 的测试桩",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def reply(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def main():
    initialized = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except Exception:
            continue
        method = message.get("method")
        msg_id = message.get("id")
        if msg_id is None:
            continue
        if method == "initialize":
            reply(
                msg_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "echo-mcp", "version": "1.0.0"},
                },
            )
        elif method == "tools/list":
            reply(msg_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "echo_tool":
                reply(
                    msg_id,
                    {
                        "content": [
                            {"type": "text", "text": json.dumps({"echo": args}, ensure_ascii=False)}
                        ]
                    },
                )
            else:
                reply(
                    msg_id,
                    {
                        "content": [{"type": "text", "text": f"echo_fail: {name}"}],
                        "isError": True,
                    },
                )
        else:
            reply(msg_id, {})


if __name__ == "__main__":
    main()
