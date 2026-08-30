# -*- coding: utf-8 -*-
"""browser_check 回归：输入校验矩阵（离线）+ 真实浏览器用例（RUN_REAL_BROWSER=1）。

依赖懒加载语义：playwright 缺失时工具返回 blocked 而不是崩溃；本环境已装
playwright+chromium，真实用例打标 RUN_REAL_BROWSER=1 运行。"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools import get_registry
from tools import browser_check as mod
from tools.browser_check import BrowserCheck
from executor import DefaultExecutor

_PAGE_HTML = """<html><head><title>EAS</title></head>
<body><h1 id="title">EAS browser_check OK</h1><p class="hint">hello</p></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = _PAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - 与标准库签名一致
        pass


# ===== 输入校验（全部离线，不触浏览器） =====

def test_metadata_complete():
    meta = BrowserCheck.get_metadata()
    assert meta["tool_id"] == "browser_check"
    assert meta["category"] == "test"
    instructions = meta["capabilities"]["instructions"]
    assert {i["name"] for i in instructions} == {"check_page"}
    assert all(str(v).strip() for v in instructions[0]["parameters"].values())


def test_missing_url_blocked():
    result = BrowserCheck.execute("check_page", {})
    assert result["success"] is False
    assert result.get("blocked") is True


def test_bad_scheme_blocked():
    result = BrowserCheck.execute("check_page", {"url": "file:///C:/x.html", "expect_text": "x"})
    assert result["success"] is False
    assert result.get("blocked") is True
    assert result["error"] == "Invalid or blocked URL"


def test_ssrf_blocked():
    result = BrowserCheck.execute(
        "check_page", {"url": "http://169.254.169.254/latest/meta-data/", "expect_text": "x"}
    )
    assert result["success"] is False
    assert result.get("blocked") is True


def test_no_assertions_blocked():
    """只有 url、没有任何断言/截图 → 拒绝执行（白开一次浏览器没有意义）。"""
    result = BrowserCheck.execute("check_page", {"url": "https://example.com"})
    assert result["success"] is False
    assert result.get("blocked") is True
    assert result["error"] == "No assertions"


def test_unknown_instruction_rejected():
    result = BrowserCheck.execute("click", {"url": "https://example.com"})
    assert result["success"] is False
    assert result["error"] == "Unknown instruction"


def test_dependency_missing_blocked(monkeypatch):
    """playwright 缺失 → blocked + 安装提示，不炸注册表。"""
    monkeypatch.setattr(mod, "_load_playwright", lambda: (None, "playwright 未安装（模拟）；请先安装"))
    result = BrowserCheck.execute("check_page", {"url": "https://example.com", "expect_text": "x"})
    assert result["success"] is False
    assert result.get("blocked") is True
    assert result["error"] == "Dependency missing"
    assert "playwright" in result["message"]


# ===== 注册与推断接线 =====

def test_registered_in_tool_registry():
    registry = get_registry()
    assert registry.get_tool("browser_check") is BrowserCheck


def test_inferred_instruction_accepted():
    executor = DefaultExecutor(None, {})
    assert executor._infer_instruction("browser_check", {"url": "http://x"}) == "check_page"


# ===== 真实浏览器（打标，默认跳过） =====

@pytest.fixture()
def page_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.skipif(mod._load_playwright()[0] is None, reason="本机未安装 playwright")
def test_real_page_check_pass_and_screenshot(tmp_path, monkeypatch, page_url):
    from tools import web_fetcher

    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(web_fetcher, "_is_private_or_internal_ip", lambda ip: False)
    result = BrowserCheck.execute(
        "check_page",
        {
            "url": page_url,
            "expect_text": "EAS browser_check OK",
            "expect_selector": "#title",
            "output": "shots/real.png",
        },
    )
    assert result["success"] is True
    assert result["exit_code"] == 0
    assert all(c["passed"] for c in result["checks"])
    assert (tmp_path / "shots" / "real.png").is_file()


@pytest.mark.skipif(mod._load_playwright()[0] is None, reason="本机未安装 playwright")
def test_real_page_check_failure_semantics(monkeypatch, page_url):
    from tools import web_fetcher

    monkeypatch.setattr(web_fetcher, "_is_private_or_internal_ip", lambda ip: False)
    result = BrowserCheck.execute(
        "check_page",
        {"url": page_url, "not_expect_text": "EAS browser_check OK"},
    )
    assert result["success"] is False
    assert result["exit_code"] == 1
    assert result["checks"][0]["passed"] is False
