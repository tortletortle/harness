# code_context 代码上下文与知识检索工具

> 2026-08-30 新增的内置工具（category=search）。参考 Aider repo map、CrewAI 长期记忆、
> LangGraph 记忆分层与 SWE-agent ACI 原则实现的轻量版（零新依赖，仅 Python 标准库）。
> 遵循 [ERROR_HANDLING.md](ERROR_HANDLING.md) 工具层约定（永不抛异常，统一返回 dict）。

## 能力一览

| 指令 | 用途 | 关键参数 |
|---|---|---|
| `repo_map` | 工作区仓库地图：文件 + 关键符号（类/函数/方法/标题）按引用频次排名，字符预算内输出 | `max_chars`（默认 4000，500~20000）、`paths`（限定子目录，可选） |
| `search` | 全文检索工作区文件与知识笔记，返回 文件/行号/摘录 | `query`（必填）、`max_results`（默认 8，上限 30） |
| `reindex` | 增量重建全文索引（按内容哈希，只处理变化文件） | `force`（全量重建，可选） |
| `remember` | 把经验/结论写入知识笔记并入索引，跨任务可检索 | `note`（必填）、`title`（可选） |

调用方式与其它内置工具一致：`{"tool": "code_context", "inputs": {...}}`。
executor 的 `_infer_instruction` 映射：`query`→search、`note`/`content`→remember、
`force`→reindex、其余→repo_map。

## 设计来源（开源借鉴）

| 能力 | 参考项目 | 本实现取舍 |
|---|---|---|
| 仓库地图 | Aider（tree-sitter AST + PageRank 排序 + token 预算） | Python 用 stdlib `ast` 提取类/函数/签名；JS/TS/Rust/Markdown 用正则；引用频次计数近似图排序；`max_chars` 字符预算 |
| 全文检索 | SQLite FTS5 本地检索模式（无向量、零部署） | FTS5 + `bm25()` 排序 + `snippet()` 摘录；FTS5 不可用时自动退回 LIKE 扫描 |
| 长期记忆 | CrewAI 长期记忆（SQLite）、LangGraph（长期记忆与运行状态分离） | `remember` 写 `data/knowledge/notes/*.md` 并入索引，`search` 可跨任务检索 |
| 检索输出 | SWE-agent ACI 原则（搜索输出保持简洁） | 每条命中只返回 路径/行号/≤300 字符摘录，不倾倒整块上下文 |

## 索引与排除规则

- 索引库存放 `data/knowledge/fts.db`（首次 search 自动建索引；reindex 增量按内容哈希）。
- 排除目录：`.git`、`node_modules`、`__pycache__`、`venv/.venv`、`dist/build/target`、
  `htmlcov`、各类缓存目录、`logs`、`data/**`（**例外**：`data/knowledge/notes/` 笔记始终索引）。
- 排除文件：`.env*`、`api_token`、锁文件；仅索引文本扩展名；单文件 ≤512KB；
  索引文件数上限 4000（repo_map 扫描 1500）。

## 安全

- 只读工作区文件 + 写 `data/knowledge/`，无网络、无命令执行；路径基于
  `tools.workspace.resolve_workspace_root()`（`EAS_WORKSPACE_ROOT` 优先，否则仓库根）。
- 工具永不抛异常；检索无命中返回 `success=true` + 空结果 + 重建索引提示（未命中 ≠ 故障）。

## 测试

```bash
py -3.11 -m pytest tests/python/test_code_context.py -q --no-cov
```

15 个离线用例（临时工作区，不触碰真实仓库内容）：元数据契约、repo_map 符号提取与
预算截断、paths 过滤、增量 reindex、node_modules 排除、FTS5 与 LIKE 双引擎检索、
remember 写入与可检索性、executor 指令推断、注册表契约。
