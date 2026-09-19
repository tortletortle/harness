"""Model catalog cache: disposable files, mocked upstream, no secrets persisted."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import model_catalog as catalog
from api import model_probe as probe
from api.configuration import read_configuration


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("ALPHA", "BETA"):
        monkeypatch.delenv("EAS_LLM_API_KEY_" + name, raising=False)
        monkeypatch.delenv("EAS_LLM_BASE_URL_" + name, raising=False)
    runtime = {
        "base_urls": {"alpha": "https://alpha.invalid/v1", "beta": "https://beta.invalid/v1"},
        "models": {"designer": {"primary": {"provider": "alpha", "model": "m1"}}},
    }
    (tmp_path / "models.yaml").write_text(yaml.safe_dump(runtime), encoding="utf-8")
    return SimpleNamespace(root=tmp_path, runtime=runtime)


def test_missing_or_corrupt_cache_reads_as_empty(env):
    assert catalog.load_catalog(env.root, env.runtime["base_urls"]) == {}
    (env.root / "model_catalog.json").write_text("{not json", encoding="utf-8")
    assert catalog.load_catalog(env.root, env.runtime["base_urls"]) == {}
    (env.root / "model_catalog.json").write_text(json.dumps({"providers": []}), encoding="utf-8")
    assert catalog.load_catalog(env.root, env.runtime["base_urls"]) == {}


def test_remember_filters_prunes_and_marks_stale(env):
    urls = env.runtime["base_urls"]
    entry = catalog.remember_catalog(
        env.root, "alpha", urls["alpha"],
        ["m1", "m1", "", "bad\x00id", "x" * 257, "m2"], known_providers=set(urls),
    )
    assert entry["models"] == ["m1", "m2"] and entry["stale"] is False
    assert entry["fetched_at"]
    # A provider that later disappears from the configuration is hidden on read
    # and pruned on the next write.
    catalog.remember_catalog(env.root, "ghost", "https://ghost.invalid/v1", ["g1"])
    assert "ghost" not in catalog.load_catalog(env.root, urls)
    catalog.remember_catalog(env.root, "beta", urls["beta"], ["b1"], known_providers=set(urls))
    raw = json.loads((env.root / "model_catalog.json").read_text(encoding="utf-8"))
    assert set(raw["providers"]) == {"alpha", "beta"}
    # Changing the effective endpoint flags the cached list as stale.
    loaded = catalog.load_catalog(env.root, {**urls, "alpha": "https://moved.invalid/v1"})
    assert loaded["alpha"]["stale"] is True and loaded["beta"]["stale"] is False
    assert catalog.forget_catalog(env.root, "alpha") is True
    assert catalog.forget_catalog(env.root, "alpha") is False
    assert "alpha" not in catalog.load_catalog(env.root, urls)


def test_environment_endpoint_override_participates_in_staleness(env, monkeypatch):
    urls = env.runtime["base_urls"]
    catalog.remember_catalog(env.root, "alpha", urls["alpha"], ["m1"])
    monkeypatch.setenv("EAS_LLM_BASE_URL_ALPHA", "https://override.invalid/v1")
    assert catalog.load_catalog(env.root, urls)["alpha"]["stale"] is True


def test_probe_route_persists_catalog_and_get_returns_it(env, monkeypatch):
    from api.auth import reset_token_cache
    from api.routes import misc_routes

    monkeypatch.setattr(misc_routes, "_config_dir", lambda: env.root)
    monkeypatch.setattr(misc_routes, "state", SimpleNamespace(config=env.runtime))
    monkeypatch.setattr(probe, "_GATE", asyncio.Lock())
    monkeypatch.setenv("EAS_API_TOKEN", "catalog-test-token")
    monkeypatch.setenv("EAS_LLM_API_KEY_ALPHA", "synthetic-secret-key")
    reset_token_cache()
    real_client = httpx.AsyncClient
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}, {"id": "m1"}]})

    monkeypatch.setattr(
        probe.httpx, "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    app = FastAPI()
    app.include_router(misc_routes.router)
    headers = {"Authorization": "Bearer catalog-test-token"}
    try:
        with TestClient(app) as client:
            assert client.get("/api/config/catalog").status_code == 401
            assert client.get("/api/config/catalog", headers=headers).json()["catalog"] == {}
            cfg = client.get("/api/config", headers=headers).json()["config"]
            assert cfg["catalog_version"] == 1
            response = client.post(
                "/api/config/probe", headers=headers,
                json={"provider": "alpha", "revision": cfg["revision"], "mode": "connectivity"},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["result"]["ok"] and body["catalog"]["models"] == ["m1", "m2"]
            assert len(calls) == 1 and calls[0].method == "GET"
            listing = client.get("/api/config/catalog", headers=headers).json()["catalog"]
            assert listing["alpha"]["models"] == ["m1", "m2"] and listing["alpha"]["stale"] is False
            assert "beta" not in listing
            persisted = (env.root / "model_catalog.json").read_text(encoding="utf-8")
            assert "synthetic-secret" not in persisted
            # Model tests never touch the catalog.
            calls.clear()
            response = client.post(
                "/api/config/probe", headers=headers,
                json={"provider": "alpha", "revision": cfg["revision"], "mode": "model",
                      "model": "m1", "acknowledge_cost": True},
            )
            assert response.status_code == 200 and response.json()["catalog"] is None
            assert client.delete("/api/config/catalog/nope", headers=headers).status_code == 404
            assert client.delete("/api/config/catalog/alpha", headers=headers).json()["removed"] is True
            assert client.get("/api/config/catalog", headers=headers).json()["catalog"] == {}
    finally:
        reset_token_cache()


def test_cache_write_failure_does_not_fail_probe(env, monkeypatch):
    from api.auth import reset_token_cache
    from api.routes import misc_routes

    monkeypatch.setattr(misc_routes, "_config_dir", lambda: env.root)
    monkeypatch.setattr(misc_routes, "state", SimpleNamespace(config=env.runtime))
    monkeypatch.setattr(probe, "_GATE", asyncio.Lock())
    monkeypatch.setenv("EAS_API_TOKEN", "catalog-test-token")
    reset_token_cache()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        probe.httpx, "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"id": "m1"}]})),
            **kwargs,
        ),
    )

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(misc_routes.model_catalog, "remember_catalog", boom)
    app = FastAPI()
    app.include_router(misc_routes.router)
    headers = {"Authorization": "Bearer catalog-test-token"}
    try:
        with TestClient(app) as client:
            revision = read_configuration(env.root, env.runtime)["revision"]
            response = client.post(
                "/api/config/probe", headers=headers,
                json={"provider": "alpha", "revision": revision, "mode": "connectivity"},
            )
            assert response.status_code == 200
            assert response.json()["result"]["ok"] and response.json()["catalog"] is None
    finally:
        reset_token_cache()
