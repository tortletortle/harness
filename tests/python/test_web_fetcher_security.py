# -*- coding: utf-8 -*-
"""
阶段1.3回归测试：web_fetcher 超时/大小限制/协议限制/内网地址拦截(SSRF防护)
"""
import inspect
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools import web_fetcher
from tools.web_fetcher import (
    WebFetcher,
    _validate_url,
    _is_private_or_internal_ip,
    _MAX_DOWNLOAD_SIZE,
    _MAX_CONTENT_SIZE,
    _DEFAULT_TIMEOUT,
)


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("127.0.0.1", True),
        ("10.0.0.1", True),
        ("192.168.1.1", True),
        ("172.16.0.1", True),
        ("169.254.169.254", True),  # link-local, common cloud metadata address
        ("::1", True),  # IPv6 loopback
        ("8.8.8.8", False),
    ],
)
def test_is_private_or_internal_ip(ip, expected):
    assert _is_private_or_internal_ip(ip) is expected


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/file"],
)
def test_disallowed_schemes_rejected(url):
    assert _validate_url(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
def test_internal_urls_rejected(url):
    assert _validate_url(url) is not None


def test_download_file_rejects_localhost():
    result = WebFetcher.download_file(
        url="http://127.0.0.1:8000/x",
        path=os.path.join(tempfile.gettempdir(), "x.txt"),
    )
    assert result.get("success") is False
    assert result.get("error") == "Invalid or blocked URL"


def test_fetch_content_rejects_file_scheme():
    result = WebFetcher.fetch_content(url="file:///etc/passwd")
    assert result.get("success") is False
    assert result.get("error") == "Invalid or blocked URL"


def test_fetch_content_rejects_cloud_metadata_address():
    result = WebFetcher.fetch_content(url="http://169.254.169.254/latest/meta-data/")
    assert result.get("success") is False
    assert result.get("error") == "Invalid or blocked URL"


def test_download_file_has_timeout_param():
    sig = inspect.signature(WebFetcher.download_file)
    assert "timeout" in sig.parameters


def test_fetch_content_has_timeout_param():
    sig = inspect.signature(WebFetcher.fetch_content)
    assert "timeout" in sig.parameters


def test_size_and_timeout_limits_defined():
    assert _MAX_DOWNLOAD_SIZE > 0
    assert _MAX_CONTENT_SIZE > 0
    assert _DEFAULT_TIMEOUT > 0


_FETCH_TEST_MARKER = "EAS-FETCH-TEST-MARKER-7f3a"


class _LocalTestHandler(BaseHTTPRequestHandler):
    """固定页面处理器：`/` 返回含唯一标记的 HTML，其余路径返回 404。"""

    def do_GET(self):
        if self.path == "/":
            body = f"<html><body>{_FETCH_TEST_MARKER}</body></html>".encode("utf-8")
            status = 200
        else:
            body = b"<html><body>not found</body></html>"
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - 与标准库签名一致
        pass  # 静默请求日志，保持测试输出干净


@pytest.fixture()
def local_http_server_url(monkeypatch):
    """在 127.0.0.1 临时端口起一个本地 HTTP 服务器（后台线程），返回其 base URL。

    fetch_content 的 SSRF 防护会拒绝本机地址，因此仅在访问本地服务器的
    测试中临时放行内网 IP 判定；其他用例仍直接验证真实的拦截逻辑。
    """
    monkeypatch.setattr(
        web_fetcher, "_is_private_or_internal_ip", lambda ip: False
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_fetch_content_local_http_server(local_http_server_url):
    """确定性获取：本地服务器 200 页面应成功并包含标记内容（不依赖外网）。"""
    result = WebFetcher.fetch_content(url=f"{local_http_server_url}/", timeout=10)
    assert result.get("success") is True
    assert _FETCH_TEST_MARKER in result.get("content", "")


def test_fetch_content_local_http_server_404(local_http_server_url):
    """确定性失败：本地服务器 404 路由应返回 success=False。"""
    result = WebFetcher.fetch_content(
        url=f"{local_http_server_url}/definitely-missing", timeout=10
    )
    assert result.get("success") is False
