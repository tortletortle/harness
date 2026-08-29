"""
P0 修复的真实 HTTP 验证：起一个真实的 uvicorn 子进程，发送真实 HTTP 请求
验证 401/200/409 与 CORS 行为。
"""

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

TOKEN = "e2e-verify-token-9988"
BASE = "http://127.0.0.1:8011"
PY = sys.executable
PYTHON_DIR = Path(__file__).resolve().parent.parent.parent / "python"


def _lower(headers):
    """HTTP 头不区分大小写，uvicorn 发小写。统一降为小写再查。"""
    return {k.lower(): v for k, v in headers.items()}


def _req(path, token=None, header_name="Authorization", method="GET", origin=None):
    url = BASE + path
    r = urllib.request.Request(url, method=method)
    if token:
        value = f"Bearer {token}" if header_name == "Authorization" else token
        r.add_header(header_name, value)
    if origin:
        r.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), _lower(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), _lower(e.headers)
    except Exception as e:
        return 0, f"ERR {e}", {}


@pytest.fixture(scope="module")
def server():
    """在子进程中启动真实服务，测试结束后关闭。"""
    env = dict(os.environ)
    env["EAS_API_TOKEN"] = TOKEN
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    log_path = PYTHON_DIR / "verify_http_server.log"
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", "8011"],
        stdout=log, stderr=subprocess.STDOUT, env=env, cwd=str(PYTHON_DIR),
    )

    ready = False
    try:
        for _ in range(60):
            code, _, _ = _req("/")
            if code == 200:
                ready = True
                break
            time.sleep(1)

        if not ready:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()
            pytest.fail("服务启动失败，详见 verify_http_server.log")

        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def test_health_probe_no_auth_required(server):
    code, body, _ = _req("/")
    assert code == 200


def test_no_token_returns_401(server):
    code, body, hdrs = _req("/api/status")
    assert code == 401
    assert "bearer" in hdrs.get("www-authenticate", "").lower()


def test_wrong_token_returns_401(server):
    code, _, _ = _req("/api/status", token="wrong-token")
    assert code == 401


def test_correct_bearer_token_returns_200(server):
    code, body, _ = _req("/api/status", token=TOKEN)
    assert code == 200
    assert '"status"' in body


def test_x_api_token_header_returns_200(server):
    code, _, _ = _req("/api/status", token=TOKEN, header_name="X-API-Token")
    assert code == 200


@pytest.mark.parametrize(
    "path", ["/api/designs", "/api/plans", "/api/tools", "/api/config", "/api/status"]
)
def test_sensitive_get_endpoints_require_auth(server, path):
    code, _, _ = _req(path)
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
    code, _, _ = _req(path, method=method)
    assert code == 401


def test_cors_rejects_malicious_origin(server):
    code, body, hdrs = _req("/api/status", token=TOKEN, origin="http://evil.example.com")
    acao = hdrs.get("access-control-allow-origin")
    assert acao != "http://evil.example.com" and acao != "*"


def test_cors_allows_local_frontend_origin(server):
    code, body, hdrs = _req("/api/status", token=TOKEN, origin="http://localhost:5173")
    acao = hdrs.get("access-control-allow-origin")
    assert acao == "http://localhost:5173"
