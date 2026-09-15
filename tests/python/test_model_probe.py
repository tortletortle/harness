import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from api import model_probe as probe
from api.configuration import ConfigProblem, read_configuration


@pytest.fixture
def env(tmp_path, monkeypatch):
    runtime = {"base_urls": {"alpha": "https://example.invalid/v1"}, "models": {}}
    (tmp_path / "models.yaml").write_text(yaml.safe_dump(runtime), encoding="utf-8")
    monkeypatch.setenv("EAS_LLM_API_KEY_ALPHA", "synthetic-secret-key")
    monkeypatch.delenv("EAS_LLM_BASE_URL_ALPHA", raising=False)
    monkeypatch.setattr(probe, "_GATE", asyncio.Lock())
    real_client = httpx.AsyncClient
    calls = []

    def transport(handler):
        def capture(request):
            calls.append(request)
            return handler(request)

        def client(**kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
            return real_client(transport=httpx.MockTransport(capture), **kwargs)

        monkeypatch.setattr(probe.httpx, "AsyncClient", client)

    def request(**kwargs):
        return probe.ProbeRequest(
            provider="alpha",
            revision=read_configuration(tmp_path, runtime)["revision"],
            mode="connectivity",
            **kwargs,
        )

    return SimpleNamespace(
        root=tmp_path,
        runtime=runtime,
        transport=transport,
        calls=calls,
        request=request,
    )


@pytest.mark.asyncio
async def test_catalog_does_not_invoke_inference(env):
    env.transport(
        lambda _: httpx.Response(
            200, json={"data": [{"id": "m1"}, {"id": "m1"}, {"id": "m2"}]}
        )
    )
    result = await probe.probe_configuration(env.root, env.runtime, env.request())
    assert result["ok"] and result["models"] == ["m1", "m2"]
    assert len(env.calls) == 1 and env.calls[0].method == "GET"
    assert str(env.calls[0].url) == "https://example.invalid/v1/models"
    assert env.calls[0].headers["authorization"] == "Bearer synthetic-secret-key"
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_explicit_model_test_is_small_and_does_not_echo_response(env):
    env.transport(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "synthetic-secret-key hidden upstream text"
                        }
                    }
                ]
            },
        )
    )
    req = env.request().model_copy(
        update={"mode": "model", "model": "custom/m", "acknowledge_cost": True}
    )
    result = await probe.probe_configuration(env.root, env.runtime, req)
    assert result["ok"]
    payload = json.loads(env.calls[0].content)
    assert payload == {
        "model": "custom/m",
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 16,
        "stream": False,
    }
    assert "hidden upstream" not in json.dumps(
        result
    ) and "synthetic-secret" not in json.dumps(result)
    assert len(env.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (404, "not_found"),
        (429, "rate_or_quota_limited"),
        (400, "request_rejected"),
        (500, "upstream_error"),
        (302, "redirect_blocked"),
    ],
)
async def test_failures_are_classified_without_echo_or_redirect(env, status, code):
    env.transport(
        lambda _: httpx.Response(
            status,
            headers={"location": "https://other.invalid"},
            json={"error": "synthetic-secret-key"},
        )
    )
    result = await probe.probe_configuration(env.root, env.runtime, env.request())
    assert not result["ok"] and result["code"] == code and result["reachable"]
    assert result["http_status"] == status and len(env.calls) == 1
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,code",
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectError, "connection_failed"),
        (httpx.RemoteProtocolError, "network_error"),
    ],
)
async def test_network_failure(env, error, code):
    def handler(_):
        raise error("synthetic-secret-key")

    env.transport(handler)
    result = await probe.probe_configuration(env.root, env.runtime, env.request())
    assert result["code"] == code and not result["ok"]
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "model", "model": "m"},
        {"revision": "stale"},
        {"provider": "missing"},
        {"mode": "model", "model": "", "acknowledge_cost": True},
    ],
)
async def test_invalid_or_unconfirmed_request_never_connects(env, changes):
    env.transport(lambda _: pytest.fail("must not connect"))
    with pytest.raises(ConfigProblem):
        await probe.probe_configuration(
            env.root, env.runtime, env.request().model_copy(update=changes)
        )
    assert not env.calls


def test_browser_cannot_supply_destination_or_key(env):
    with pytest.raises(ValidationError):
        probe.ProbeRequest(
            **env.request().model_dump(), base_url="http://private.invalid", api_key="x"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"<html>not an API</html>",
        b"[]",
        b'{"error":"secret"}',
        b"{}",
        b"x" * (257 * 1024),
    ],
    ids=["html", "list", "error", "missing-data", "oversize"],
)
async def test_invalid_or_large_response_is_not_success(env, body):
    env.transport(lambda _: httpx.Response(200, content=body))
    result = await probe.probe_configuration(env.root, env.runtime, env.request())
    assert not result["ok"] and result["reachable"]


@pytest.mark.asyncio
async def test_empty_inference_is_inconclusive(env):
    env.transport(
        lambda _: httpx.Response(
            200,
            json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
        )
    )
    req = env.request().model_copy(
        update={"mode": "model", "model": "reasoning", "acknowledge_cost": True}
    )
    result = await probe.probe_configuration(env.root, env.runtime, req)
    assert result["code"] == "empty_response" and not result["ok"]


@pytest.mark.asyncio
async def test_environment_destination_override_and_no_file_changes(env, monkeypatch):
    monkeypatch.setenv("EAS_LLM_BASE_URL_ALPHA", "http://localhost:12345/v1")
    before = (env.root / "models.yaml").read_bytes()
    env.transport(lambda _: httpx.Response(200, json={"data": []}))
    await probe.probe_configuration(env.root, env.runtime, env.request())
    assert str(env.calls[0].url) == "http://localhost:12345/v1/models"
    assert (env.root / "models.yaml").read_bytes() == before
    assert not (env.root / "models.local.yaml").exists()


@pytest.mark.asyncio
async def test_concurrent_probe_is_refused(env):
    await probe._GATE.acquire()
    try:
        with pytest.raises(ConfigProblem) as exc:
            await probe.probe_configuration(env.root, env.runtime, env.request())
        assert exc.value.status == 429
    finally:
        probe._GATE.release()


def test_http_requires_auth_and_confirmation(env, monkeypatch):
    from api.routes import misc_routes
    from api.auth import reset_token_cache

    monkeypatch.setenv("EAS_API_TOKEN", "probe-test-token")
    monkeypatch.setattr(misc_routes, "_config_dir", lambda: env.root)
    monkeypatch.setattr(misc_routes, "state", SimpleNamespace(config=env.runtime))
    env.transport(lambda _: httpx.Response(200, json={"data": []}))
    reset_token_cache()
    app = FastAPI()
    app.include_router(misc_routes.router)
    try:
        with TestClient(app) as client:
            payload = env.request().model_dump()
            assert client.post("/api/config/probe", json=payload).status_code == 401
            headers = {"Authorization": "Bearer probe-test-token"}
            assert client.post(
                "/api/config/probe", json=payload, headers=headers
            ).json()["result"]["ok"]
            payload.update(mode="model", model="x")
            assert (
                client.post(
                    "/api/config/probe", json=payload, headers=headers
                ).status_code
                == 400
            )
            assert len(env.calls) == 1
    finally:
        reset_token_cache()


@pytest.mark.asyncio
async def test_catalog_bounds_and_echoed_key_are_filtered(env):
    rows = [
        {"id": "synthetic-secret-key"},
        {"id": "bad\nname"},
        {"id": "x" * 257},
        {"id": "bad\x7fname"},
    ] + [{"id": f"m{i}"} for i in range(210)]
    env.transport(lambda _: httpx.Response(200, json={"data": rows}))
    result = await probe.probe_configuration(env.root, env.runtime, env.request())
    assert len(result["models"]) == 200 and result["models"][0] == "m0"
    assert "synthetic-secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_read_timeout_preserves_http_reachability(env):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"{"
            raise httpx.ReadTimeout("synthetic-secret-key")

    env.transport(lambda _: httpx.Response(200, stream=Stream()))
    result = await probe.probe_configuration(env.root, env.runtime, env.request())
    assert (
        result["code"] == "timeout"
        and result["reachable"]
        and result["http_status"] == 200
    )
    assert not probe._GATE.locked()


@pytest.mark.asyncio
async def test_legacy_saved_or_runtime_keys_are_never_loaded(env, monkeypatch):
    monkeypatch.delenv("EAS_LLM_API_KEY_ALPHA")
    (env.root / "models.local.yaml").write_text(
        yaml.safe_dump({"api_keys": {"alpha": "legacy-key"}}), encoding="utf-8"
    )
    env.runtime["api_keys"] = {"alpha": "stale-key"}
    env.transport(lambda _: httpx.Response(200, json={"data": []}))
    await probe.probe_configuration(env.root, env.runtime, env.request())
    assert "authorization" not in env.calls[0].headers


@pytest.mark.asyncio
async def test_saved_unicode_provider_uses_exact_endpoint_and_env_key(env, monkeypatch):
    env.runtime['base_urls']['国模'] = 'https://unicode.invalid/v1'
    (env.root / 'models.yaml').write_text(yaml.safe_dump(env.runtime, allow_unicode=True), encoding='utf-8')
    monkeypatch.setenv('EAS_LLM_API_KEY_国模', 'synthetic-unicode-key')
    env.transport(lambda _: httpx.Response(200, json={'data': [{'id': 'model-a'}]}))
    req = probe.ProbeRequest(provider='国模', revision=read_configuration(env.root,env.runtime)['revision'], mode='connectivity')
    result = await probe.probe_configuration(env.root,env.runtime,req)
    assert result['ok'] and result['models'] == ['model-a']
    assert len(env.calls) == 1 and env.calls[0].method == 'GET'
    assert str(env.calls[0].url) == 'https://unicode.invalid/v1/models'
    assert env.calls[0].headers['authorization'] == 'Bearer synthetic-unicode-key'
    assert 'synthetic-unicode-key' not in json.dumps(result)


@pytest.mark.asyncio
async def test_unsaved_unicode_provider_never_connects(env):
    env.transport(lambda _: pytest.fail('must not connect'))
    req=probe.ProbeRequest(provider='未保存', revision=env.request().revision, mode='connectivity')
    with pytest.raises(ConfigProblem):
        await probe.probe_configuration(env.root,env.runtime,req)
    assert not env.calls


@pytest.mark.parametrize('name', ['国 模','国模\n','国模\x00','国模/路径','国模=key','国模\u202e','9国模','_国模','国'*65], ids=['space','newline','nul','slash','equals','bidi','digit-first','underscore-first','too-long'])
def test_unsafe_unicode_identifiers_are_rejected(name):
    with pytest.raises(ValidationError):
        probe.ProbeRequest(provider=name,revision='r',mode='connectivity')
