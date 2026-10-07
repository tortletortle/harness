# 2026-08-30 强化实施记录（基于开源调研）

> 本轮依据对 OpenHands / SWE-agent / LangGraph / CrewAI / Aider / MetaGPT 的调研，
> 落地了路线图中的 P0 两项与 P1 两项。全部改动通过全量回归（418 passed / 7 skipped / 0 failed）。

## 已落地

### 1. LLM 用量记账 + 计划级 token 预算闸门（P0）

- 新增 `python/utils/usage_tracker.py`：独立 SQLite（`data/usage/llm_usage.db`），
  记录 ts/provider/model/phase/plan_id/input/output/total tokens；
  兼容 OpenAI（prompt/completion）与 Anthropic（input/output）两种口径；
  记账永不抛异常、绝不阻断主链路。
- `llm_client.LLMClient.chat` 统一钩子：所有 provider 的调用自动记账（一处钩子覆盖全部）。
- 执行循环绑定上下文：`execute_plan_background` 进入时 `set_context(plan_id, phase="executor")`，
  finally 清除；执行期所有 LLM 调用自动归到该计划名下。
- 预算闸门：`config/models.yaml` 新增
  ```yaml
  budget:
    max_tokens_per_plan: 0   # 0 = 不限制
  ```
  执行循环每步前检查（与 Paused 检查同位）：超限 → 计划自动转 Paused（WS 广播提示），
  可在调高上限后 `/resume` 继续。
- 新端点 `GET /api/usage?limit=&plan_id=`：总量汇总 + 按阶段分布 + 最近记录。

### 2. 计划状态 WebSocket 推送（P0，由并行提交实现，此处入档）

- `broadcast_plan_update` 覆盖全部状态写入点（15 处），载荷带自增版本号防乱序，
  摘要来自 `Database.get_plan_status_summary()`；前端 30s 轮询保留为兜底。

### 3. ACI 硬化：语法门禁 + 空输出确认（P1，参考 SWE-agent）

- 新增 `python/tools/lint_gate.py`：`.py` 写入门禁——
  新内容语法错误且原内容合法（或新建）→ 拒绝写入（`blocked: true`）；
  原内容本来就坏 → 放行但带 `syntax_warning`（允许修复中间态）；
  `force_write: true` 显式跳过。
- `text_editor`（write_file/append_to_file/replace_in_file/insert_at_line）
  与 `file_creator.create_file` 全部接入门禁（v1.1.0）。
- `command_runner`：命令成功但无输出时返回
  "命令执行成功且无输出（已显式确认）" + `empty_output: true`
  （SWE-agent ACI 第四原则，v1.1.0）。

### 4. 命令沙箱化：Docker 模式（P1，参考 OpenHands Runtime）

- `command_runner` 显式 opt-in：`EAS_SANDBOX=docker` 启用；
  工作区挂载 `/workspace`、`EAS_SANDBOX_NETWORK`（默认 none 最严格）、
  `EAS_SANDBOX_IMAGE`（默认 python:3.11-slim）。
- **fail-closed**：启用沙箱但 Docker 不可用 → 直接拒绝执行（`blocked: true`），不静默回退宿主机。
- 未启用时行为与历史完全一致（默认关闭，向后兼容）。

## P2 上下文编排（2026-08-30 下午追加落地）

- 新增 `python/knowledge/injection.py`：
  - `build_design_memory_block(user_request)`：需求关键词 → code_context 检索（FTS5/LIKE）
    + 最近知识笔记 → ≤2000 字符记忆块；
  - `build_repo_map_block()`：仓库地图 ≤2500 字符（Aider repo map 模式）。
- **Designer.generate_design**：系统提示词在工具目录后注入【相关历史经验与代码检索】
  （无命中整段省略；`asyncio.to_thread` 防阻塞；fail-open 任何失败只降级）。
- **CodingAgentTool._build_prompt**：单轮修改提示词注入【仓库地图】——
  coding_agent 首次"看得到全仓库签名"，减少盲改（Aider 核心收益）。
- 安全口径：进程内纯读、无网络、排除 .env*/api_token；注入与 code_context 一致
  排除 data/**（笔记目录除外）。
- 验证：tests/python/test_knowledge_injection.py 11 个离线用例
  （记忆块/地图块/fail-open/Designer 提示词接线/coding_agent 提示词接线）。

## 用量记账吞吐优化（2026-08-30 晚间追加）

强度测试暴露：并发记账 1000 行耗时 ~15s（每笔开新 SQLite 连接 + 全局锁 + 每提交 fsync）。
按开源标准打法重写 `utils/usage_tracker.py`（v2）：

- **WAL**（`PRAGMA journal_mode=WAL`）：写不阻塞读、提交不做全库 fsync
  （sqlite.org/wal.html；PowerSync/better-sqlite3 生产配置同款）；
- **synchronous=NORMAL + busy_timeout=5000**：WAL 推荐档位，并发写等锁而非报 BUSY；
- **线程本地连接复用**：省掉每笔记账的建连开销，按库路径切换自动重连（测试隔离安全）；
- 参考模式：OpenTelemetry BatchSpanProcessor（后台缓冲/定量刷写）——若未来需要
  更高吞吐可再加队列化批量落盘，当前 WAL+复用已足够。

强度对比（同一测试、同机）：

| 指标 | v1 | v2 |
|---|---|---|
| 并发记账 1000 行 / 20 线程 | 14.98s | **0.18s（83x）** |
| 垃圾输入风暴 120 次 | 1.52s | **0.02s（76x）** |
| 31 查询并发检索 / 8 线程 | 1.60s | **0.45s（3.5x）** |
| 强度套件总耗时 | 23.2s | **4.5s** |

## 未落地（后续路线图）

- **P2 记忆编排**：Designer 生成设计书前注入 `code_context` 检索结果与最近 remember 笔记
  （designer 链路当前有并行改动在途，刻意避开）。
- **P2 repo_map 接入 coding_agent**：组装提示词时自动附仓库地图。
- **P3 杂项清理**：`python/api/main.py` 旧入口、`server.py.bak`、根目录杂物。

## 兼容性说明

- 写入类 wrapper 在 `force_write=False`（默认）时**不向 impl 传额外参数**，
  保证现有 monkeypatch 假实现的超时测试不受影响（test_retry_policy 套件验证）。
- 用量库为独立文件，不触碰主数据库 schema；`budget.max_tokens_per_plan: 0` 默认关闭预算。

## 验证

```bash
$ py -3.11 -m pytest tests/python/test_usage_tracker.py tests/python/test_lint_gate.py -q --no-cov
26 passed

$ py -3.11 -m pytest tests/python -q --no-cov
418 passed, 7 skipped, 0 failed
```
