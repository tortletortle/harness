"""MCP stdio 客户端：连接外部 MCP server（JSON-RPC 2.0 over stdio，换行分帧）。

设计原则（docs/TOOL_INTERFACE_DESIGN.md）：
- 只实现必需子集：initialize / notifications/initialized / tools/list / tools/call；
- 每个请求带自增 id，通知（无 id）丢弃并记录；
- stderr 单独消费（只记日志，绝不混入协议流）；
- 每请求默认 30s 超时；进程退出后所有挂起请求立即失败；
- Windows 兼容：asyncio.create_subprocess_exec（Proactor 循环）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

from logging_config import get_logger

logger = get_logger(__name__)

PROTOCOL_VERSION = "2024-11-05"
_DEFAULT_TIMEOUT = 30.0
# MCP 单行 JSON 响应可能很大（notion tools/list 实测 ~76KB）：asyncio 流默认 64KB
# readline 上限会吞掉大响应导致请求悬挂超时。根因实测：raw probe 下 6s 返回完整响应。
_STREAM_LIMIT = 16 * 1024 * 1024

_SANITIZE = re.compile(r"[^a-zA-Z0-9_]")


def sanitize_tool_id(*parts: str) -> str:
    joined = "_".join(str(p) for p in parts)
    return _SANITIZE.sub("_", joined).strip("_").lower() or "mcp_tool"


class MCPStdioClient:
    """一个外部 MCP server（stdio）的连接与协议交互。"""

    def __init__(
        self,
        name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        request_timeout: float = _DEFAULT_TIMEOUT,
        stream_limit: int = _STREAM_LIMIT,
        startup_settle: float = 0.5,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = env
        self.cwd = cwd
        self.request_timeout = float(request_timeout)
        self.stream_limit = int(stream_limit)
        self.startup_settle = float(startup_settle)
        self.process: Optional[asyncio.subprocess.Process] = None
        self.server_info: Dict[str, Any] = {}
        self.tools: List[Dict[str, Any]] = []
        self._next_id = 1
        self._restarts = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._stderr_task: Optional[asyncio.Task] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._closed = False

    # ---- 进程与协议生命周期 ----

    async def start(self) -> None:
        self._closed = False  # 自愈重启路径：stop() 会置位，重启前必须复位
        env = dict(os.environ)
        if self.env:
            env.update({str(k): str(v) for k, v in self.env.items()})
        self.process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self.cwd,
            limit=self.stream_limit,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            await self.initialize()
        except BaseException:
            await self.stop()
            raise
        # 某些 server（如 @notionhq/notion-mcp-server）在 initialize 后需要短暂
        # 稳定期：立即跟随的请求会被对端关闭连接（真实观察，第 2 期续作验证）。
        await asyncio.sleep(self.startup_settle)
        self.tools = await self._list_tools_with_retry()
        logger.info("MCP server {} 就绪（{} 个工具）", self.name, len(self.tools))

    async def initialize(self) -> Dict[str, Any]:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "eas-agent-harness", "version": "1.0.0"},
            },
        )
        self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        await self._notify("notifications/initialized", {})
        return result

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._request("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [t for t in tools if isinstance(t, dict) and t.get("name")]

    async def _list_tools_with_retry(self) -> List[Dict[str, Any]]:
        # 断线自愈：连接被对端关闭或首次 tools/list 超时（冷启动 npx 下载慢）时，
        # 重启 server 并重试一次。
        try:
            return await self.list_tools()
        except (RuntimeError, TimeoutError) as exc:
            retriable = isinstance(exc, TimeoutError) or "连接关闭" in str(exc)
            if not retriable or self._restarts >= 1:
                raise
            logger.warning("MCP {} 首次 tools/list 未完成（{}），自愈重启一次", self.name, exc)
            self._restarts += 1
            await self.stop()
            await self.start()
            return self.tools

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("tools/call", {"name": name, "arguments": dict(arguments or {})})

    async def stop(self) -> None:
        self._closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError(f"MCP server {self.name} 已停止"))
        self._pending.clear()
        process = self.process
        try:
            if process is not None:
                # EOF gives cooperative stdio servers a chance to exit cleanly.
                if process.stdin:
                    process.stdin.close()
                    try:
                        await asyncio.wait_for(process.stdin.wait_closed(), 1)
                    except (OSError, RuntimeError, asyncio.TimeoutError):
                        pass
                try:
                    await asyncio.wait_for(process.wait(), 1)
                except asyncio.TimeoutError:
                    if process.returncode is None:
                        if sys.platform == "win32":
                            # npx/cmd wrappers own child processes which inherit the pipes.
                            # Terminate this owned tree, not arbitrary node processes.
                            killer = await asyncio.create_subprocess_exec(
                                "taskkill", "/PID", str(process.pid), "/T", "/F",
                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                            )
                            await killer.wait()
                        else:
                            process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 3)
                    except asyncio.TimeoutError:
                        if process.returncode is None:
                            process.kill()
                        # A descendant can keep stdout open after the direct child exits.
                        transport = getattr(process, "_transport", None)
                        if transport is not None:
                            transport.close()
                        await asyncio.wait_for(process.wait(), 3)
        finally:
            tasks = [t for t in (self._reader_task, self._stderr_task) if t is not None]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            # CPython exposes no public Process.close(); close remaining pipe transports
            # while the Proactor loop is alive, rather than relying on __del__.
            transport = getattr(process, "_transport", None)
            if transport is not None:
                transport.close()
                await asyncio.sleep(0)
            self._reader_task = self._stderr_task = None
            self.process = None

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None and not self._closed

    # ---- 内部：帧读写与请求配对 ----

    async def _read_loop(self) -> None:
        assert self.process and self.process.stdout
        try:
            while True:
                try:
                    line = await self.process.stdout.readline()
                except ValueError as exc:
                    # 单行超过流上限：明确终止并让挂起请求失败（limit 已放宽到 16MB，
                    # 触发即说明对端行为异常，宁可诚实失败也不静默挂死）。
                    logger.error("MCP {} 单行超过流上限，终止读取循环: {}", self.name, exc)
                    break
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except Exception:
                    logger.debug("MCP {} 非协议行丢弃: {}", self.name, text[:120])
                    continue
                self._dispatch(message)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("MCP {} 读取循环异常: {}", self.name, e)
        # 进程退出：失败所有挂起请求
        if not self._closed:
            self._closed = True
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(RuntimeError(f"MCP server {self.name} 连接关闭"))
            self._pending.clear()

    def _dispatch(self, message: Dict[str, Any]) -> None:
        if not isinstance(message, dict):
            return
        msg_id = message.get("id")
        if msg_id is None:
            logger.debug("MCP {} 通知: {}", self.name, str(message.get("method", ""))[:80])
            return
        fut = self._pending.get(msg_id)
        if fut is None or fut.done():
            return
        if "error" in message:
            err = message["error"] or {}
            fut.set_exception(RuntimeError(f"{err.get('code')}: {err.get('message', 'MCP error')}"))
        else:
            fut.set_result(message.get("result", {}))

    async def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                logger.debug("MCP {} stderr: {}", self.name, line.decode("utf-8", "replace").strip()[:160])
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _request(self, method: str, params: Dict[str, Any]) -> Any:
        if self.process is None or self.process.returncode is not None:
            raise RuntimeError(f"MCP server {self.name} 未运行")
        msg_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        message = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        try:
            await self._send(message)
            return await asyncio.wait_for(fut, timeout=self.request_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(msg_id, None)
            raise TimeoutError(f"MCP {self.name}.{method} 超时（{self.request_timeout}s）")
        finally:
            self._pending.pop(msg_id, None)
            if not fut.done():
                fut.cancel()

    async def _send(self, message: Dict[str, Any]) -> None:
        assert self.process and self.process.stdin
        data = json.dumps(message, ensure_ascii=False) + "\n"
        self.process.stdin.write(data.encode("utf-8"))
        await self.process.stdin.drain()

    async def _notify(self, method: str, params: Dict[str, Any]) -> None:
        if self.process and self.process.stdin:
            data = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}, ensure_ascii=False) + "\n"
            self.process.stdin.write(data.encode("utf-8"))
            await self.process.stdin.drain()


def load_mcp_server_configs(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """从 config 提取 mcp_servers 段（仅 enabled 的），缺省返回空。"""
    servers = (config or {}).get("mcp_servers") or {}
    enabled: Dict[str, Dict[str, Any]] = {}
    for name, entry in servers.items():
        if not isinstance(entry, dict) or not entry.get("enabled", True):
            continue
        command = str(entry.get("command") or "").strip()
        if not command:
            continue
        enabled[str(name)] = {
            "command": command,
            "args": list(entry.get("args") or []),
            "env": dict(entry.get("env") or {}),
            "cwd": entry.get("cwd"),
            "request_timeout": entry.get("request_timeout", _DEFAULT_TIMEOUT),
        }
    return enabled


__all__ = ["MCPStdioClient", "load_mcp_server_configs", "sanitize_tool_id", "PROTOCOL_VERSION"]

