# -*- coding: utf-8 -*-
"""http_api 功能回归：本地 ThreadingHTTPServer 上的确定性请求（零外网）。

SSRF 防护会拒绝本机地址，因此访问本地服务器的用例临时放行内网 IP 判定
（与 test_web_fetcher_security.py 同款做法）；SSRF 拦截本身在
test_http_api_security.py 用真实逻辑验证。"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools import get_registry
from tools.http_api import HttpApi
from tools import web_fetcher
from executor import DefaultExecutor


class _Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/ok"):
            self._json(200, {"ok": True})
        elif self.path.startswith("/created"):
            self._json(201, {"created": True})
        elif self.path.startswith("/sleep"):
            time.sleep(1.0)
            self._json(200, {"woke": True})
        elif self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path.startswith("/cookie"):
            self._json(200, {"cookie": True}, extra_headers={"Set-Cookie": "secret=1; Path=/"})
        elif self.path.startswith("/headers"):
            self._json(200, {"headers": dict(self.headers.items())})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        self._json(200, {"method": "POST", "json": json.loads(raw or b"null"), "path": self.path})

    def do_PUT(self):
        self._json(200, {"method": "PUT"})

    def do_DELETE(self):
        self._json(200, {"method": "DELETE"})

    def log_message(self, format, *args):  # noqa: A002 - 与标准库签名一致
        pass


@pytest.fixture()
def base_url(monkeypatch):
    monkeypatch.setattr(web_fetcher, "_is_private_or_internal_ip", lambda ip: False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_get_json_ok(base_url):
    result = HttpApi.execute("send_request", {"url": f"{base_url}/ok"})
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert result["status"] == 200
    assert result["data"] == {"ok": True}
    assert result["elapsed_ms"] >= 0


def test_post_json_echo(base_url):
    result = HttpApi.execute(
        "send_request",
        {"url": f"{base_url}/echo", "method": "POST", "json": {"name": "eas"}},
    )
    assert result["success"] is True
    assert result["data"]["method"] == "POST"
    assert result["data"]["json"] == {"name": "eas"}


def test_put_and_delete_whitelisted(base_url):
    assert HttpApi.execute("send_request", {"url": f"{base_url}/x", "method": "PUT"})["success"] is True
    assert HttpApi.execute("send_request", {"url": f"{base_url}/x", "method": "DELETE"})["success"] is True


def test_lowercase_method_normalized(base_url):
    result = HttpApi.execute("send_request", {"url": f"{base_url}/ok", "method": "get"})
    assert result["success"] is True


def test_expected_status_match_and_mismatch(base_url):
    ok = HttpApi.execute("send_request", {"url": f"{base_url}/created", "expected_status": 201})
    assert ok["success"] is True and ok["status"] == 201

    mismatch = HttpApi.execute("send_request", {"url": f"{base_url}/ok", "expected_status": 201})
    assert mismatch["success"] is False
    assert mismatch["exit_code"] == 1
    assert mismatch["status"] == 200


def test_request_headers_forwarded(base_url):
    result = HttpApi.execute(
        "send_request",
        {"url": f"{base_url}/headers", "headers": {"X-Probe": "eas-1"}},
    )
    assert result["success"] is True
    assert result["data"]["headers"].get("X-Probe") == "eas-1"


def test_timeout_reported(base_url):
    result = HttpApi.execute("send_request", {"url": f"{base_url}/sleep", "timeout": 0.2})
    assert result["success"] is False
    assert result["exit_code"] == 1
    assert "超时" in result["message"]


def test_missing_url_blocked():
    result = HttpApi.execute("send_request", {})
    assert result["success"] is False
    assert result.get("blocked") is True


def test_unknown_instruction_rejected():
    result = HttpApi.execute("download", {"url": "http://example.com"})
    assert result["success"] is False
    assert result["error"] == "Unknown instruction"


def test_invalid_expected_status_blocked():
    result = HttpApi.execute("send_request", {"url": "http://127.0.0.1:1/x", "expected_status": "two-hundred"})
    assert result["success"] is False
    assert result.get("blocked") is True


def test_response_size_cap(base_url, monkeypatch):
    from tools import http_api as mod

    monkeypatch.setattr(mod, "_MAX_RESPONSE_BYTES", 10)
    result = HttpApi.execute("send_request", {"url": f"{base_url}/ok"})
    assert result["success"] is False
    assert result["error"] == "Response too large"


# ===== 注册与推断接线 =====

def test_registered_in_tool_registry():
    registry = get_registry()
    assert registry.get_tool("http_api") is HttpApi


def test_inferred_instruction_accepted():
    executor = DefaultExecutor(None, {})
    assert executor._infer_instruction("http_api", {"url": "http://x"}) == "send_request"
