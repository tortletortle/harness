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
