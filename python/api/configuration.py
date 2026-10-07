"""Versioned, non-secret model configuration editing. No runtime reload or LLM calls."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, ConfigDict

from api import secret_store

_LOCK = threading.RLock()
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class ConfigProblem(Exception):
    def __init__(self, message: str, status: int = 400):
        self.status = status
        super().__init__(message)


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    model: str


class RoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: ModelChoice
    fallback: ModelChoice | None = None


class ConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_keys: dict[str, str] = Field(default_factory=dict)  # Legacy empty payload only.
    base_urls: dict[str, str]
    models: dict[str, RoleUpdate] | None = None
    revision: str | None = None


class KeyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=64, pattern=r"^[\p{L}][\p{L}\p{N}_-]*$")
    value: str = Field(default="", max_length=4096)


def _merge(base, override):
    result = dict(base)
    for key, value in override.items():
        result[key] = _merge(result[key], value) if isinstance(result.get(key), dict) and isinstance(value, dict) else value
    return result


def _read(directory: Path):
    try:
        template_bytes = (directory / "models.yaml").read_bytes()
        local_path = directory / "models.local.yaml"
        local_bytes = local_path.read_bytes() if local_path.exists() else b""
        template = yaml.safe_load(template_bytes) or {}
        local = yaml.safe_load(local_bytes) or {}
        if not isinstance(template, dict) or not isinstance(local, dict):
            raise ValueError()
        merged = _merge(template, local)
        if not isinstance(merged.get("base_urls", {}), dict) or not isinstance(merged.get("models", {}), dict):
            raise ValueError()
        revision = hashlib.sha256(template_bytes + b"\0" + local_bytes).hexdigest()
        return local, merged, revision
    except (OSError, ValueError, yaml.YAMLError):
        # Never include YAML contents or secret values in errors.
        raise ConfigProblem("无法读取模型配置文件，请检查文件格式与访问权限。", 500) from None


def _public_models(config):
    result = {}
    for role, settings in config.get("models", {}).items():
        if not isinstance(settings, dict) or not isinstance(settings.get("primary"), dict):
            continue
        result[role] = {}
        for slot in ("primary", "fallback"):
            choice = settings.get(slot)
            if isinstance(choice, dict):
                result[role][slot] = {key: choice[key] if isinstance(choice.get(key), str) else "" for key in ("provider", "model")}
                result[role][slot].update({key: choice[key] for key in ("temperature", "max_tokens") if isinstance(choice.get(key), (int, float))})
    return result


def _snapshot(merged, revision, runtime, directory: Path):
    urls = {str(k): str(v or "") for k, v in merged.get("base_urls", {}).items()}
    models = _public_models(merged)
    overridden = [p for p in urls if os.getenv(f"EAS_LLM_BASE_URL_{p.upper()}")]
    effective_urls = {p: os.getenv(f"EAS_LLM_BASE_URL_{p.upper()}") or url for p, url in urls.items()}
    env_configured = {p: bool(os.getenv(secret_store.env_key_name(p))) for p in urls}
    # Persisted keys live in an encrypted, account-local file; report only
    # presence, never the value, and never let a read failure break the page.
    try:
        persisted = set(secret_store.load_api_keys(directory))
    except secret_store.SecretStoreError:
        persisted = set()
    return {
        "schema_version": 2, "probe_version": 1, "catalog_version": 1, "revision": revision,
        "base_urls": urls, "models": models, "agents": models,
        "api_keys_configured": {p: env_configured[p] or p in persisted for p in urls},
        "api_keys_persisted": {p: p in persisted and not env_configured[p] for p in urls},
        "environment_overrides": overridden,
        "api_key_environment_overrides": [p for p, flag in env_configured.items() if flag],
        "requires_restart": effective_urls != runtime.get("base_urls", {}) or models != _public_models(runtime),
    }


def save_api_key(directory: Path, request: KeyUpdateRequest):
    """Persist (or clear) one provider key in the encrypted local store."""
    with _LOCK:
        _, merged, _ = _read(directory)
        if request.provider not in merged.get("base_urls", {}):
            raise ConfigProblem("请先保存该提供商，再设置密钥。")
        try:
            secret_store.save_api_keys(directory, {request.provider: request.value})
        except secret_store.SecretStoreError as exc:
            raise ConfigProblem(str(exc), exc.status) from None
        value = request.value.strip()
        env_set = bool(os.getenv(secret_store.env_key_name(request.provider)))
        return {
            "provider": request.provider,
            "persisted": bool(value),
            "configured": bool(value) or env_set,
            "cleared": not value,
        }


def read_configuration(directory: Path, runtime: dict):
    with _LOCK:
        _, merged, revision = _read(directory)
        return _snapshot(merged, revision, runtime, directory)


def save_configuration(directory: Path, runtime: dict, request: ConfigUpdateRequest):
    with _LOCK:
        local, merged, revision = _read(directory)
        if request.api_keys:
            raise ConfigProblem(
                "API Key 不随配置保存提交，请在提供商卡片单独加密保存或用环境变量注入。"
            )
        if request.revision is None:
            raise ConfigProblem("请刷新页面后重试，配置保存需要版本校验。", 409)
        if request.revision is not None and request.revision != revision:
            raise ConfigProblem("配置已被其他操作更新。请重新加载后再保存。", 409)
        urls = {}
        existing_names = set(merged.get("base_urls", {}))
        for name, value in request.base_urls.items():
            # Preserve safe pre-existing Unicode IDs; new providers retain the
            # ASCII naming rule shown in the UI. Never silently rename an ID.
            legacy_unicode = (name in existing_names and 0 < len(name) <= 64
                              and name[0].isalpha()
                              and all(c.isalnum() or c in "_-" for c in name))
            if (not _NAME.fullmatch(name) and not legacy_unicode) or name.lower() in {p.lower() for p in urls}:
                raise ConfigProblem("提供商标识无效或重复；请使用字母开头的字母、数字、下划线或短横线。")
            value = value.strip()
            try:
                parsed = urlsplit(value)
                valid = parsed.scheme in {"http", "https"} and parsed.hostname and not (
                    parsed.username or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in value)
                )
                _ = parsed.port
            except ValueError:
                valid = False
            if not valid:
                raise ConfigProblem(f"提供商 {name} 的 Base URL 必须是无凭据、查询参数或片段的 HTTP(S) 地址。")
            urls[name] = value
        # Templates are deep-merged at startup: omission cannot delete a provider.
        if set(merged.get("base_urls", {})) - set(urls):
            raise ConfigProblem("不能从此页面删除已保存的提供商；请保留已有端点及模型依赖。")
        models = _public_models(merged)
        changes = {}
        for role, update in (request.models or {}).items():
            if role not in models:
                raise ConfigProblem("模型角色已变化，请重新加载配置。", 409)
            changes[role] = {}
            for slot in ("primary", "fallback"):
                choice = getattr(update, slot)
                if choice is None:
                    continue
                if slot not in models[role]:
                    raise ConfigProblem("该角色尚未定义备用模型，请通过配置文件添加。")
                provider, model = choice.provider.strip(), choice.model.strip()
                if provider not in urls or not model or len(model) > 256 or any(ord(c) < 32 for c in model):
                    raise ConfigProblem(f"角色 {role} 的 {slot} 需要有效的提供商和模型 ID。")
                changes[role][slot] = {"provider": provider, "model": model}
        updated = _merge(local, {"base_urls": urls, "models": changes})
        # Retain all unrelated local options, including existing ignored legacy keys.
        path = directory / "models.local.yaml"
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".models-", suffix=".yaml", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                yaml.safe_dump(updated, stream, allow_unicode=True, sort_keys=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError:
            raise ConfigProblem("配置保存失败，原文件未被替换；请检查目录权限或文件占用。", 500) from None
        finally:
            if temporary and Path(temporary).exists():
                Path(temporary).unlink()
        _, persisted, new_revision = _read(directory)
        return _snapshot(persisted, new_revision, runtime, directory)
