"""Non-secret configuration contract; every file and HTTP app is disposable."""
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.configuration import ConfigUpdateRequest, ConfigProblem, read_configuration, save_configuration


@pytest.fixture
def config(tmp_path, monkeypatch):
    for name in ('ALPHA', 'BETA'):
        monkeypatch.delenv('EAS_LLM_API_KEY_' + name, raising=False)
        monkeypatch.delenv('EAS_LLM_BASE_URL_' + name, raising=False)
    runtime = {'base_urls': {'alpha': 'https://alpha.invalid/v1'}, 'models': {
        'designer': {'primary': {'provider': 'alpha', 'model': 'custom-main', 'temperature': .3, 'max_tokens': 4096},
                     'fallback': {'provider': 'alpha', 'model': 'custom-backup', 'max_tokens': 2048}},
        'special_voice': {'primary': {'provider': 'alpha', 'model': 'voice/custom'}},
        'executor': {'max_concurrency': 3},
    }, 'api_keys': {'alpha': 'never-expose-this'}, 'parallel_graphs': True}
    (tmp_path / 'models.yaml').write_text(yaml.safe_dump(runtime), encoding='utf-8')
    return tmp_path, runtime


def request(config, **updates):
    root, runtime = config
    view = read_configuration(root, runtime)
    payload = {'base_urls': view['base_urls'], 'revision': view['revision'], 'models': {
        'designer': {'primary': {'provider': 'alpha', 'model': 'new/custom'},
                     'fallback': {'provider': 'alpha', 'model': 'fallback/custom'}}}}
    payload.update(updates)
    return ConfigUpdateRequest(**payload)


def test_read_models_not_agents_and_never_return_secrets(config, monkeypatch):
    root, runtime = config
    monkeypatch.setenv('EAS_LLM_API_KEY_ALPHA', 'another-secret')
    cfg = read_configuration(root, runtime)
    assert cfg['models']['special_voice']['primary']['model'] == 'voice/custom'
    assert cfg['api_keys_configured'] == {'alpha': True}
    assert 'secret' not in str(cfg) and 'never-expose' not in str(cfg)
    assert not cfg['requires_restart']
    assert cfg['schema_version'] == 2


def test_save_both_models_preserves_unknown_options_and_runtime(config):
    root, runtime = config
    before = copy.deepcopy(runtime)
    (root / 'models.local.yaml').write_text(yaml.safe_dump({'custom': {'enabled': True}, 'models': {'designer': {'other': 5}}}), encoding='utf-8')
    saved = save_configuration(root, runtime, request(config))
    assert saved['models']['designer']['primary']['model'] == 'new/custom'
    assert saved['models']['designer']['fallback']['model'] == 'fallback/custom'
    assert saved['models']['designer']['primary']['temperature'] == .3
    assert saved['models']['designer']['fallback']['max_tokens'] == 2048
    assert saved['requires_restart']
    assert runtime == before
    disk = yaml.safe_load((root / 'models.local.yaml').read_text(encoding='utf-8'))
    assert disk['custom'] == {'enabled': True} and disk['models']['designer']['other'] == 5
    assert 'api_keys' not in disk
    assert read_configuration(root, runtime) == saved


def test_stale_revision_is_rejected_without_overwrite(config):
    root, runtime = config
    stale = request(config)
    save_configuration(root, runtime, stale)
    before = (root / 'models.local.yaml').read_bytes()
    with pytest.raises(ConfigProblem) as exc:
        save_configuration(root, runtime, stale)
    assert exc.value.status == 409
    assert (root / 'models.local.yaml').read_bytes() == before


@pytest.mark.parametrize('updates', [
    {'api_keys': {'alpha': 'not-to-be-written'}}, {'revision': None},
    {'base_urls': {}}, {'base_urls': {'Alpha': 'https://x.invalid', 'alpha': 'https://x.invalid'}},
    {'base_urls': {'invalid/name': 'https://x.invalid'}},
    {'models': {'designer': {'primary': {'provider': 'missing', 'model': 'm'}}}},
    {'models': {'designer': {'primary': {'provider': 'alpha', 'model': ' '}}}},
    {'models': {'missing_role': {'primary': {'provider': 'alpha', 'model': 'm'}}}},
    {'models': {'special_voice': {'primary': {'provider': 'alpha', 'model': 'm'}, 'fallback': {'provider': 'alpha', 'model': 'm'}}}},
])
def test_invalid_updates_leave_files_untouched(config, updates):
    root, runtime = config
    template = (root / 'models.yaml').read_bytes()
    with pytest.raises(ConfigProblem):
        save_configuration(root, runtime, request(config, **updates))
    assert not (root / 'models.local.yaml').exists()
    assert (root / 'models.yaml').read_bytes() == template


@pytest.mark.parametrize('url', ['ftp://x.invalid', 'https://user:secret@x.invalid', 'https://x.invalid?key=secret',
                                'https://x.invalid#secret', 'not a url', 'https://x.invalid:bad', 'https://[bad', 'https://x.invalid/with space'])
def test_invalid_urls(config, url):
    root, runtime = config
    with pytest.raises(ConfigProblem):
        save_configuration(root, runtime, request(config, base_urls={'alpha': url}))
    assert not (root / 'models.local.yaml').exists()


def test_environment_endpoint_override_is_visible(config, monkeypatch):
    root, runtime = config
    monkeypatch.setenv('EAS_LLM_BASE_URL_ALPHA', 'https://override.invalid/v1')
    runtime['base_urls']['alpha'] = 'https://override.invalid/v1'
    cfg = read_configuration(root, runtime)
    assert cfg['base_urls']['alpha'] == 'https://alpha.invalid/v1'
    assert cfg['environment_overrides'] == ['alpha']
    assert not cfg['requires_restart']


def test_atomic_replace_failure_preserves_original(config, monkeypatch):
    from api import configuration
    root, runtime = config
    local = root / 'models.local.yaml'
    local.write_text('custom: keep\n', encoding='utf-8')
    before = local.read_bytes()
    monkeypatch.setattr(configuration.os, 'replace', Mock(side_effect=PermissionError('private path')))
    with pytest.raises(ConfigProblem) as exc:
        save_configuration(root, runtime, request(config))
    assert 'private path' not in str(exc.value)
    assert local.read_bytes() == before
    assert not list(root.glob('.models-*'))


def test_malformed_file_is_not_overwritten_or_echoed(config):
    root, runtime = config
    local = root / 'models.local.yaml'
    local.write_text('private-secret: [broken', encoding='utf-8')
    with pytest.raises(ConfigProblem) as exc:
        read_configuration(root, runtime)
    assert 'private-secret' not in str(exc.value)
    assert local.read_text() == 'private-secret: [broken'


def test_add_provider_and_reload(config):
    root, runtime = config
    save_configuration(root, runtime, request(config, base_urls={'alpha': 'https://alpha.invalid/v1', 'beta': 'http://localhost:11434/v1'}))
    assert read_configuration(root, runtime)['base_urls']['beta'] == 'http://localhost:11434/v1'


def test_http_roundtrip_auth_and_revision(config, monkeypatch):
    from api.routes import misc_routes
    from api.auth import reset_token_cache
    root, runtime = config
    monkeypatch.setattr(misc_routes, '_config_dir', lambda: root)
    monkeypatch.setattr(misc_routes, 'state', SimpleNamespace(config=runtime))
    monkeypatch.setenv('EAS_API_TOKEN', 'model-config-test-token')
    reset_token_cache()
    app = FastAPI(); app.include_router(misc_routes.router)
    try:
        with TestClient(app) as client:
            assert client.get('/api/config').status_code == 401
            headers = {'Authorization': 'Bearer model-config-test-token'}
            cfg = client.get('/api/config', headers=headers).json()['config']
            assert cfg['models']['designer']['primary']['model'] == 'custom-main'
            payload = request(config).model_dump()
            response = client.post('/api/config', headers=headers, json=payload)
            assert response.status_code == 200
            assert response.json()['config']['requires_restart']
            assert client.post('/api/config', headers=headers, json=payload).status_code == 409
    finally:
        reset_token_cache()


def test_unversioned_legacy_save_is_rejected(config):
    root, runtime = config
    with pytest.raises(ConfigProblem) as exc:
        save_configuration(root, runtime, ConfigUpdateRequest(base_urls=runtime['base_urls']))
    assert exc.value.status == 409
    assert not (root / 'models.local.yaml').exists()


def test_missing_model_fields_read_as_repairable_empty_fields(config):
    root, runtime = config
    (root / 'models.local.yaml').write_text('models:\n  designer:\n    primary:\n      model: null\n', encoding='utf-8')
    cfg = read_configuration(root, runtime)
    assert cfg['models']['designer']['primary']['model'] == ''



def test_save_preserves_existing_unicode_provider_and_dependencies(config):
    root,runtime=config
    runtime['base_urls']['国模']='https://unicode.invalid/v1'
    runtime['models']['special_voice']['primary']['provider']='国模'
    (root/'models.yaml').write_text(yaml.safe_dump(runtime,allow_unicode=True),encoding='utf-8')
    before=(root/'models.yaml').read_bytes()
    result=save_configuration(root,runtime,request(config))
    assert result['base_urls']['国模']=='https://unicode.invalid/v1'
    assert result['models']['special_voice']['primary']['provider']=='国模'
    assert (root/'models.yaml').read_bytes()==before
    assert result['models']['designer']['primary']['model']=='new/custom'


def test_new_unicode_provider_still_uses_existing_creation_policy(config):
    root,runtime=config
    with pytest.raises(ConfigProblem):
        save_configuration(root,runtime,request(config,base_urls={**runtime['base_urls'],'新国模':'https://unicode.invalid/v1'}))
    assert not (root/'models.local.yaml').exists()


@pytest.mark.parametrize('name',['国 模','国模\x00','国模/路径','国模\u202e'],ids=['space','nul','slash','bidi'])
def test_unsafe_saved_names_are_not_grandfathered(config,name):
    root,runtime=config
    runtime['base_urls'][name]='https://unicode.invalid/v1'
    (root/'models.yaml').write_text(yaml.safe_dump(runtime,allow_unicode=True),encoding='utf-8')
    with pytest.raises(ConfigProblem):
        save_configuration(root,runtime,request(config))
    assert not (root/'models.local.yaml').exists()
