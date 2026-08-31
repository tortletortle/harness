"""
超级搜索工具（Super Search）

多引擎聚合搜索 + 深度检索 + 正文抽取，作为 harness 内置搜索能力：
- 无需 API Key 即可使用（DuckDuckGo HTML + Bing 网页解析作为无键引擎）；
  若环境变量提供 TAVILY_API_KEY / BRAVE_API_KEY / SERPER_API_KEY，
  则自动启用对应 API 引擎增强。
- 多引擎结果合并、按归一化 URL 去重、按"共识度 + 位置分"排序。
- deep_search：搜索后并发抓取前 N 个页面并抽取正文文本（去 script/style/标签）。
- 复用 web_fetcher 的 SSRF 防护（内网/保留地址拦截）；被拦截的页面单页
  标记 blocked，不影响整体任务。
- 遵守 docs/ERROR_HANDLING.md 工具层约定：永不抛异常，统一返回
  {"success": bool, "message": str, ...}；全部引擎失败必须显式可见。
"""

import gzip
import json
import os
import re
import urllib.error
import urllib.request
from html import unescape
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from tools.web_fetcher import _validate_url
from tools.workspace import resolve_workspace_root

_DEFAULT_TIMEOUT = 15  # 秒，搜索引擎请求
_MAX_ENGINE_BYTES = 2 * 1024 * 1024  # 单次引擎响应上限 2MB
_MAX_PAGE_BYTES = 5 * 1024 * 1024  # 单页抓取上限 5MB
_READ_CHUNK = 64 * 1024
_MAX_RESULTS_LIMIT = 30
_DEFAULT_MAX_RESULTS = 10
_DEEP_FETCH_TOP_LIMIT = 5
_DEEP_MAX_WORKERS = 4
_UA = "Mozilla/5.0 (compatible; EAS-SuperSearch/1.0)"


class EngineError(Exception):
    """单个搜索引擎失败（HTTP 错误/解析为空/未配置密钥等）"""


# ---------------------------------------------------------------------------
# HTTP 基础
# ---------------------------------------------------------------------------

def _read_limited(resp, max_bytes: int) -> bytes:
    """流式读取响应体，超过 max_bytes 立即中断，防止内存被打满。"""
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = resp.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise EngineError(f"响应过大（>{max_bytes} 字节），已中断读取")
        chunks.append(chunk)
    return b"".join(chunks)


def _http_fetch(
    url: str,
    timeout: float = _DEFAULT_TIMEOUT,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    max_bytes: int = _MAX_ENGINE_BYTES,
) -> str:
    """带 UA/超时/大小上限/gzip 解压的 HTTP GET/POST，返回解码后的文本。"""
    merged = {
        "User-Agent": _UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip",
    }
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=data, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = _read_limited(resp, max_bytes)
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except EngineError:
        raise
    except urllib.error.HTTPError as e:
        raise EngineError(f"HTTP {e.code}（{url}）") from e
    except Exception as e:
        raise EngineError(f"请求失败：{e}（{url}）") from e


# ---------------------------------------------------------------------------
# HTML 解析与正文抽取
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\s*\n\s*")
_MULTI_NL_RE = re.compile(r"\n{2,}")


def _strip_tags(text: str) -> str:
    """去标签 + 实体解码 + 空白折叠（用于标题/摘要兜底清洗）。"""
    if not text:
        return ""
    cleaned = _WS_RE.sub(" ", _TAG_RE.sub(" ", unescape(text)))
    return cleaned.strip()


class _TextExtractor(HTMLParser):
    """正文抽取：跳过 script/style 等，块级标签处换行，保留纯文本。"""

    _SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "object", "head"}
    _BLOCK_TAGS = {
        "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table",
        "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
        "blockquote", "pre", "header", "footer", "hr", "figure", "figcaption",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._pieces: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._pieces.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self._pieces.append(data)

    def get_text(self) -> str:
        raw = "".join(self._pieces)
        raw = _WS_RE.sub(" ", raw)
        raw = _NL_RE.sub("\n", raw)
        raw = _MULTI_NL_RE.sub("\n\n", raw)
        return raw.strip()


def _html_to_text(html_str: str) -> str:
    """HTML → 可读正文文本；解析失败时退化为正则去标签。"""
    if not html_str:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html_str)
        parser.close()
        return parser.get_text()
    except Exception:
        return _strip_tags(html_str)


# ---------------------------------------------------------------------------
# 搜索引擎实现：每个函数 (query, max_results, timeout) -> List[result]
# result 统一为 {"title": str, "url": str, "snippet": str}
# ---------------------------------------------------------------------------

_DDG_ANCHOR_RE = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET_RE = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)


def _unwrap_ddg_href(href: str) -> str:
    """DDG 结果链接多为 //duckduckgo.com/l/?uddg=<urlenc>&rut=... 跳转，解出真实 URL。"""
    href = unescape(href or "")
    if href.startswith("//"):
        href = "https:" + href
    if "uddg=" in href:
        try:
            uddg = parse_qs(urlparse(href).query).get("uddg", [None])[0]
            if uddg:
                return uddg
        except Exception:
            pass
    return href


def _parse_ddg_html(html_str: str) -> List[Dict[str, str]]:
    """解析 DuckDuckGo HTML 版搜索结果页。"""
    anchors = _DDG_ANCHOR_RE.findall(html_str or "")
    snippets = _DDG_SNIPPET_RE.findall(html_str or "")
    results: List[Dict[str, str]] = []
    for i, (href, title) in enumerate(anchors):
        url = _unwrap_ddg_href(href)
        if not url.startswith("http"):
            continue
        snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
        results.append({"title": _strip_tags(title), "url": url, "snippet": snippet})
    return results


_BING_LI_RE = re.compile(r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', re.S)
_BING_ANCHOR_RE = re.compile(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S)
_BING_SNIPPET_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


def _parse_bing_html(html_str: str) -> List[Dict[str, str]]:
    """解析 Bing 网页版搜索结果页（li.b_algo 块）。"""
    results: List[Dict[str, str]] = []
    for block in _BING_LI_RE.findall(html_str or ""):
        m = _BING_ANCHOR_RE.search(block)
        if not m:
            continue
        url = unescape(m.group(1))
        if not url.startswith("http"):
            continue
        s = _BING_SNIPPET_RE.search(block)
        results.append({
            "title": _strip_tags(m.group(2)),
            "url": url,
            "snippet": _strip_tags(s.group(1)) if s else "",
        })
    return results


def _search_duckduckgo(query: str, max_results: int, timeout: float) -> List[Dict[str, str]]:
    """DuckDuckGo HTML 版（无 API Key）。"""
    body = urlencode({"q": query}).encode("utf-8")
    html_str = _http_fetch(
        "https://html.duckduckgo.com/html/",
        timeout=timeout,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return _parse_ddg_html(html_str)[:max_results]


def _bing_fetch(query: str, max_results: int, timeout: float) -> List[Dict[str, str]]:
    """单次 Bing 查询。"""
    html_str = _http_fetch(
        "https://www.bing.com/search?" + urlencode({"q": query, "count": max_results}),
        timeout=timeout,
    )
    return _parse_bing_html(html_str)[:max_results]


def _search_bing(query: str, max_results: int, timeout: float) -> List[Dict[str, str]]:
    """Bing 网页版（无 API Key）。

    长中文查询（>20 字符）自动拆分为两个子查询并合并去重——
    真实测试发现 bing 对长中文多厂商查询的相关性会崩坏（返回单字字典页）。
    """
    if len(query) > 20 and re.search(r"[\u4e00-\u9fff]", query):
        mid = len(query) // 2
        cut = query.find(" ", mid)
        if cut == -1 or cut < 4 or cut > len(query) - 4:
            cut = mid
        parts = [p.strip() for p in (query[:cut], query[cut:]) if p.strip()]
        merged: List[Dict[str, str]] = []
        seen = set()
        for part in parts:
            for item in _bing_fetch(part, max_results, timeout):
                key = _norm_url(item.get("url", "")) or item.get("url", "")
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return merged[:max_results]

    return _bing_fetch(query, max_results, timeout)


def _search_tavily(query: str, max_results: int, timeout: float) -> List[Dict[str, str]]:
    """Tavily Search API（需 TAVILY_API_KEY）。"""
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        raise EngineError("未配置 TAVILY_API_KEY")
    body = json.dumps({
        "api_key": key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
    }).encode("utf-8")
    data = json.loads(_http_fetch(
        "https://api.tavily.com/search",
        timeout=timeout,
        data=body,
        headers={"Content-Type": "application/json"},
    ))
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "snippet": (r.get("content") or "")[:400]}
        for r in data.get("results", [])
        if r.get("url")
    ]


def _search_brave(query: str, max_results: int, timeout: float) -> List[Dict[str, str]]:
    """Brave Search API（需 BRAVE_API_KEY）。"""
    key = os.environ.get("BRAVE_API_KEY")
    if not key:
        raise EngineError("未配置 BRAVE_API_KEY")
    data = json.loads(_http_fetch(
        "https://api.search.brave.com/res/v1/web/search?"
        + urlencode({"q": query, "count": max_results}),
        timeout=timeout,
        headers={"Accept": "application/json", "X-Subscription-Token": key},
    ))
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""),
         "snippet": (r.get("description") or "")[:400]}
        for r in (data.get("web") or {}).get("results", [])
        if r.get("url")
    ]


def _search_serper(query: str, max_results: int, timeout: float) -> List[Dict[str, str]]:
    """Serper.dev（Google 结果，需 SERPER_API_KEY）。"""
    key = os.environ.get("SERPER_API_KEY")
    if not key:
        raise EngineError("未配置 SERPER_API_KEY")
    body = json.dumps({"q": query, "num": max_results}).encode("utf-8")
    data = json.loads(_http_fetch(
        "https://google.serper.dev/search",
        timeout=timeout,
        data=body,
        headers={"Content-Type": "application/json", "X-API-KEY": key},
    ))
    return [
        {"title": r.get("title", ""), "url": r.get("link", ""),
         "snippet": (r.get("snippet") or "")[:400]}
        for r in data.get("organic", [])
        if r.get("link")
    ]


def _stepfun_key() -> str:
    """阶跃 API key：环境变量 STEPFUN_API_KEY 优先，回落 config/models.yaml api_keys.stepfun。"""
    key = os.environ.get("STEPFUN_API_KEY", "").strip()
    if key:
        return key
    try:
        cfg_path = resolve_workspace_root() / "config" / "models.yaml"
        if cfg_path.exists():
            import yaml

            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            return str((cfg.get("api_keys") or {}).get("stepfun") or "").strip()
    except Exception:
        pass
    return ""


def _search_stepfun(query: str, max_results: int, timeout: float) -> List[Dict[str, str]]:
    """阶跃星辰网页搜索 API（POST /v1/search，信源权威性优化，中文友好）。"""
    key = _stepfun_key()
    if not key:
        raise EngineError("未配置 STEPFUN key（env STEPFUN_API_KEY 或 config/models.yaml api_keys.stepfun）")
    body = json.dumps({"query": query, "n": max_results}).encode("utf-8")
    data = json.loads(_http_fetch(
        "https://api.stepfun.com/step_plan/v1/search",
        timeout=timeout,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {key}",
        },
    ))
    results: List[Dict[str, str]] = []
    for r in data.get("results") or []:
        url = str(r.get("url") or "").strip()
        if not url:
            continue
        results.append({
            "title": str(r.get("title") or "").strip(),
            "url": url,
            "snippet": str(r.get("snippet") or r.get("content") or "")[:400],
        })
    return results


_ENGINE_FUNCS = {
    "duckduckgo": _search_duckduckgo,
    "bing": _search_bing,
    "stepfun": _search_stepfun,
    "tavily": _search_tavily,
    "brave": _search_brave,
    "serper": _search_serper,
}

_API_KEY_ENGINES = {"tavily": "TAVILY_API_KEY", "brave": "BRAVE_API_KEY", "serper": "SERPER_API_KEY"}

# 引擎权重（合并排序用）：用户偏好的源在同分时排前。1.0 = 中性。
_ENGINE_WEIGHTS: Dict[str, float] = {"stepfun": 1.25}


def _normalize_engines(engines: Any) -> Optional[List[str]]:
    """engines 参数归一化与校验：None/空 → None（自动选择）；非法值直接报错。"""
    if engines is None:
        return None
    if isinstance(engines, str):
        engines = [e.strip() for e in engines.split(",") if e.strip()]
    if not isinstance(engines, (list, tuple)):
        raise ValueError("engines 参数必须是列表或逗号分隔的字符串")
    cleaned: List[str] = []
    for e in engines:
        name = (e or "").strip().lower()
        if name not in _ENGINE_FUNCS:
            raise ValueError(f"未知搜索引擎: {e!r}（可用: {', '.join(_ENGINE_FUNCS)}）")
        if name not in cleaned:
            cleaned.append(name)
    return cleaned or None


def _auto_select_engines() -> List[str]:
    """默认引擎：stepfun 为首选（用户偏好，中文质量好且快），无键引擎兜底，已配置密钥的 API 引擎追加。"""
    selected: List[str] = []
    if _stepfun_key():
        selected.append("stepfun")
    selected.extend(["duckduckgo", "bing"])
    for name, env_key in _API_KEY_ENGINES.items():
        if os.environ.get(env_key):
            selected.append(name)
    return selected


# ---------------------------------------------------------------------------
# 合并去重排序
# ---------------------------------------------------------------------------

_TRACKING_PARAM_RE = re.compile(r"^(utm_|ref$|ref_|from|fbclid|gclid)", re.I)


def _norm_url(url: str) -> str:
    """URL 归一化：小写 host、去 www.、去 fragment、去跟踪参数、去尾部 /。"""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return ""
        host = p.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        pairs = [
            (k, v) for k, v in parse_qs(p.query, keep_blank_values=True).items()
            if not _TRACKING_PARAM_RE.match(k)
        ]
        pairs.sort()
        query = urlencode([(k, v[0]) for k, v in pairs])
        path = p.path.rstrip("/")
        return f"{p.scheme.lower()}://{host}{path}" + (f"?{query}" if query else "")
    except Exception:
        return ""


def _merge_and_rank(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """按归一化 URL 去重合并；得分 = Σ各引擎位置分 + 共识加分。"""
    buckets: Dict[str, Dict[str, Any]] = {}
    for it in items:
        url = (it.get("url") or "").strip()
        key = _norm_url(url)
        if not key:
            continue
        engine = it.get("engine") or "?"
        position = int(it.get("position", 0))
        b = buckets.get(key)
        if b is None:
            b = buckets[key] = {
                "url": url, "title": "", "snippet": "",
                "engines": set(), "score": 0.0, "best_pos": 10 ** 9,
            }
        if engine not in b["engines"]:
            b["engines"].add(engine)
            b["score"] += max(1.0, float(limit) - position) * _ENGINE_WEIGHTS.get(engine, 1.0)
        if not b["title"] and it.get("title"):
            b["title"] = it["title"]
        if len(it.get("snippet") or "") > len(b["snippet"]):
            b["snippet"] = it["snippet"]
        b["best_pos"] = min(b["best_pos"], position)
    ranked = sorted(
        buckets.values(),
        key=lambda b: (-(b["score"] + 3.0 * (len(b["engines"]) - 1)), b["best_pos"]),
    )[: max(1, limit)]
    return [
        {
            "rank": i + 1,
            "title": b["title"],
            "url": b["url"],
            "snippet": b["snippet"],
            "engines": sorted(b["engines"]),
            "score": round(b["score"] + 3.0 * (len(b["engines"]) - 1), 1),
        }
        for i, b in enumerate(ranked)
    ]


# ---------------------------------------------------------------------------
# 工具主类
# ---------------------------------------------------------------------------

def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


class SuperSearch:
    """超级搜索工具：多引擎聚合搜索 + 深度检索 + 正文抽取"""

    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        """获取工具元数据"""
        return {
            "tool_id": "super_search",
            "name": "超级搜索工具",
            "description": "多引擎聚合网页搜索（DuckDuckGo/Bing，可选 Tavily/Brave/Serper），"
                           "支持深度检索抓取正文；search 搜摘要，deep_search 连正文一起返回",
            "category": "search",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "search",
                        "description": "多引擎聚合搜索，返回去重排序后的 标题/链接/摘要 列表",
                        "parameters": {
                            "query": "搜索关键词（必填）",
                            "max_results": "返回条数，默认 10，上限 30",
                            "engines": "可选，引擎列表或逗号分隔：duckduckgo,bing,tavily,brave,serper",
                            "timeout": "单引擎超时秒数，默认 15"
                        }
                    },
                    {
                        "name": "deep_search",
                        "description": "搜索后并发抓取前 N 个结果页并抽取正文文本，适合需要深入资料的调研",
                        "parameters": {
                            "query": "搜索关键词（必填）",
                            "fetch_top": "抓取正文的结果条数，默认 3，上限 5",
                            "content_chars": "单页正文保留字符数，默认 4000",
                            "max_results": "搜索返回条数，默认 10",
                            "engines": "可选，同 search",
                            "timeout": "超时秒数，默认 15"
                        }
                    },
                    {
                        "name": "extract_text",
                        "description": "抓取指定网页并抽取正文纯文本（含 SSRF 防护）",
                        "parameters": {
                            "url": "网页 URL（必填，仅 http/https）",
                            "max_chars": "正文保留字符数，默认 8000，上限 50000",
                            "timeout": "超时秒数，默认 20"
                        }
                    }
                ]
            }
        }

    # -- 公共入口（均不抛异常，统一返回结果 dict） ---------------------------

    @staticmethod
    def search(query: str, max_results: int = _DEFAULT_MAX_RESULTS,
               engines: Any = None, timeout: float = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """多引擎聚合搜索：并发单线程逐个调用（引擎内部串行），合并去重排序。"""
        try:
            query = (query or "").strip()
            if not query:
                return {"success": False, "message": "query 不能为空",
                        "error": "Invalid input"}
            limit = _clamp_int(max_results, _DEFAULT_MAX_RESULTS, 1, _MAX_RESULTS_LIMIT)
            timeout = _clamp_int(timeout, _DEFAULT_TIMEOUT, 1, 120)
            selected = _normalize_engines(engines) or _auto_select_engines()

            merged: List[Dict[str, Any]] = []
            engine_stats: Dict[str, Dict[str, Any]] = {}
            for name in selected:
                fn = _ENGINE_FUNCS[name]
                try:
                    items = fn(query, limit, timeout)
                    engine_stats[name] = {"ok": True, "count": len(items)}
                    if not items:
                        engine_stats[name]["note"] = "未解析到结果（可能被反爬拦截）"
                    for pos, item in enumerate(items):
                        merged.append({
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "snippet": item.get("snippet", ""),
                            "engine": name,
                            "position": pos,
                        })
                except Exception as e:
                    engine_stats[name] = {"ok": False, "error": str(e)}

            results = _merge_and_rank(merged, limit)
            engines_failed = {k: v["error"] for k, v in engine_stats.items() if not v["ok"]}
            engines_used = [k for k, v in engine_stats.items() if v["ok"]]

            if not results and not engines_used:
                return {
                    "success": False,
                    "message": "所有搜索引擎均未返回结果（可能被网络环境或反爬拦截）",
                    "error": "All engines failed",
                    "query": query,
                    "results": [],
                    "engine_stats": engine_stats,
                    "engines_failed": engines_failed,
                }

            failed_note = f"；失败引擎: {', '.join(engines_failed)}" if engines_failed else ""
            return {
                "success": True,
                "message": f"搜索完成：{len(results)} 条结果（引擎: {', '.join(engines_used) or '无'}）{failed_note}",
                "query": query,
                "results": results,
                "engine_stats": engine_stats,
                "engines_used": engines_used,
                "engines_failed": engines_failed,
            }
        except Exception as e:
            return {"success": False, "message": f"搜索失败: {e}", "error": str(e)}

    @staticmethod
    def deep_search(query: str, fetch_top: int = 3, content_chars: int = 4000,
                    max_results: int = _DEFAULT_MAX_RESULTS, engines: Any = None,
                    timeout: float = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
        """搜索 + 并发抓取前 N 个页面正文；单页失败/被拦截不影响整体。"""
        try:
            fetch_top = _clamp_int(fetch_top, 3, 1, _DEEP_FETCH_TOP_LIMIT)
            content_chars = _clamp_int(content_chars, 4000, 200, 20000)
            base = SuperSearch.search(
                query=query,
                max_results=max(max_results or _DEFAULT_MAX_RESULTS, fetch_top),
                engines=engines,
                timeout=timeout,
            )
            if not base.get("success") or not base.get("results"):
                base["message"] = f"深度检索中止：{base.get('message', '搜索无结果')}"
                return base

            top = base["results"][:fetch_top]
            timeout = _clamp_int(timeout, _DEFAULT_TIMEOUT, 1, 120)
            with ThreadPoolExecutor(max_workers=min(_DEEP_MAX_WORKERS, len(top))) as pool:
                pages = list(pool.map(
                    lambda r: SuperSearch._fetch_readable(r["url"], timeout, content_chars),
                    top,
                ))
            for result, page in zip(top, pages):
                result.update(page)

            fetched_ok = sum(1 for p in pages if p.get("content"))
            base["message"] = (
                f"深度检索完成：{len(base['results'])} 条结果，"
                f"成功抓取 {fetched_ok}/{len(pages)} 个页面正文"
            )
            base["deep"] = True
            return base
        except Exception as e:
            return {"success": False, "message": f"深度检索失败: {e}", "error": str(e)}

    @staticmethod
    def extract_text(url: str, max_chars: int = 8000,
                     timeout: float = 20) -> Dict[str, Any]:
        """抓取指定网页并抽取正文纯文本（复用 web_fetcher 的 SSRF 防护）。"""
        try:
            url = (url or "").strip()
            if not url:
                return {"success": False, "message": "url 不能为空", "error": "Invalid input"}
            error = _validate_url(url)
            if error:
                return {
                    "success": False,
                    "message": error,
                    "error": "Invalid or blocked URL",
                    "blocked": True,
                }
            max_chars = _clamp_int(max_chars, 8000, 100, 50000)
            timeout = _clamp_int(timeout, 20, 1, 120)
            html_str = _http_fetch(url, timeout=timeout, max_bytes=_MAX_PAGE_BYTES)
            text = _html_to_text(html_str)[:max_chars]
            return {
                "success": True,
                "message": f"正文抽取成功（{len(text)} 字符）",
                "url": url,
                "content": text,
                "page_chars": len(text),
            }
        except Exception as e:
            return {"success": False, "message": f"正文抽取失败: {str(e)}", "error": str(e)}

    # -- 内部 ---------------------------------------------------------------

    @staticmethod
    def _fetch_readable(url: str, timeout: float, max_chars: int) -> Dict[str, Any]:
        """抓取单页并抽取正文；返回带 blocked/page_error/content 标记的页级结果。"""
        error = _validate_url(url)
        if error:
            return {"blocked": True, "page_error": error}
        try:
            html_str = _http_fetch(url, timeout=timeout, max_bytes=_MAX_PAGE_BYTES)
        except Exception as e:
            return {"page_error": str(e)}
        text = _html_to_text(html_str)[:max_chars]
        if not text:
            return {"page_error": "页面未抽取到正文"}
        return {"content": text, "page_chars": len(text)}

    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具指令"""
        inputs = inputs or {}
        if instruction == "search":
            return SuperSearch.search(
                query=inputs.get("query", ""),
                max_results=inputs.get("max_results", _DEFAULT_MAX_RESULTS),
                engines=inputs.get("engines"),
                timeout=inputs.get("timeout", _DEFAULT_TIMEOUT),
            )
        elif instruction == "deep_search":
            return SuperSearch.deep_search(
                query=inputs.get("query", ""),
                fetch_top=inputs.get("fetch_top", 3),
                content_chars=inputs.get("content_chars", 4000),
                max_results=inputs.get("max_results", _DEFAULT_MAX_RESULTS),
                engines=inputs.get("engines"),
                timeout=inputs.get("timeout", _DEFAULT_TIMEOUT),
            )
        elif instruction == "extract_text":
            return SuperSearch.extract_text(
                url=inputs.get("url", ""),
                max_chars=inputs.get("max_chars", 8000),
                timeout=inputs.get("timeout", 20),
            )
        else:
            return {
                "success": False,
                "message": f"未知指令: {instruction}",
                "error": "Invalid instruction"
            }
