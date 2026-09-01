"""大响应 stdio MCP server（测试桩）：tools/list 返回 >64KB 的单行 JSON。

回归 2026-09-01：asyncio 子进程流默认 readline 上限 64KB，会吞掉大响应导致
tools/list 悬挂超时（notion 实测 ~76KB）。客户端放宽 limit 后本桩应正常通过。
"""
import json
import sys

PROTOCOL_VERSION = "2024-11-05"

BIG_DESCRIPTION = "x" * (100 * 1024)

TOOLS = [
    {
        "name": "big_tool",
        "description": BIG_DESCRIPTION,
        "inputSchema": {"type": "object", "properties": {}},
    }
]


def reply(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def main():
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
                    "serverInfo": {"name": "biglist-mcp", "version": "1.0.0"},
                },
            )
        elif method == "tools/list":
            reply(msg_id, {"tools": TOOLS})
        else:
            reply(msg_id, {})


if __name__ == "__main__":
    main()
