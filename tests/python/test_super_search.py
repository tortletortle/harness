# -*- coding: utf-8 -*-
"""
super_search 回归测试：全部离线，不访问外网。

覆盖：
- 工具元数据与指令分发契约
- 引擎 HTML 解析（DuckDuckGo 跳转链接解包 / Bing b_algo 块）
- 多引擎合并去重排序（URL 归一化、共识加分）
- 未命中 vs 故障的区分（docs/ERROR_HANDLING.md 工具层约定）
- SSRF 防护（内网/保留地址/协议拦截，被拦截页面单页 blocked 不整体失败）
- 正文抽取（本地 HTTP 服务器端到端，script 内容不进正文）
- executor._infer_instruction 的 super_search 映射
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tools import super_search as ss
from tools.super_search import (
    SuperSearch,
    _parse_ddg_html,
    _parse_bing_html,
    _norm_url,
    _html_to_text,
    _auto_select_engines,
    _normalize_engines,
)


# ===== 元数据与指令分发契约 =====

def test_metadata_contract():
    meta = SuperSearch.get_metadata()
    assert meta["tool_id"] == "super_search"
    assert meta["category"] == "search"
    instructions = [i["name"] for i in meta["capabilities"]["instructions"]]
    assert instructions == ["search", "deep_search", "extract_text"]


def test_execute_unknown_instruction():
    result = SuperSearch.execute("no_such_instruction", {})
    assert result.get("success") is False
    assert result.get("error") == "Invalid instruction"


def test_search_empty_query_rejected():
    result = SuperSearch.search("   ")
    assert result.get("success") is False
    assert "query" in result.get("message", "")


def test_search_unknown_engine_rejected():
    result = SuperSearch.search("test", engines="baidu,nosuch")
    assert result.get("success") is False
    assert "未知搜索引擎" in result.get("message", "")


def test_normalize_engines_string_and_dedupe():
    assert _normalize_engines("DuckDuckGo, bing,bing") == ["duckduckgo", "bing"]
    assert _normalize_engines([]) is None
    assert _normalize_engines(None) is None


def test_max_results_clamped_to_engine(monkeypatch):
    captured = {}

    def fake_engine(query, max_results, timeout):
        captured["n"] = max_results
        return [{"title": "t", "url": "https://example.com/", "snippet": "s"}]

    monkeypatch.setitem(ss._ENGINE_FUNCS, "duckduckgo", fake_engine)
    result = SuperSearch.search("q", max_results=999)
    assert result["success"] is True
    assert captured["n"] == 30


# ===== 引擎 HTML 解析 =====

_DDG_FIXTURE = """
<div class="results">
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&amp;rut=abc"><b>Example</b> Page</a>
  </h2>
  <a class="result__snippet" href="#">An <b>example</b> snippet</a>
</div>
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="https://direct.com/x">Direct Result</a>
  </h2>
  <a class="result__snippet" href="#">Direct snippet</a>
</div>
</div>
"""


def test_parse_ddg_html():
    items = _parse_ddg_html(_DDG_FIXTURE)
    assert len(items) == 2
    # uddg 跳转链接解包出真实 URL，实体已解码
    assert items[0]["url"] == "https://example.com/page"
    assert items[0]["title"] == "Example Page"
    assert items[0]["snippet"] == "An example snippet"
    # 非跳转的直链保持原样
    assert items[1]["url"] == "https://direct.com/x"


_BING_FIXTURE = """
<ol id="b_results">
<li class="b_algo"><div><h2><a href="https://one.com/">One Title</a></h2></div>
<div class="b_caption"><p>First snippet</p></div></li>
<li class="b_algo"><div><h2><a href="https://two.com/">Two Title</a></h2></div>
<div class="b_caption"><p>Second snippet</p></div></li>
</ol>
"""


def test_parse_bing_html():
    items = _parse_bing_html(_BING_FIXTURE)
    assert len(items) == 2
    assert items[0]["url"] == "https://one.com/"
    assert items[0]["title"] == "One Title"
    assert items[0]["snippet"] == "First snippet"
    assert items[1]["url"] == "https://two.com/"


def test_parse_bing_html_garbage_returns_empty():
    assert _parse_bing_html("<html><body>blocked</body></html>") == []
    assert _parse_ddg_html("") == []


# ===== URL 归一化与合并排序 =====

def test_norm_url():
    assert _norm_url("https://WWW.A.com/x/?utm_source=z&id=2#frag") == "https://a.com/x?id=2"
    assert _norm_url("http://a.com/") == "http://a.com"
    assert _norm_url("ftp://a.com/x") == ""
    assert _norm_url("not a url") == ""


def test_search_merge_dedupe_and_consensus_ranking(monkeypatch):
    ddg_items = [
        {"title": "R1", "url": "https://a.com/x?utm_source=z", "snippet": "s1"},
        {"title": "R2", "url": "https://b.com/", "snippet": "s2"},
    ]
    bing_items = [
        {"title": "R1 dup", "url": "https://a.com/x#frag", "snippet": "s1-longer"},
        {"title": "R3", "url": "https://c.com/", "snippet": "s3"},
    ]
    monkeypatch.setitem(ss._ENGINE_FUNCS, "duckduckgo", lambda q, n, t: ddg_items)
    monkeypatch.setitem(ss._ENGINE_FUNCS, "bing", lambda q, n, t: bing_items)

    result = SuperSearch.search("q")
    assert result["success"] is True
    assert result["engines_used"] == ["duckduckgo", "bing"]
    urls = [r["url"] for r in result["results"]]
    # a.com/x 两个引擎都命中：utm/fragment 差异被归一化掉，共识加分排第一
    assert len(result["results"]) == 3
    assert urls[0].startswith("https://a.com/x")
    assert set(result["results"][0]["engines"]) == {"bing", "duckduckgo"}
    # 摘要取最长
    assert result["results"][0]["snippet"] == "s1-longer"
    assert [r["rank"] for r in result["results"]] == [1, 2, 3]


def test_search_all_engines_fail_is_visible_failure(monkeypatch):
    def boom(query, max_results, timeout):
        raise ss.EngineError("HTTP 403")

    monkeypatch.setitem(ss._ENGINE_FUNCS, "duckduckgo", boom)
    monkeypatch.setitem(ss._ENGINE_FUNCS, "bing", boom)
    result = SuperSearch.search("q")
    assert result["success"] is False
    assert "所有搜索引擎均未返回结果" in result["message"]
    assert result["engines_failed"]["bing"] == "HTTP 403"


def test_search_single_engine_failure_does_not_kill_task(monkeypatch):
    def boom(query, max_results, timeout):
        raise ss.EngineError("HTTP 403")

    monkeypatch.setitem(ss._ENGINE_FUNCS, "duckduckgo", boom)
    monkeypatch.setitem(
        ss._ENGINE_FUNCS, "bing",
        lambda q, n, t: [{"title": "T", "url": "https://ok.com/", "snippet": "s"}],
    )
    result = SuperSearch.search("q")
    assert result["success"] is True
    assert len(result["results"]) == 1
    assert result["engines_failed"]["duckduckgo"] == "HTTP 403"


def test_search_engine_ok_but_zero_results(monkeypatch):
    monkeypatch.setitem(ss._ENGINE_FUNCS, "duckduckgo", lambda q, n, t: [])
    monkeypatch.setitem(ss._ENGINE_FUNCS, "bing", lambda q, n, t: [])
    result = SuperSearch.search("q")
    # 引擎本身正常但确实未命中：success=True + 空结果（未命中 ≠ 故障）
    assert result["success"] is True
    assert result["results"] == []
    assert result["engines_used"] == ["duckduckgo", "bing"]
    assert "note" in result["engine_stats"]["bing"]


def test_auto_select_engines_with_api_keys(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert _auto_select_engines() == ["duckduckgo", "bing"]
    monkeypatch.setenv("TAVILY_API_KEY", "k1")
    monkeypatch.setenv("SERPER_API_KEY", "k2")
    assert _auto_select_engines() == ["duckduckgo", "bing", "tavily", "serper"]


# ===== 正文抽取 =====

_EXTRACT_MARKER = "EAS-EXTRACT-TEST-MARKER-9d21"


class _PageHandler(BaseHTTPRequestHandler):
    """固定页面：正文含唯一标记，script/style 内容不得进入正文。"""

    def do_GET(self):
        body = (
            "<html><head><style>.x{color:red}</style>"
            "<script>var noise='SCRIPT-NOISE-COOKIE';</script></head>"
            f"<body><h1>{_EXTRACT_MARKER}</h1><p>para one</p>"
            "<div><p>para two</p></div></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 - 与标准库签名一致
        pass


@pytest.fixture()
def local_page_url(monkeypatch):
    """本地 HTTP 服务器（extract_text 的 SSRF 防护会拒绝本机地址，测试内临时放行）。"""
    from tools import web_fetcher

    monkeypatch.setattr(web_fetcher, "_is_private_or_internal_ip", lambda ip: False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_html_to_text_strips_script_and_style():
    html_str = "<html><head><script>evil()</script><style>b{}.c{}</style></head><body><p>keep me</p></body></html>"
    text = _html_to_text(html_str)
    assert "keep me" in text
    assert "evil" not in text
    assert "b{}" not in text


def test_extract_text_local_page(local_page_url):
    result = SuperSearch.extract_text(local_page_url, timeout=10)
    assert result["success"] is True
    assert _EXTRACT_MARKER in result["content"]
    assert "SCRIPT-NOISE-COOKIE" not in result["content"]
    assert result["page_chars"] == len(result["content"])


def test_extract_text_blocks_internal_addresses():
    for url in (
        "http://127.0.0.1:8000/x",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
    ):
        result = SuperSearch.extract_text(url)
        assert result["success"] is False
        assert result["error"] == "Invalid or blocked URL"
        assert result["blocked"] is True


# ===== deep_search =====

def test_deep_search_fetches_pages_and_flags_blocked(monkeypatch):
    search_items = [
        {"title": "Good", "url": "https://example.com/page1", "snippet": "s"},
        {"title": "SSRF", "url": "http://169.254.169.254/meta", "snippet": "s"},
    ]
    monkeypatch.setitem(ss._ENGINE_FUNCS, "duckduckgo", lambda q, n, t: search_items)
    monkeypatch.setitem(ss._ENGINE_FUNCS, "bing", lambda q, n, t: [])
    monkeypatch.setattr(
        ss, "_http_fetch",
        lambda *a, **kw: "<html><body><p>DEEP-PAGE-MARKER</p></body></html>",
    )

    result = SuperSearch.deep_search("q", fetch_top=2)
    assert result["success"] is True
    assert result["deep"] is True
    assert result["message"].startswith("深度检索完成：")
    good = result["results"][0]
    assert "DEEP-PAGE-MARKER" in good["content"]
    # 内网地址被 SSRF 防护拦截：单页 blocked，任务不整体失败
    flagged = result["results"][1]
    assert flagged["blocked"] is True
    assert "content" not in flagged


def test_deep_search_aborts_when_search_fails(monkeypatch):
    def boom(query, max_results, timeout):
        raise ss.EngineError("HTTP 403")

    monkeypatch.setitem(ss._ENGINE_FUNCS, "duckduckgo", boom)
    monkeypatch.setitem(ss._ENGINE_FUNCS, "bing", boom)
    result = SuperSearch.deep_search("q")
    assert result["success"] is False
    assert result["message"].startswith("深度检索中止")


# ===== 与执行器集成：指令推断映射 =====

def test_executor_infer_instruction_mapping():
    from executor import DefaultExecutor
    from llm_client import LLMClient

    executor = DefaultExecutor(llm_client=LLMClient(), model_config=None)
    assert executor._infer_instruction("super_search", {"query": "x"}) == "search"
    assert executor._infer_instruction(
        "super_search", {"query": "x", "fetch_top": 3}
    ) == "deep_search"
    assert executor._infer_instruction(
        "super_search", {"url": "https://a.com"}
    ) == "extract_text"


# ===== 注册表契约 =====

def test_registry_contains_super_search():
    from tools import get_registry

    registry = get_registry()
    tool_ids = [t.get("tool_id") for t in registry.list_tools()]
    assert "super_search" in tool_ids
    # 别名解析不回归
    assert registry.get_tool("super_search") is not None
