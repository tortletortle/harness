"""Explicit, bounded upstream checks against saved provider endpoints only."""

from __future__ import annotations
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field
from api import secret_store
from api.configuration import ConfigProblem, read_configuration

_GATE = asyncio.Lock()
_MAX_BYTES = 256 * 1024


class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Existing saved IDs can use Unicode letters (e.g. 国模). The endpoint
    # resolver still requires exact membership in saved providers.
    provider: str = Field(
        min_length=1, max_length=64, pattern=r"^[\p{L}][\p{L}\p{N}_-]*$"
    )
    revision: str = Field(min_length=1, max_length=128)
    mode: Literal["connectivity", "model"]
    model: str = Field(default="", max_length=256)
    acknowledge_cost: bool = False


def _endpoint(directory, runtime, request):
    cfg = read_configuration(directory, runtime)
    if cfg["revision"] != request.revision:
        raise ConfigProblem("配置版本已变化，请重新加载后再检测。", 409)
    if request.provider not in cfg["base_urls"]:
        raise ConfigProblem("请选择已保存的提供商；检测不会访问浏览器提交的任意地址。")
    url = (
        os.getenv(f"EAS_LLM_BASE_URL_{request.provider.upper()}")
        or cfg["base_urls"][request.provider]
    )
    try:
        parsed = urlsplit(url)
        if (
            not (parsed.scheme in {"https", "http"} and parsed.hostname)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in url)
        ):
            raise ValueError()
        _ = parsed.port
    except ValueError:
        raise ConfigProblem("已保存或环境变量覆盖的端点无效，请先修正端点。") from None
    if request.mode == "model" and (
        not request.acknowledge_cost
        or not request.model.strip()
        or any(ord(c) < 32 for c in request.model)
    ):
        raise ConfigProblem("模型测试需要填写模型 ID 并明确确认可能产生费用。")
    return url.rstrip("/"), secret_store.effective_key(directory, request.provider)


async def probe_configuration(directory: Path, runtime: dict, request: ProbeRequest):
    base, key = _endpoint(directory, runtime, request)
    if _GATE.locked():
        raise ConfigProblem("已有检测进行中，请稍后再试。", 429)
    async with _GATE:
        started = time.monotonic()

        def result(code, message, *, reachable=False, status=None, models=None):
            return {
                "ok": code == "ok",
                "code": code,
                "message": message,
                "provider": request.provider,
                "mode": request.mode,
                "model": request.model.strip() if request.mode == "model" else None,
                "reachable": reachable,
                "http_status": status,
                "latency_ms": round((time.monotonic() - started) * 1000),
                "models": models or [],
            }

        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        path = "/models" if request.mode == "connectivity" else "/chat/completions"
        method = "GET" if request.mode == "connectivity" else "POST"
        payload = (
            None
            if request.mode == "connectivity"
            else {
                "model": request.model.strip(),
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 16,
                "stream": False,
            }
        )
        status = None
        try:
            # No redirects (credential leakage), inherited proxies, retries, tool
            # calls, or user-supplied prompts. Timeout bounds the entire response.
            async with asyncio.timeout(20):
                async with httpx.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(12, connect=5),
                    limits=httpx.Limits(max_connections=1),
                ) as client:
                    async with client.stream(
                        method, base + path, headers=headers, json=payload
                    ) as response:
                        status = response.status_code
                        if not 200 <= status < 300:
                            code, message = {
                                400: (
                                    "request_rejected",
                                    "上游拒绝请求；请检查模型 ID 或 OpenAI Chat Completions 兼容性。",
                                ),
                                401: (
                                    "authentication_failed",
                                    "端点可达，但密钥缺失或无效。",
                                ),
                                403: (
                                    "permission_denied",
                                    "端点可达，但该密钥无访问权限。",
                                ),
                                404: (
                                    "not_found",
                                    "端点可达，但模型或接口路径不存在；/models 不支持不代表推理不可用。",
                                ),
                                429: (
                                    "rate_or_quota_limited",
                                    "上游限流或配额不足，请稍后或检查额度。",
                                ),
                            }.get(
                                status,
                                (
                                    (
                                        "redirect_blocked",
                                        "上游返回重定向，已阻止转发密钥。",
                                    )
                                    if 300 <= status < 400
                                    else (
                                        "upstream_error",
                                        "上游返回错误，请检查服务状态或协议兼容性。",
                                    )
                                ),
                            )
                            return result(code, message, reachable=True, status=status)
                        body = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=8192):
                            body.extend(chunk)
                            if len(body) > _MAX_BYTES:
                                return result(
                                    "response_too_large",
                                    "响应超过 256 KiB 检测上限，已停止读取。",
                                    reachable=True,
                                    status=status,
                                )
                        try:
                            data = json.loads(body)
                        except (ValueError, UnicodeError):
                            return result(
                                "invalid_response",
                                "HTTP 可达，但返回的不是有效 JSON API 响应。",
                                reachable=True,
                                status=status,
                            )
                        if not isinstance(data, dict) or data.get("error"):
                            return result(
                                "invalid_response",
                                "HTTP 可达，但响应格式不符合接口约定或包含错误。",
                                reachable=True,
                                status=status,
                            )
                        if request.mode == "connectivity":
                            if not isinstance(data.get("data"), list):
                                return result(
                                    "invalid_response",
                                    "HTTP 可达，但没有 OpenAI 兼容模型列表；可单独测试已知模型 ID。",
                                    reachable=True,
                                    status=status,
                                )
                            models = list(
                                dict.fromkeys(
                                    row["id"]
                                    for row in data["data"]
                                    if isinstance(row, dict)
                                    and isinstance(row.get("id"), str)
                                    and 0 < len(row["id"]) <= 256
                                    and not (key and key in row["id"])
                                    and not any(
                                        ord(c) < 32 or ord(c) == 127 for c in row["id"]
                                    )
                                )
                            )[:200]
                            return result(
                                "ok",
                                "端点与模型列表接口可用；列表可见不等于模型推理已通过（最多展示 200 个）。",
                                reachable=True,
                                status=status,
                                models=models,
                            )
                        choices = data.get("choices")
                        message = (
                            choices[0].get("message")
                            if isinstance(choices, list)
                            and choices
                            and isinstance(choices[0], dict)
                            else None
                        )
                        content = (
                            message.get("content")
                            if isinstance(message, dict)
                            else None
                        )
                        if not isinstance(content, str) or not content.strip():
                            return result(
                                "empty_response",
                                "上游未返回可验证的文本；可能需要更多输出预算或不支持文本对话。",
                                reachable=True,
                                status=status,
                            )
                        # Do not echo upstream content, request IDs, URLs or error bodies.
                        return result(
                            "ok",
                            "指定模型已完成一次最小文本推理；不代表视觉、音频或工具调用能力已验证。",
                            reachable=True,
                            status=status,
                        )
        except (TimeoutError, httpx.TimeoutException):
            return result(
                "timeout",
                "检测超时（总上限 20 秒）。"
                + (
                    "模型请求可能已在上游执行或计费。"
                    if request.mode == "model"
                    else "未自动重试。"
                ),
                reachable=status is not None,
                status=status,
            )
        except httpx.ConnectError:
            return result(
                "connection_failed", "连接失败：请检查 DNS、TLS 证书、端口或网络。"
            )
        except httpx.RequestError:
            return result(
                "network_error",
                "网络或传输失败；未重试，也未回显上游敏感内容。",
                reachable=status is not None,
                status=status,
            )
