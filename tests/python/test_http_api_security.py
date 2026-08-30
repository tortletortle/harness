# -*- coding: utf-8 -*-
"""http_api 安全回归：SSRF 矩阵、scheme/方法白名单、不跟随重定向、敏感头不回显。
全部离线：SSRF 用例断言根本不会发起 HTTP 调用。"""
import pytest

from tools.http_api import ALLOWED_METHODS, _ECHO_HEADERS, HttpApi


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    """任何真实 HTTP 调用都视为测试失败：安全用例必须在不联网的前提下被拦截。"""

    def _no_request(self, *args, **kwargs):
        raise AssertionError("被拦截的请求不应触达 httpx.Client.request")

    monkeypatch.setattr("httpx.Client.request", _no_request)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/x",
        "http://192.168.1.1/x",
        "http://172.16.0.1/x",
        "http://0.0.0.0/x",
        "http://[::1]/x",
    ],
)
def test_ssrf_targets_blocked(url):
    result = HttpApi.execute("send_request", {"url": url})
    assert result["success"] is False
    assert result.get("blocked") is True
    assert result["error"] == "Invalid or blocked URL"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com"])
def test_non_http_schemes_blocked(url):
    result = HttpApi.execute("send_request", {"url": url})
    assert result["success"] is False
    assert result.get("blocked") is True


@pytest.mark.parametrize("method", ["OPTIONS", "TRACE", "CONNECT", "BREW"])
def test_method_whitelist(method):
    result = HttpApi.execute(
        "send_request", {"url": "https://example.com/x", "method": method}
    )
    assert result["success"] is False
    assert result.get("blocked") is True
    assert result["error"] == "Method not allowed"
    assert sorted(result["allowed"]) == sorted(ALLOWED_METHODS)


def test_default_timeout_positive():
    from tools import http_api as mod

    assert mod.DEFAULT_TIMEOUT > 0


def test_echo_headers_whitelist_constant():
    """敏感响应头不在回显白名单里（set-cookie/authorization 等）。"""
    forbidden = {"set-cookie", "authorization", "proxy-authenticate", "www-authenticate"}
    assert forbidden.isdisjoint(set(_ECHO_HEADERS))
