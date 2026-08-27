# -*- coding: utf-8 -*-
"""
阶段1.3回归测试：web_fetcher 超时/大小限制/协议限制/内网地址拦截(SSRF防护)
"""
import inspect
import os
import tempfile

import pytest

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


@pytest.mark.integration
def test_fetch_content_real_public_url():
    """Requires real network access; marked integration so it's skipped by default."""
    result = WebFetcher.fetch_content(url="http://example.com/", timeout=10)
    assert result.get("success") is True
