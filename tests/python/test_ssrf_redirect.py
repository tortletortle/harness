# -*- coding: utf-8 -*-
"""
SSRF 重定向链防护回归测试：全部离线，不访问外网。

背景：_validate_url 只校验初始 URL；urllib 默认跟随重定向且不复检，
公网 URL 经 302 跳转到内网/元数据地址可绕过防护（Mimosa 复核发现的残留）。
修复：_SafeRedirectHandler 对每个跳转目标重新过 _validate_url。

覆盖：
- web_fetcher.fetch_content / download_file：跳内网被拦（blocked=True，不落盘）
- 合法跳转（仍为公网）正常跟随
- super_search._http_fetch：跳内网转 EngineError（HTTP 302，未取到目标内容）
- _SafeRedirectHandler 单测：元数据 IP 拒绝 / 公网放行

手法：假 DNS（redirect.test → 公网 IP）+ create_connection 改道本地，
让“校验用公网身份、连接走本地服务器”，全程离线且防护保持武装。
"""
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools.super_search import EngineError, _http_fetch
from tools.web_fetcher import (
    WebFetcher,
    _RedirectBlockedError,
    _SafeRedirectHandler,
    _validate_url,
)

_FAKE_HOST = "redirect.test"
_FAKE_PUBLIC_IP = "93.184.216.34"  # 假 DNS 应答（公网段），实际连接被改道到本地
_SECRET_MARKER = "SECRET-PWNED"

_REAL_GETADDRINFO = socket.getaddrinfo
_REAL_CREATE_CONNECTION = socket.create_connection


def _fake_getaddrinfo(host, port, *args, **kwargs):
    if host == _FAKE_HOST:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_FAKE_PUBLIC_IP, 0))]
    return _REAL_GETADDRINFO(host, port, *args, **kwargs)


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        port = self.server.server_address[1]
        if self.path == "/to-private":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}/secret")
            self.end_headers()
        elif self.path == "/to-public":
            self.send_response(302)
            self.send_header("Location", f"http://{_FAKE_HOST}/final")
            self.end_headers()
        elif self.path == "/final":
            body = b"final-ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/secret":
            body = _SECRET_MARKER.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # 保持测试输出干净
        pass


@pytest.fixture()
def redirect_base(monkeypatch):
    """本地跳转服务器：校验走假公网身份，连接改道到本地。"""
    # hermetic：清掉代理环境变量，避免 urllib 经代理出站（代理会吞掉本地改道）。
    for _proxy_var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.delenv(_proxy_var, raising=False)
    # Windows 还会从注册表读系统代理；getproxies 是 env+注册表的唯一汇合点，一并掐掉。
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    port = server.server_address[1]

    def _fake_create_connection(address, *args, **kwargs):
        host, _ = address
        if host == _FAKE_HOST:
            return _REAL_CREATE_CONNECTION(("127.0.0.1", port), *args, **kwargs)
        return _REAL_CREATE_CONNECTION(address, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", _fake_create_connection)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{_FAKE_HOST}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_fake_host_passes_initial_validation(redirect_base):
    assert _validate_url(f"{redirect_base}/to-private") is None


def test_fetch_content_blocks_redirect_to_loopback(redirect_base):
    result = WebFetcher.fetch_content(f"{redirect_base}/to-private")
    assert result["success"] is False
    assert result.get("blocked") is True
    assert "重定向目标被拒绝" in result["message"]
    assert _SECRET_MARKER not in result.get("message", "")


def test_download_file_blocks_redirect_to_loopback(redirect_base, tmp_path):
    target = tmp_path / "evil.bin"
    result = WebFetcher.download_file(f"{redirect_base}/to-private", str(target))
    assert result["success"] is False
    assert result.get("blocked") is True
    assert "重定向目标被拒绝" in result["message"]
    assert not target.exists()


def test_safe_redirect_is_followed(redirect_base):
    result = WebFetcher.fetch_content(f"{redirect_base}/to-public")
    assert result["success"] is True
    assert result["content"] == "final-ok"


def test_super_search_http_fetch_blocks_redirect(redirect_base):
    with pytest.raises(EngineError, match="HTTP 302") as exc_info:
        _http_fetch(f"{redirect_base}/to-private")
    assert _SECRET_MARKER not in str(exc_info.value)


def test_redirect_handler_unit_blocks_metadata_ip():
    handler = _SafeRedirectHandler()
    req = urllib.request.Request(f"http://{_FAKE_HOST}/start")
    with pytest.raises(_RedirectBlockedError):
        handler.redirect_request(
            req, None, 302, "Found", {}, "http://169.254.169.254/latest/meta-data"
        )


def test_redirect_handler_unit_allows_public(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    handler = _SafeRedirectHandler()
    req = urllib.request.Request(f"http://{_FAKE_HOST}/start")
    out = handler.redirect_request(req, None, 302, "Found", {}, f"http://{_FAKE_HOST}/other")
    assert isinstance(out, urllib.request.Request)
    assert out.get_full_url() == f"http://{_FAKE_HOST}/other"

