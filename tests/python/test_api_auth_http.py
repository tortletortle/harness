"""Real HTTP auth/CORS tests with isolated source, config, data, logs and port.

The server resolves some configuration relative to __file__, not cwd. Copy only
Python source into a private root: changing EAS_WORKSPACE_ROOT alone is unsafe.
No original database, local config, token, plugin or inherited provider is used.
"""

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest

TOKEN = secrets.token_urlsafe(24)
PYTHON_DIR = Path(__file__).resolve().parents[2] / "python"
OFFLINE_CONFIG = {
    "models": {}, "base_urls": {}, "api_keys": {}, "mcp_servers": {},
    "skills": {"enabled": False}, "notify": {"enabled": False},
}
# Bind port 0 in the child and keep that very socket through uvicorn startup.
# Unlike selecting then releasing a free port, this has no port-reuse race.
_BOOTSTRAP = """
import json, os, socket
from pathlib import Path
import uvicorn
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.bind(('127.0.0.1', 0))
    Path('server-endpoint.json').write_text(
        json.dumps({'port': sock.getsockname()[1], 'pid': os.getpid()}),
        encoding='utf-8')
    server = uvicorn.Server(uvicorn.Config('api.server:app', log_level='warning'))
    server.run(sockets=[sock])
finally:
    sock.close()
"""


def _source_only(directory, names):
    excluded = []
    for name in names:
        path = Path(directory) / name
        if (path.is_symlink() or name.startswith(".")
                or name in {"data", "logs", "__pycache__", "venv", "env"}
                or (path.is_file() and path.suffix != ".py")):
            excluded.append(name)
    return excluded


def _prepare_root(root, python_dir):
    shutil.copytree(python_dir, root / "python", ignore=_source_only)
    for directory in ("config", "data", "logs", "external_tools"):
        (root / directory).mkdir()
    # JSON is valid YAML. Do not copy even the source configuration template:
    # a developer may have enabled MCP processes or notifications there.
    (root / "config/models.yaml").write_text(
        json.dumps(OFFLINE_CONFIG), encoding="utf-8")
    return root


def _child_env(root, inherited=None):
    inherited = os.environ if inherited is None else inherited
    env = {
        key: value for key, value in inherited.items()
        if not key.upper().startswith(("EAS_", "RUN_REAL_"))
        and not key.upper().endswith(("_API_KEY", "_TOKEN"))
        and key.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}
    }
    env.update(
        EAS_API_TOKEN=TOKEN, EAS_WORKSPACE_ROOT=str(root),
        EAS_TOOLS_DIR=str(root / "external_tools"),
        PYTHONPATH=str(root / "python"), PYTHONIOENCODING="utf-8",
        PYTHONUNBUFFERED="1", RUN_REAL_LLM_EVAL="0", RUN_REAL_GH="0",
        DOTENV_LOADED="1",
    )
    return env


def _lower(headers):
    """HTTP header names are case-insensitive."""
    return {k.lower(): v for k, v in headers.items()}


def _req(server, path, token=None, header_name="Authorization", method="GET",
         origin=None, timeout=15):
    r = urllib.request.Request(server["base_url"] + path, method=method)
    if token:
        value = f"Bearer {token}" if header_name == "Authorization" else token
        r.add_header(header_name, value)
    if origin:
        r.add_header("Origin", origin)
    # Local auth tokens must never be forwarded to an inherited HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), _lower(resp.headers)
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read().decode("utf-8", "replace"), _lower(e.headers)
    except (OSError, urllib.error.URLError) as e:
        return 0, f"ERR {e}", {}


def _stop_process(proc):
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


@contextmanager
def _running_server(root, startup_timeout=60):
    log_path = root / "verify_http_server.log"
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-c", _BOOTSTRAP], stdout=log,
            stderr=subprocess.STDOUT, env=_child_env(root), cwd=str(root),
        )
        try:
            deadline = time.monotonic() + startup_timeout
            endpoint = root / "server-endpoint.json"
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(f"HTTP test server exited; see {log_path}")
                try:
                    info = json.loads(endpoint.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError):
                    time.sleep(0.05)
                    continue
                if info["pid"] != proc.pid:
                    raise RuntimeError("HTTP endpoint belongs to a different process")
                handle = {"base_url": f"http://127.0.0.1:{info['port']}",
                          "root": root, "pid": proc.pid}
                code, _, _ = _req(handle, "/api/status", token=TOKEN, timeout=0.5)
                if code == 200 and proc.poll() is None:
                    yield handle
                    return
                time.sleep(0.05)
            raise RuntimeError(f"HTTP test server startup timed out; see {log_path}")
        finally:
            _stop_process(proc)


@pytest.fixture(scope="module")
def server_root(tmp_path_factory):
    return _prepare_root(tmp_path_factory.mktemp("auth_http"), PYTHON_DIR)


@pytest.fixture(scope="module")
def server(server_root):
    with _running_server(server_root) as handle:
        yield handle


def test_health_probe_no_auth_required(server):
    code, body, _ = _req(server, "/")
    assert code == 200


def test_no_token_returns_401(server):
    code, body, hdrs = _req(server, "/api/status")
    assert code == 401
    assert "bearer" in hdrs.get("www-authenticate", "").lower()


def test_wrong_token_returns_401(server):
    code, _, _ = _req(server, "/api/status", token="wrong-token")
    assert code == 401


def test_correct_bearer_token_returns_200(server):
    code, body, _ = _req(server, "/api/status", token=TOKEN)
    assert code == 200
    assert '"status"' in body


def test_x_api_token_header_returns_200(server):
    code, _, _ = _req(server, "/api/status", token=TOKEN, header_name="X-API-Token")
    assert code == 200


@pytest.mark.parametrize(
    "path", ["/api/designs", "/api/plans", "/api/tools", "/api/config", "/api/status"]
)
def test_sensitive_get_endpoints_require_auth(server, path):
    code, _, _ = _req(server, path)
    assert code == 401


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/design", "POST"),
        ("/api/config", "POST"),
        ("/api/restart", "POST"),
        ("/api/chat", "POST"),
    ],
)
def test_sensitive_post_endpoints_require_auth(server, path, method):
    code, _, _ = _req(server, path, method=method)
    assert code == 401


def test_cors_rejects_malicious_origin(server):
    code, body, hdrs = _req(server, "/api/status", token=TOKEN, origin="http://evil.example.com")
    acao = hdrs.get("access-control-allow-origin")
    assert acao != "http://evil.example.com" and acao != "*"


def test_cors_allows_local_frontend_origin(server):
    code, body, hdrs = _req(server, "/api/status", token=TOKEN, origin="http://localhost:5173")
    acao = hdrs.get("access-control-allow-origin")
    assert acao == "http://localhost:5173"


def test_server_runtime_is_isolated(server):
    import sqlite3
    from contextlib import closing

    root = server["root"]
    assert root != PYTHON_DIR.parent
    assert (root / "python/api/server.py").read_bytes() == (PYTHON_DIR / "api/server.py").read_bytes()
    assert json.loads((root / "config/models.yaml").read_text()) == OFFLINE_CONFIG
    assert not (root / "config/models.local.yaml").exists()
    assert not (root / "config/api_token").exists()
    assert (root / "verify_http_server.log").is_file()
    assert (root / "logs/eas.log").is_file()
    db_path = root / "data/eas.db"
    assert db_path.is_file()
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0
    endpoint = json.loads((root / "server-endpoint.json").read_text())
    assert endpoint["pid"] == server["pid"]
    assert endpoint["port"] > 0


def test_prepare_root_copies_only_source_and_minimal_config(tmp_path):
    source = tmp_path / "original/python"
    source.mkdir(parents=True)
    for name in ("module.py", "pkg/__init__.py", "data/private.py", "logs/private.py",
                 ".hidden.py", ".venv/private.py", "env/private.py",
                 "__pycache__/private.py", "private.db", "private.log", "secret.json"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel", encoding="utf-8")
    original_config = source.parent / "config"
    original_config.mkdir()
    (original_config / "models.local.yaml").write_text("private", encoding="utf-8")
    root = tmp_path / "isolated"
    root.mkdir()
    _prepare_root(root, source)
    copied = {p.relative_to(root / "python").as_posix()
              for p in (root / "python").rglob("*") if p.is_file()}
    assert copied == {"module.py", "pkg/__init__.py"}
    assert json.loads((root / "config/models.yaml").read_text()) == OFFLINE_CONFIG
    assert not (root / "config/models.local.yaml").exists()
    assert list((root / "data").iterdir()) == []
    assert list((root / "external_tools").iterdir()) == []
    assert (original_config / "models.local.yaml").read_text() == "private"


def test_child_env_drops_inherited_credentials_paths_and_real_evals(tmp_path):
    inherited = {
        "PATH": "system-path", "SYSTEMROOT": "system-root", "TEMP": "temp-path",
        "EAS_API_TOKEN": "private", "EAS_WORKSPACE_ROOT": "business-root",
        "EAS_TOOLS_DIR": "external-plugins", "EAS_LLM_API_KEY_ANY": "private",
        "EAS_LLM_BASE_URL_ANY": "external-url", "EAS_OTHER_PATH": "business-path",
        "OPENAI_API_KEY": "private", "CUSTOM_TOKEN": "private",
        "RUN_REAL_LLM_EVAL": "1", "RUN_REAL_GH": "1", "RUN_REAL_OTHER": "1",
        "PYTHONPATH": "original-source", "PYTHONHOME": "other-python",
        "PYTHONSTARTUP": "external-script",
    }
    before = dict(inherited)
    env = _child_env(tmp_path, inherited)
    assert inherited == before
    assert env["PATH"] == "system-path"
    assert env["SYSTEMROOT"] == "system-root"
    assert env["EAS_API_TOKEN"] == TOKEN
    assert env["EAS_WORKSPACE_ROOT"] == str(tmp_path)
    assert env["EAS_TOOLS_DIR"] == str(tmp_path / "external_tools")
    assert env["PYTHONPATH"] == str(tmp_path / "python")
    assert env["RUN_REAL_LLM_EVAL"] == env["RUN_REAL_GH"] == "0"
    for name in ("EAS_LLM_API_KEY_ANY", "EAS_LLM_BASE_URL_ANY", "EAS_OTHER_PATH",
                 "OPENAI_API_KEY", "CUSTOM_TOKEN", "RUN_REAL_OTHER", "PYTHONHOME", "PYTHONSTARTUP"):
        assert name not in env


def test_stop_process_terminates_and_reaps():
    proc = Mock()
    proc.poll.return_value = None
    _stop_process(proc)
    proc.terminate.assert_called_once_with()
    proc.wait.assert_called_once_with(timeout=10)
    proc.kill.assert_not_called()


def test_stop_process_reaps_already_exited_child():
    proc = Mock()
    proc.poll.return_value = 1
    _stop_process(proc)
    proc.terminate.assert_not_called()
    proc.wait.assert_called_once_with(timeout=10)
    proc.kill.assert_not_called()


def test_stop_process_waits_after_forced_kill():
    proc = Mock()
    proc.poll.return_value = None
    proc.wait.side_effect = [subprocess.TimeoutExpired("test-server", 10), 0]
    _stop_process(proc)
    proc.terminate.assert_called_once_with()
    proc.kill.assert_called_once_with()
    assert proc.wait.call_count == 2


def test_spawn_failure_closes_log(tmp_path, monkeypatch):
    handles = []

    def fail(*args, **kwargs):
        handles.append(kwargs["stdout"])
        assert kwargs["cwd"] == str(tmp_path)
        raise OSError("spawn failed")

    monkeypatch.setattr(subprocess, "Popen", fail)
    with pytest.raises(OSError, match="spawn failed"):
        with _running_server(tmp_path):
            pytest.fail("must not yield")
    assert handles[0].closed


def test_startup_early_exit_reaps_child(tmp_path, monkeypatch):
    proc = Mock()
    proc.poll.return_value = 1
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=proc))
    with pytest.raises(RuntimeError, match="exited"):
        with _running_server(tmp_path):
            pytest.fail("must not yield")
    proc.wait.assert_called_once_with(timeout=10)
    proc.terminate.assert_not_called()


def test_startup_timeout_stops_child(tmp_path, monkeypatch):
    proc = Mock()
    proc.poll.return_value = None
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=proc))
    with pytest.raises(RuntimeError, match="timed out"):
        with _running_server(tmp_path, startup_timeout=0):
            pytest.fail("must not yield")
    proc.terminate.assert_called_once_with()
    proc.wait.assert_called_once_with(timeout=10)


def test_wrong_endpoint_pid_is_rejected_before_request(tmp_path, monkeypatch):
    proc = Mock(pid=123)
    proc.poll.return_value = None
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=proc))
    (tmp_path / "server-endpoint.json").write_text(json.dumps({"pid": 456, "port": 8011}))
    request = Mock(side_effect=AssertionError("must not contact another server"))
    monkeypatch.setitem(globals(), "_req", request)
    with pytest.raises(RuntimeError, match="different process"):
        with _running_server(tmp_path):
            pytest.fail("must not yield")
    request.assert_not_called()
    proc.terminate.assert_called_once_with()


def test_body_exception_still_stops_child(tmp_path, monkeypatch):
    proc = Mock(pid=123)
    proc.poll.return_value = None
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=proc))
    (tmp_path / "server-endpoint.json").write_text(json.dumps({"pid": 123, "port": 50000}))
    monkeypatch.setitem(globals(), "_req", Mock(return_value=(200, "{}", {})))
    with pytest.raises(ValueError, match="test body failed"):
        with _running_server(tmp_path) as handle:
            assert handle["base_url"] == "http://127.0.0.1:50000"
            raise ValueError("test body failed")
    proc.terminate.assert_called_once_with()
    proc.wait.assert_called_once_with(timeout=10)
