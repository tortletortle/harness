"""Encrypted provider-key storage: DPAPI roundtrip, no plaintext, HTTP contract.

The crypto half is Windows-only (DPAPI). On other platforms saving must fail
closed and the service must keep working with environment variables only.
"""
import json
import os
import sys
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import secret_store
from api.auth import reset_token_cache
from api.configuration import (
    ConfigProblem,
    KeyUpdateRequest,
    read_configuration,
    save_api_key,
)

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="DPAPI is Windows-only")

RUNTIME = {
    "base_urls": {"alpha": "https://alpha.invalid/v1", "beta": "https://beta.invalid/v1"},
    "models": {"designer": {"primary": {"provider": "alpha", "model": "m1"}}},
}


@pytest.fixture
def config(tmp_path, monkeypatch):
    for name in ("ALPHA", "BETA"):
        monkeypatch.delenv("EAS_LLM_API_KEY_" + name, raising=False)
        monkeypatch.delenv("EAS_LLM_BASE_URL_" + name, raising=False)
    (tmp_path / "models.yaml").write_text(
        yaml.safe_dump(RUNTIME, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


@windows_only
def test_saved_key_is_encrypted_and_roundtrips(config):
    secret = "sk-do-not-store-plaintext-42"
    secret_store.save_api_keys(config, {"alpha": secret})
    raw = secret_store.keys_path(config).read_text(encoding="utf-8")
    assert secret not in raw
    assert secret.encode().hex() not in raw.lower()
    assert set(json.loads(raw)) == {"format", "values"}
    assert secret_store.load_api_keys(config) == {"alpha": secret}


@windows_only
def test_missing_file_reads_as_empty_and_clear_removes_value(config):
    assert secret_store.load_api_keys(config) == {}
    secret_store.save_api_keys(config, {"alpha": "a", "beta": "b"})
    secret_store.save_api_keys(config, {"beta": "b2"})
    assert secret_store.load_api_keys(config) == {"alpha": "a", "beta": "b2"}
    secret_store.save_api_keys(config, {"alpha": ""})
    assert secret_store.load_api_keys(config) == {"beta": "b2"}


@windows_only
def test_environment_variable_wins_over_persisted_key(config, monkeypatch):
    secret_store.save_api_keys(config, {"alpha": "stored"})
    assert secret_store.effective_key(config, "alpha") == "stored"
    monkeypatch.setenv("EAS_LLM_API_KEY_ALPHA", "from-env")
    assert secret_store.effective_key(config, "alpha") == "from-env"


def test_corrupt_store_is_reported_without_echoing_content(config):
    secret_store.keys_path(config).write_text('{"format": 1, "values": "broken"}', encoding="utf-8")
    with pytest.raises(secret_store.SecretStoreError) as exc:
        secret_store.load_api_keys(config)
    assert "broken" not in str(exc.value)


def test_unreadable_store_never_breaks_the_snapshot(config):
    """A bad store must not take the configuration page down."""
    if WINDOWS:
        secret_store.keys_path(config).write_text("{not json", encoding="utf-8")
    cfg = read_configuration(config, RUNTIME)
    assert cfg["api_keys_configured"] == {"alpha": False, "beta": False}
    assert cfg["api_keys_persisted"] == {"alpha": False, "beta": False}


@windows_only
def test_snapshot_reports_presence_not_value(config):
    secret = "sk-presence-only"
    save_api_key(config, KeyUpdateRequest(provider="alpha", value=secret))
    cfg = read_configuration(config, RUNTIME)
    assert cfg["api_keys_configured"]["alpha"] is True
    assert cfg["api_keys_persisted"]["alpha"] is True
    assert cfg["api_keys_configured"]["beta"] is False
    assert secret not in json.dumps(cfg, ensure_ascii=False)


@windows_only
def test_environment_variable_key_is_not_marked_persisted(config, monkeypatch):
    save_api_key(config, KeyUpdateRequest(provider="alpha", value="stored"))
    monkeypatch.setenv("EAS_LLM_API_KEY_ALPHA", "from-env")
    cfg = read_configuration(config, RUNTIME)
    assert cfg["api_keys_configured"]["alpha"] is True
    assert cfg["api_keys_persisted"]["alpha"] is False


def test_unknown_provider_is_rejected_without_writing(config):
    with pytest.raises(ConfigProblem) as exc:
        save_api_key(config, KeyUpdateRequest(provider="ghost", value="x"))
    assert exc.value.status == 400
    assert not secret_store.keys_path(config).exists()


@windows_only
def test_runtime_config_loader_injects_persisted_key(config, monkeypatch):
    """The startup loader must pick up an encrypted key with no environment variable."""
    from api import shared as shared_mod

    secret_store.save_api_keys(config, {"alpha": "sk-from-store"})
    monkeypatch.delenv("EAS_LLM_API_KEY_ALPHA", raising=False)
    loaded = shared_mod._load_models_config(config)
    assert loaded["api_keys"].get("alpha") == "sk-from-store"


@windows_only
def test_environment_variable_beats_persisted_key_in_loader(config, monkeypatch):
    from api import shared as shared_mod

    secret_store.save_api_keys(config, {"alpha": "sk-from-store"})
    monkeypatch.setenv("EAS_LLM_API_KEY_ALPHA", "sk-from-env")
    loaded = shared_mod._load_models_config(config)
    assert loaded["api_keys"]["alpha"] == "sk-from-env"


def test_http_key_endpoint_auth_and_contract(config, monkeypatch):
    from api.routes import misc_routes

    runtime = json.loads(json.dumps(RUNTIME))
    monkeypatch.setattr(misc_routes, "_config_dir", lambda: config)
    monkeypatch.setattr(misc_routes, "_dotenv_path", lambda: config / ".env")
    from llm_client import LLMClient
    monkeypatch.setattr(
        misc_routes,
        "state",
        type("S", (), {"config": runtime, "llm_client": LLMClient()})(),
    )
    monkeypatch.setenv("EAS_API_TOKEN", "secret-store-test-token")
    reset_token_cache()
    app = FastAPI()
    app.include_router(misc_routes.router)
    try:
        with TestClient(app) as client:
            body = {"provider": "alpha", "value": "sk-http-secret"}
            assert client.post("/api/config/key", json=body).status_code == 401
            headers = {"Authorization": "Bearer secret-store-test-token"}
            if not WINDOWS:
                response = client.post("/api/config/key", headers=headers, json=body)
                assert response.status_code == 500
                assert "sk-http-secret" not in response.text
                return
            response = client.post("/api/config/key", headers=headers, json=body)
            assert response.status_code == 200
            payload = response.json()["key"]
            assert payload["configured"] is True
            assert payload["applied"] is True
            assert "sk-http-secret" not in response.text
            assert misc_routes.state.llm_client.providers["alpha"].api_key == "sk-http-secret"
            cfg = client.get("/api/config", headers=headers).json()["config"]
            assert cfg["api_keys_configured"]["alpha"] is True
            assert "sk-http-secret" not in json.dumps(cfg)
            # Unknown provider and over-long values are rejected, not stored.
            assert client.post(
                "/api/config/key", headers=headers,
                json={"provider": "ghost", "value": "x"},
            ).status_code == 400
            assert client.post(
                "/api/config/key", headers=headers,
                json={"provider": "alpha", "value": "x" * 5000},
            ).status_code == 422
            cleared = client.post(
                "/api/config/key", headers=headers,
                json={"provider": "alpha", "value": ""},
            )
            assert cleared.status_code == 200
            assert cleared.json()["key"]["cleared"] is True
            cfg = client.get("/api/config", headers=headers).json()["config"]
            assert cfg["api_keys_configured"]["alpha"] is False
    finally:
        reset_token_cache()


def test_retire_dotenv_assignment_comments_only_the_matching_line(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EAS_LLM_API_KEY_OTHER=keep-me\n"
        "EAS_LLM_API_KEY_ALPHA=replace-me\n"
        "EAS_LLM_BASE_URL_ALPHA=https://alpha.invalid\n",
        encoding="utf-8",
    )
    assert secret_store.retire_dotenv_assignment(env_file, "alpha") is True
    text = env_file.read_text(encoding="utf-8")
    assert "EAS_LLM_API_KEY_OTHER=keep-me" in text
    assert "EAS_LLM_BASE_URL_ALPHA=https://alpha.invalid" in text
    assert text.splitlines()[1].startswith("# retired-by-config-ui ")
    assert "EAS_LLM_API_KEY_ALPHA=replace-me" in text.splitlines()[1]
    assert secret_store.retire_dotenv_assignment(env_file, "alpha") is False
    assert secret_store.retire_dotenv_url(env_file, "alpha") is True
    text = env_file.read_text(encoding="utf-8")
    assert any(line.startswith("# retired-by-config-ui ") and "EAS_LLM_BASE_URL_ALPHA=" in line for line in text.splitlines())
    assert "EAS_LLM_API_KEY_OTHER=keep-me" in text


@windows_only
def test_http_key_save_releases_env_and_applies_live_provider(config, monkeypatch):
    from api.routes import misc_routes
    from llm_client import LLMClient, OpenAICompatibleProvider

    runtime = json.loads(json.dumps(RUNTIME))
    client_obj = LLMClient()
    client_obj.register_provider(
        OpenAICompatibleProvider(
            name="alpha",
            base_url="https://alpha.invalid/v1",
            api_key="sk-from-env",
        )
    )
    monkeypatch.setattr(misc_routes, "_config_dir", lambda: config)
    monkeypatch.setattr(misc_routes, "_dotenv_path", lambda: config / ".env")
    monkeypatch.setattr(
        misc_routes, "state", type("S", (), {"config": runtime, "llm_client": client_obj})()
    )
    monkeypatch.setenv("EAS_API_TOKEN", "secret-store-test-token")
    monkeypatch.setenv("EAS_LLM_API_KEY_ALPHA", "sk-from-env")
    (config / ".env").write_text("EAS_LLM_API_KEY_ALPHA=sk-from-env\n", encoding="utf-8")
    reset_token_cache()
    app = FastAPI()
    app.include_router(misc_routes.router)
    try:
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer secret-store-test-token"}
            response = client.post(
                "/api/config/key",
                headers=headers,
                json={"provider": "alpha", "value": "sk-from-ui"},
            )
            assert response.status_code == 200
            body = response.json()["key"]
            assert body["applied"] is True
            assert body["env_released"] is True
            assert body["environment_override"] is False
            assert "sk-from-ui" not in response.text
            assert "sk-from-env" not in response.text
            assert os.getenv("EAS_LLM_API_KEY_ALPHA") is None
            assert client_obj.providers["alpha"].api_key == "sk-from-ui"
            commented = (config / ".env").read_text(encoding="utf-8")
            assert commented.startswith("# retired-by-config-ui ")
    finally:
        reset_token_cache()
