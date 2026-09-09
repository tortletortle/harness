#!/usr/bin/env python3
"""Read-only readiness check for the local daily task executor."""

from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "models.yaml"
TOKEN = ROOT / "config" / "api_token"


def check_public_url(value: str) -> tuple[bool, str]:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "URL 必须使用 http/https 且包含 host"
    host = parsed.hostname
    if host.lower() in {"localhost", "localhost.localdomain"}:
        return False, "拒绝 localhost"
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        return False, f"host 解析失败: {exc}"
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False, f"host 解析到受限地址: {address}"
    return True, "public host"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 Harness 是否具备本地日常任务运行条件")
    parser.add_argument("--offline", action="store_true", help="不要求真实 LLM 凭据，只检查离线运行条件")
    args = parser.parse_args()

    failures: list[str] = []
    if not CONFIG.exists():
        failures.append("缺少 config/models.yaml")
        config: dict = {}
    else:
        config = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}

    providers = config.get("base_urls", {})
    if not providers:
        failures.append("没有配置任何 LLM base URL")
    for provider, url in providers.items():
        ok, detail = check_public_url(str(os.getenv(f"EAS_LLM_BASE_URL_{provider.upper()}") or url))
        print(f"LLM URL {provider}: {'OK' if ok else 'FAIL'} ({detail})")
        if not ok:
            failures.append(f"{provider}: {detail}")
        key_present = bool(os.getenv(f"EAS_LLM_API_KEY_{provider.upper()}"))
        print(f"LLM key {provider}: {'configured' if key_present else 'missing'}")
        if not key_present and not args.offline:
            failures.append(f"缺少 EAS_LLM_API_KEY_{provider.upper()}")

    token_present = bool(os.getenv("EAS_API_TOKEN")) or TOKEN.exists()
    print(f"API token: {'configured' if token_present else 'missing'}")
    if not token_present:
        failures.append("缺少 EAS_API_TOKEN 或 config/api_token")

    try:
        sys.path.insert(0, str(ROOT / "python"))
        from tools import ToolRegistry

        tool_count = len(ToolRegistry().tools)
        print(f"Tools: {tool_count} registered")
        if tool_count < 15:
            failures.append(f"工具数量异常: {tool_count}")
    except Exception as exc:
        failures.append(f"工具注册失败: {type(exc).__name__}")

    print(f"Frontend package: {'OK' if (ROOT / 'frontend' / 'package.json').exists() else 'FAIL'}")
    if not (ROOT / "frontend" / "package.json").exists():
        failures.append("缺少 frontend/package.json")

    if failures:
        print("\nNOT READY")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nREADY: local daily task executor prerequisites are present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
