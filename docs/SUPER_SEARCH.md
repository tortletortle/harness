# super_search 超级搜索工具

> 2026-08-30 新增的内置工具（category=search）。多引擎聚合搜索 + 深度检索 + 正文抽取，
> 无需任何 API Key 即可使用；遵循 [ERROR_HANDLING.md](ERROR_HANDLING.md) 工具层约定
> （永不抛异常，统一返回 dict；失败必须可见）。

## 能力一览

| 指令 | 用途 | 关键参数 |
|---|---|---|
| `search` | 多引擎聚合搜索，返回去重排序后的 标题/链接/摘要 | `query`（必填）、`max_results`（默认 10，上限 30）、`engines`、`timeout`（默认 15s） |
| `deep_search` | 搜索后并发抓取前 N 个页面并抽取正文 | `fetch_top`（默认 3，上限 5）、`content_chars`（默认 4000，上限 20000）、其余同 search |
| `extract_text` | 抓取指定网页并抽取正文纯文本 | `url`（必填）、`max_chars`（默认 8000，上限 50000）、`timeout`（默认 20s） |

调用方式与其它内置工具一致：`{"tool": "super_search", "inputs": {...}}`；
计划步骤不写 instruction 时，executor 按 inputs 推断（query→search、
fetch_top/content_chars→deep_search、仅 url→extract_text）。

## 搜索引擎

| 引擎 | 密钥（环境变量） | 说明 |
|---|---|---|
| duckduckgo | 无 | html.duckduckgo.com HTML 解析（含 uddg 跳转解包） |
| bing | 无 | www.bing.com `li.b_algo` 块解析 |
| tavily | `TAVILY_API_KEY` | Tavily Search API，返回 content 摘要 |
| brave | `BRAVE_API_KEY` | Brave Search API |
| serper | `SERPER_API_KEY` | Google 结果（serper.dev） |

- 默认（不传 `engines`）：duckduckgo + bing；检测到对应环境变量时自动追加 API 引擎。
- 显式传 `engines: ["tavily", "bing"]`（列表或逗号分隔字符串）可指定引擎；
  未配置密钥的引擎以 EngineError 计入 `engines_failed`，不影响其余引擎。

## 排序与去重

- 归一化 URL 后去重：小写 host、去 `www.`、去 fragment、去 `utm_*`/`ref`/`fbclid`/`gclid` 等跟踪参数。
- 得分 = Σ（引擎内位置分）+ 3 ×（跨引擎共识数 − 1）；同分按最早出现位置稳定排序。
- 返回字段：`rank / title / url / snippet / engines / score`；deep_search 结果额外带
  `content / page_chars` 或 `page_error / blocked`。

## 安全

- **SSRF 防护**：所有出站抓取（extract_text / deep_search 页面抓取）复用
  `web_fetcher._validate_url`——仅允许 http/https，拒绝内网/保留/链路本地地址
  （含云元数据 169.254.169.254）。deep_search 中被拦截的页面**单页**标记 `blocked`，
  不拖垮整体任务。
- **授权集成**：执行链路上 inputs 含 `url` 的步骤受 Runtime Grant `allowed_hosts`
  校验（与 web_fetcher 同规则，见 authorization.py）；`search`/`deep_search`
  输入为 `query`，不涉及 host 白名单。
- **限额**：引擎响应 ≤2MB、单页 ≤5MB、结果 ≤30 条、正文 ≤20000 字符，超限即中断。

## 集成点

- `tools/__init__.py`：注册为 `super_search`；`ToolRegistryAgent.handle_search_request`
  （设计期搜索助手）优先暴露并调用 super_search，无则退回 web_fetcher。
- `executor/__init__.py`：`_infer_instruction` 新增 super_search 映射。
- 设计期搜索的 Grant/审计收编见 [AGENT_HARNESS_DESIGN.md](AGENT_HARNESS_DESIGN.md)
  §23.3（P1-A，待实施；实施时应以 super_search 为搜索载体）。

## 测试

```bash
py -3.11 -m pytest tests/python/test_super_search.py tests/python/test_web_fetcher_security.py -q --no-cov
```

`tests/python/test_super_search.py` 共 22 个用例，全部离线（不访问外网）：
元数据契约、DDG/Bing HTML 解析（含跳转解包）、URL 归一化与共识排序、
未命中 vs 故障区分、SSRF 拦截、本地 HTTP 服务器端到端正文抽取、
deep_search blocked 单页语义、executor 指令推断、注册表契约。
