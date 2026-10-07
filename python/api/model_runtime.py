"""Apply persisted model settings onto the live process without a restart.

Designer / executor / knowledge hold dict references into ``state.config``.
Updates mutate those same objects so the next LLM call sees new
provider/model IDs. In-flight requests keep the provider instance they
already started with.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from api.shared import _load_models_config
from logging_config import get_logger

logger = get_logger(__name__)


def _replace_mapping(target: dict, source: dict) -> None:
    target.clear()
    target.update(source)


def _merge_models_inplace(current: dict, loaded: dict) -> None:
    for role, settings in loaded.items():
        if not isinstance(settings, dict):
            current[role] = settings
            continue
        existing = current.get(role)
        if not isinstance(existing, dict):
            current[role] = dict(settings)
            continue
        for key, value in settings.items():
            if key in {"primary", "fallback"} and isinstance(value, dict):
                slot = existing.get(key)
                if isinstance(slot, dict):
                    _replace_mapping(slot, value)
                else:
                    existing[key] = dict(value)
            else:
                existing[key] = value


def _sync_providers(runtime: dict, client: Any) -> None:
    from llm_client import OpenAICompatibleProvider

    urls = runtime.get("base_urls") or {}
    keys = runtime.get("api_keys") or {}
    providers = getattr(client, "providers", None)
    if not isinstance(providers, dict):
        return
    live = set()
    for name, key in keys.items():
        url = urls.get(name)
        if not key or not url:
            continue
        try:
            client.register_provider(
                OpenAICompatibleProvider(name=name, base_url=url, api_key=key)
            )
            live.add(name)
        except Exception as exc:
            logger.warning("热加载 provider 失败 {}: {}", name, type(exc).__name__)
    for stale in [name for name in list(providers) if name not in live]:
        providers.pop(stale, None)


def _sync_chat_and_agents(app_state: Any, runtime: dict) -> None:
    models = runtime.get("models") or {}
    designer = (models.get("designer") or {}).get("primary") or {}
    executor = (models.get("executor") or {}).get("primary") or {}
    chat = runtime.setdefault("chat", {})
    if isinstance(chat, dict) and designer.get("provider"):
        chat["provider"] = designer.get("provider")
        chat["model"] = designer.get("model") or chat.get("model") or ""
    manager = getattr(app_state, "chat_manager", None)
    if manager is not None:
        manager.config = runtime
        for session in getattr(manager, "sessions", {}).values():
            session.config = chat
            session.provider = str(chat.get("provider") or "")
            session.model = str(chat.get("model") or "")
    registry = getattr(app_state, "capability_registry", None)
    if registry is None or not executor.get("provider"):
        return
    for spec in registry.list_specs():
        if spec.kind != "agent_tool":
            continue
        tool = registry.get(spec.id)
        if tool is None:
            continue
        if hasattr(tool, "provider_name"):
            tool.provider_name = executor.get("provider", "")
        if hasattr(tool, "model"):
            tool.model = executor.get("model", "")


def apply_runtime_models(directory: Path, app_state: Any | None = None) -> None:
    """Reload URLs, keys and role models into the running AppState."""
    from api.shared import state as default_state

    app_state = default_state if app_state is None else app_state
    runtime = getattr(app_state, "config", None)
    if not isinstance(runtime, dict):
        return
    loaded = _load_models_config(directory)
    _replace_mapping(runtime.setdefault("base_urls", {}), loaded.get("base_urls") or {})
    _replace_mapping(runtime.setdefault("api_keys", {}), loaded.get("api_keys") or {})
    _merge_models_inplace(runtime.setdefault("models", {}), loaded.get("models") or {})
    client = getattr(app_state, "llm_client", None)
    if client is not None:
        _sync_providers(runtime, client)
    _sync_chat_and_agents(app_state, runtime)
