"""Server-side cache of provider model catalogs fetched by explicit probes.

The catalog is a convenience layer only: it never contains secrets, is never
consulted by the runtime, and is only refreshed by the user's own explicit
"connectivity / fetch models" action. Entries survive page reloads so the
role routing table can offer real upstream model IDs without re-probing.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.RLock()
_FILE = "model_catalog.json"
_MAX_MODELS = 200
_MAX_PROVIDERS = 64


def _path(directory: Path) -> Path:
    return directory / _FILE


def _read_raw(directory: Path) -> dict:
    try:
        data = json.loads(_path(directory).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError):
        # A corrupt cache must never break the configuration page; treat it
        # as empty and let the next explicit fetch overwrite it.
        return {}
    entries = data.get("providers") if isinstance(data, dict) else None
    return entries if isinstance(entries, dict) else {}


def _clean_models(models) -> list[str]:
    seen: dict[str, None] = {}
    for item in models if isinstance(models, list) else []:
        if (
            isinstance(item, str)
            and 0 < len(item) <= 256
            and not any(ord(c) < 32 or ord(c) == 127 for c in item)
        ):
            seen.setdefault(item, None)
        if len(seen) >= _MAX_MODELS:
            break
    return list(seen)


def effective_base_urls(base_urls: dict[str, str]) -> dict[str, str]:
    """Apply the same EAS_LLM_BASE_URL_<PROVIDER> precedence the probe uses."""
    return {
        provider: os.getenv(f"EAS_LLM_BASE_URL_{provider.upper()}") or url
        for provider, url in base_urls.items()
    }


def load_catalog(directory: Path, base_urls: dict[str, str]) -> dict[str, dict]:
    """Return cached catalogs for currently configured providers only.

    ``stale`` marks entries fetched against a different endpoint than the one
    now in effect, so the UI can suggest re-fetching instead of trusting old IDs.
    """
    with _LOCK:
        raw = _read_raw(directory)
    effective = effective_base_urls(base_urls)
    result: dict[str, dict] = {}
    for provider, url in effective.items():
        entry = raw.get(provider)
        if not isinstance(entry, dict):
            continue
        models = _clean_models(entry.get("models"))
        if not models:
            continue
        fetched_at = entry.get("fetched_at")
        cached_url = entry.get("base_url")
        result[provider] = {
            "models": models,
            "fetched_at": fetched_at if isinstance(fetched_at, str) else None,
            "base_url": cached_url if isinstance(cached_url, str) else None,
            "stale": cached_url != url,
        }
    return result


def remember_catalog(
    directory: Path,
    provider: str,
    base_url: str,
    models,
    known_providers=None,
) -> dict:
    """Atomically persist one provider's fetched model IDs.

    Providers no longer present in the configuration are pruned on write.
    Filesystem failures propagate as ``OSError`` so the caller can downgrade
    them to a non-fatal notice without hiding the probe result itself.
    """
    cleaned = _clean_models(models)
    with _LOCK:
        raw = _read_raw(directory)
        if known_providers is not None:
            raw = {name: entry for name, entry in raw.items() if name in known_providers}
        entry = {
            "models": cleaned,
            "base_url": base_url,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        raw[provider] = entry
        if len(raw) > _MAX_PROVIDERS:
            ordered = sorted(
                raw.items(),
                key=lambda kv: str((kv[1] or {}).get("fetched_at") or ""),
                reverse=True,
            )
            raw = dict(ordered[:_MAX_PROVIDERS])
        _write(directory, {"version": 1, "providers": raw})
        return {**entry, "stale": False}


def forget_catalog(directory: Path, provider: str) -> bool:
    """Drop one provider's cached list (e.g. after the user changed endpoints)."""
    with _LOCK:
        raw = _read_raw(directory)
        if provider not in raw:
            return False
        del raw[provider]
        _write(directory, {"version": 1, "providers": raw})
        return True


def _write(directory: Path, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".catalog-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _path(directory))
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
