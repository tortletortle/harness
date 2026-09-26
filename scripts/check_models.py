#!/usr/bin/env python
"""模型配置校验闸门：探测运行时生效的主/备模型，揪出"死了还躺在配置里"的项。

问题背景：模型/provider 配置分散在 models.yaml + models.local.yaml + .env，且
designer 里还有硬编码兜底。死模型（4xx / 断连）曾导致主模型一瞬断就整链崩溃。
本脚本加载与运行时一致的生效配置，对每个 (provider, model) 做一次性轻量探测
（max_tokens=1），输出 OK / DEAD，任一主模型 DEAD 即非零退出——可在 CI 或发布前拦下。

用法（真实探测，会打少量真实调用）：
    python scripts/check_models.py
只探测配置（不真打上游）：
    python scripts/check_models.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from llm_client import LLMClient, Message, OpenAICompatibleProvider  # noqa: E402


def _effective_config() -> Dict[str, Any]:
    """与运行时一致的生效模型配置（models.yaml + models.local 合并 + env 密钥/URL）。"""
    from api.server import _load_models_config

    return _load_models_config()


def _candidates(config: Dict[str, Any]) -> List[Tuple[str, str, str, str]]:
    """收集主/备模型候选 (role, provider, model, base_url)。"""
    base_urls = config.get("base_urls", {})
    models = config.get("models", {})
    seen = set()
    out: List[Tuple[str, str, str, str]] = []
    for role in ("designer", "executor"):
        role_cfg = models.get(role) or {}
        for slot in ("primary", "fallback"):
            entry = role_cfg.get(slot) or {}
            provider = entry.get("provider")
            model = entry.get("model")
            if not provider or not model:
                continue
            key = (provider, model)
            if key in seen:
                continue
            seen.add(key)
            base = (os.getenv(f"EAS_LLM_BASE_URL_{provider.upper()}")
                    or base_urls.get(provider))
            if base and base_urls:
                out.append((f"{role}.{slot}", provider, model, base))
    return out


async def _probe(llm: LLMClient, provider: str, model: str) -> str:
    try:
        resp = await llm.chat(provider, [Message(role="user", content="ping")],
                              model=model, temperature=0.0, max_tokens=1)
        return "OK" if resp.content is not None else "empty"
    except Exception as ex:  # noqa: BLE001 - 探测失败统一归类
        status = getattr(getattr(ex, "response", None), "status_code", None)
        if status is not None:
            return f"DEAD({status})"
        return f"ERR({type(ex).__name__})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印候选，不真打上游")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    config = _effective_config()
    candidates = _candidates(config)
    if not candidates:
        print("没有可探测的模型候选（检查 config/models.yaml + models.local.yaml）")
        return 1
    if args.dry_run:
        for role, provider, model, base in candidates:
            print(f"{role:<18} {provider:<12} {model:<24} {base}")
        return 0

    llm = LLMClient()
    for provider in {p for _, p, _, _ in candidates}:
        base = os.getenv(f"EAS_LLM_BASE_URL_{provider.upper()}") \
            or config.get("base_urls", {}).get(provider)
        key = os.getenv(f"EAS_LLM_API_KEY_{provider.upper()}")
        if not base or not key:
            continue
        llm.register_provider(OpenAICompatibleProvider(name=provider, base_url=base, api_key=key))

    results: List[Dict[str, Any]] = []
    for role, provider, model, _base in candidates:
        if provider not in llm.providers:
            results.append({"role": role, "provider": provider, "model": model, "status": "NO_KEY"})
            continue
        try:
            status = asyncio.run(_probe(llm, provider, model))
        except Exception:  # noqa: BLE001
            status = "ERR"
        results.append({"role": role, "provider": provider, "model": model, "status": status})
        print(f"{role:<18} {provider:<12} {model:<24} {status}")

    dead = [r for r in results if r["status"].startswith("DEAD")]
    if args.json:
        import json as _json
        print(_json.dumps({"results": results, "dead_count": len(dead)}, ensure_ascii=False))
    print(f"\n共 {len(results)} 项，DEAD {len(dead)} 项")
    return 1 if dead else 0


if __name__ == "__main__":
    raise SystemExit(main())