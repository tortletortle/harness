#!/usr/bin/env python3
"""快速测试 API 连接性（走运行时生效配置）。

历史坑（2026-09-28 修复）：本脚本原先直接读 config/models.yaml 原文，而该文件里的
api_keys 是 "PUT-REAL-KEY-IN-config/models.local.yaml" 占位符、base_urls 也是旧地址。
结果是无论线上是否正常，都会稳定输出 4/4 HTTP 401，掩盖真实状态。

现在改为复用 api.server._load_models_config()，与运行时（models.yaml +
models.local.yaml + .env 合并）完全一致，并且只测配置里真实声明的角色模型，
不再硬编码模型名。凭据只从环境变量读取，永不打印。

用法:
    python scripts/test_api_connectivity.py            # 测所有 chat 角色的主/备
    python scripts/test_api_connectivity.py --dry-run  # 只列候选，不打上游
    python scripts/test_api_connectivity.py --role designer executor
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
except Exception:  # noqa: BLE001 - dotenv 缺失时依赖真实环境变量
    pass

from llm_client import LLMClient, Message, OpenAICompatibleProvider  # noqa: E402

# 非 chat-completions 协议的角色：用 chat 探测必然假阳性失败，默认跳过
NON_CHAT_ROLES = {
    "stepfun_asr": "语音识别端点，非 chat 协议",
    "stepfun_tts": "语音合成端点，非 chat 协议",
    "stepfun_audio": "音频对话端点，需音频输入",
    "stepfun_image_edit": "图像编辑端点，非 chat 协议",
    "stepfun_search": "检索端点，非 chat 协议",
}

PROMPT = "请用一句话回复：你好"


def _effective_config() -> Dict[str, Any]:
    from api.server import _load_models_config

    return _load_models_config()


def _base_url(provider: str, config: Dict[str, Any]) -> Optional[str]:
    return (os.getenv("EAS_LLM_BASE_URL_" + provider.upper())
            or (config.get("base_urls", {}) or {}).get(provider))


def _candidates(config: Dict[str, Any], roles: Optional[List[str]]) -> List[Dict[str, Any]]:
    models = config.get("models", {}) or {}
    out: List[Dict[str, Any]] = []
    for role in sorted(models):
        if roles and role not in roles:
            continue
        role_cfg = models.get(role) or {}
        if not isinstance(role_cfg, dict):
            continue
        for slot in ("primary", "fallback"):
            entry = role_cfg.get(slot) or {}
            if not isinstance(entry, dict):
                continue
            provider, model = entry.get("provider"), entry.get("model")
            if not provider or not model:
                continue
            out.append({
                "label": role + "." + slot,
                "role": role,
                "provider": provider,
                "model": model,
                "base_url": _base_url(provider, config),
                "chat": role not in NON_CHAT_ROLES,
            })
    return out


async def _probe(llm: LLMClient, provider: str, model: str) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        response = await llm.chat(provider, [Message(role="user", content=PROMPT)],
                                  model=model, temperature=0.1, max_tokens=64)
        elapsed = round((time.perf_counter() - started) * 1000)
        content = (response.content or "").strip()
        return {"status": "OK" if content else "EMPTY", "ms": elapsed,
                "detail": content[:60].replace("\n", " ")}
    except Exception as exc:  # noqa: BLE001 - 探测失败统一归类
        elapsed = round((time.perf_counter() - started) * 1000)
        code = getattr(getattr(exc, "response", None), "status_code", None)
        status = ("DEAD(" + str(code) + ")") if code else ("ERR(" + type(exc).__name__ + ")")
        return {"status": status, "ms": elapsed, "detail": str(exc)[:120].replace("\n", " ")}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只列候选，不打上游")
    parser.add_argument("--role", nargs="*", help="只测指定角色，如 designer executor")
    parser.add_argument("--all", action="store_true", help="连非 chat 角色也强行探测")
    args = parser.parse_args()

    print("=" * 78)
    print("  API 连接性测试（运行时生效配置）")
    print("=" * 78)

    config = _effective_config()
    candidates = _candidates(config, args.role)
    if not candidates:
        print("没有可探测的候选，检查 config/models.yaml + config/models.local.yaml")
        return 2

    if args.dry_run:
        for item in candidates:
            tag = "chat" if (item["chat"] or args.all) else "skip"
            print("%-26s %-16s %-26s [%s] %s"
                  % (item["label"], item["provider"], item["model"], tag, item["base_url"]))
        return 0

    llm = LLMClient()
    registered = set()
    for provider in sorted({item["provider"] for item in candidates}):
        base = _base_url(provider, config)
        key = os.getenv("EAS_LLM_API_KEY_" + provider.upper())
        if not base:
            print("跳过 %s：没有 base_url" % provider)
            continue
        if not key:
            print("跳过 %s：缺少环境变量 EAS_LLM_API_KEY_%s" % (provider, provider.upper()))
            continue
        llm.register_provider(OpenAICompatibleProvider(name=provider, base_url=base, api_key=key))
        registered.add(provider)
        print("注册 %s -> %s" % (provider, base))

    print()
    print("%-26s %-16s %-26s %-12s %9s" % ("角色", "provider", "模型", "状态", "耗时"))
    print("-" * 98)

    results: List[Dict[str, Any]] = []
    for item in candidates:
        if not item["chat"] and not args.all:
            outcome = {"status": "SKIP", "ms": 0, "detail": NON_CHAT_ROLES[item["role"]]}
        elif item["provider"] not in registered:
            outcome = {"status": "NO_KEY", "ms": 0,
                       "detail": "missing EAS_LLM_API_KEY_" + item["provider"].upper()}
        else:
            outcome = await _probe(llm, item["provider"], item["model"])
        merged = dict(item)
        merged.update(outcome)
        results.append(merged)
        print("%-26s %-16s %-26s %-12s %7dms"
              % (item["label"], item["provider"], item["model"], outcome["status"], outcome["ms"]))
        if outcome["status"] not in ("OK", "SKIP"):
            print("%-26s  -> %s" % ("", outcome["detail"]))

    ok = [r for r in results if r["status"] == "OK"]
    bad = [r for r in results if r["status"] not in ("OK", "SKIP", "NO_KEY")]
    skipped = [r for r in results if r["status"] in ("SKIP", "NO_KEY")]

    print("-" * 98)
    print("OK %d / 失败 %d / 跳过 %d，共 %d 项" % (len(ok), len(bad), len(skipped), len(results)))
    if bad:
        print("\n失败项：")
        for item in bad:
            print("  %-26s %s" % (item["label"], item["detail"]))
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n用户中断")
        raise SystemExit(130)

