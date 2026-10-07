# 错误处理约定（Error Handling Conventions）

> 本文是各层错误处理的**规范**。2026-08 重构后生效，新增代码必须遵守；
> 违反本文的写法（尤其是"吞错不报"）属于缺陷。

## 总原则

1. **失败必须可见**：任何失败要么沿调用链上抛、要么以显式失败形态返回，禁止"吞掉后假装成功"。
2. **区分"未命中"与"故障"**：查不到数据是正常业务结果（返回 None/False/[]）；连接损坏、SQL 失败是故障（必须上抛）。二者不得混为一谈。
3. **检查返回值是强制的**：跨层调用凡约定可能返回失败标志，调用方必须检查；无法信任调用方自觉的关键路径，统一走带检查的封装。
4. **错误信息面向人**：面向用户的错误消息用中文、说明"发生了什么"；面向日志的用 `logger.exception` 保留堆栈。

## 各层约定

### 数据库层（`python/database.py`）

| 情形 | 行为 |
|---|---|
| SQL 失败、连接损坏等意外异常 | `logger.exception` 后 **raise `DatabaseError`**（携带操作上下文，`from e` 保留原因） |
| 写操作未命中任何行（rowcount=0） | 返回 `False`（正常业务结果，不是故障） |
| 非法状态值（如 `status="Bogus"`） | 拒绝写入，返回 `False` + warning 日志 |
| 读操作未命中（计划/步骤不存在） | 返回 `None` / `[]` |
| 读操作 SQL 失败 | raise `DatabaseError` |

- `False` / `None` / `[]` **只表示未命中**，绝不再表示故障。
- 调用方对写方法的 `False` 仍必须处理（见 API 层 `_require_write`）。

### 工具层（`python/tools/*.py`）

- 工具**永不抛异常**，统一返回 `{"success": bool, "message": str, "error": str, ...}` dict。
- 工具内部的子进程/网络失败一律转成 dict，禁止把异常泄漏到执行器。
- 工具输出不得伪造成功：无法验证时用 `check_status: "unverified"`，不冒充 `passed`。

### 生成器层（`python/designer`）

- LLM 调用保留内建重试；**重试耗尽后必须 `raise GenerationError`**，禁止返回占位设计书 / 原设计 / manual 失败计划来"假装成功"。
- 非致命降级允许且需明示：如工具信息不可用时使用占位文案并 `logger.warning`——这是功能降级，不是错误冒充。
- dispatcher 等内部协作者失败由 designer 捕获转换，不让裸异常穿透到无关层。

### API 层（`python/api/server.py`）

- 可预期业务冲突：`HTTPException(409, detail="中文原因")`；资源不存在：404；参数问题：400。
- 全局异常处理器：
  - `DatabaseError` → 500，`{"detail": ..., "code": "database_error"}`
  - `GenerationError` → 默认 502，`{"detail": ..., "code": "generation_failed"}`。
  - 原因链为结构化 `LLMRequestError` 时：上游限流 → 429 / `llm_rate_limited`；其他暂时性网络/服务故障 → 503；上游权限或模型不可用 → 502（不是本 API 的鉴权失败）。可用时传递 `Retry-After`，响应不回显上游原文或密钥。
  - 设计/审批端点不得用 catch-all 把 `GenerationError` 或现有 `HTTPException` 重包装成 500。
- 其余未预期异常：端点 catch-all 中先 `logger.exception` 再 `HTTPException(500, detail=str(e))`，保证服务端日志有堆栈。
- 响应格式约定：成功 `{"success": true, ...}`；失败 `{"detail": "...", "code": "..."}`（HTTP 4xx/5xx）。前端两套都要解析。

### 执行循环（`execute_plan_background`）

- 所有状态写入（Running/Paused/Completed/Failed、step 事件）必须通过 `_require_write(ok, what)` 检查；`False` 视同故障上抛，交给外层统一把计划标记 Failed 并释放执行锁。
- 步骤结果遵循"成功 → 重试 → 最终失败"的判定顺序；`unverified` 不阻断计划（历史语义，见代码注释）。

### 前端（`frontend/src`）

- `api/client.ts`：非 2xx 读取响应体的 `detail`（FastAPI 错误格式）抛 `Error`；2xx 中 `success === false` 同样抛。
- 组件 catch 后**必须 `addLog({ level: 'error', ... })`**——禁止只写 `console.error`（用户看不到）。
- `error` 级日志自动进入全局 toast（`ToastHub`，5 秒自动消失，上限 5 条，同文案去重）；日志面板保留完整历史。
- 组件不自行弹错误 UI（AiPanel 的聊天错误气泡除外）。

## 已知的例外（不在本次规范化范围）

- `python/api/main.py`：遗留的旧入口（9000 端口，scripts/dev*.sh|bat 使用），未纳入新约定，逐步淘汰。
- Rust 侧错误体系另行规范。

## 违规案例存档（引以为戒）

以下真实缺陷均源于违反本约定，已由回归测试钉住：

| 缺陷 | 违反的原则 |
|---|---|
| `clone_steps_from` 主键冲突被吞，回滚谎报成功 | 总原则 1 / DB 层 |
| `list_plans(limit)` 位置参数错，读失败吞成 `[]` | DB 层 / 总原则 2 |
| 跳过只改原 id，活动克隆照常执行且无报错 | 总原则 3 |
| LLM 生成失败返回"待定"假设计书，HTTP 200 | 生成器层 |
| 恢复后 UI 永远停在"已暂停" | 前端层（状态无属主） |


## 2026-09-23 执行、交付报告与模型恢复

- 执行成功必须先完成 Run、清理授权/审批并释放所属 reservation，之后才广播完成并进行可选 LLM 报告 I/O。报告耗时不再阻塞下一计划；旧任务的回调不得释放新任务的 token。
- 报告任务按 Run 保存不可变计划快照；`pending → running → completed / failed / interrupted`。取消要记录 `interrupted` 并继续抛出 `CancelledError`，不能伪装成成功。
- 服务启动只恢复已完成 Run 的未完成报告（最多总计两次尝试）；确定失败不无限重试。关停先等待执行/报告任务，超时取消并收拢，再关闭 MCP 子进程及管道。
- 默认报告超时 180 秒、关停宽限 15 秒，可在模型配置根节点 `reports.timeout_seconds` / `reports.shutdown_grace_seconds` 覆盖。
- `/api/artifacts` 的 `artifacts` 只包含可交付产物；内部报告任务另列于 `report_jobs`，不暴露完整执行快照，也不能通过产物写接口创建或覆盖。
- 设计对话和最终报告遇到可恢复调用失败时优先切换已配置备用模型；最后一条路由最多重试一次。遵守数值及 HTTP-date 的 `Retry-After`；超过 5 秒的等待不提前重试，而是返回可重试错误。401/403/404 可尝试另一条配置路由，但不对同一失效权限路由反复重试；400 不重试；取消始终传播。
- OpenAI-compatible 同 endpoint/key 在本进程内默认最多 2 个并发 HTTP 请求，环境变量 `EAS_LLM_MAX_CONCURRENCY` 可调整。此限制**不覆盖其他进程/应用**，不能保证供应商永不返回 429。
- 用量上下文使用 ContextVar 隔离并发任务；报告使用独立的 `report` phase，不覆盖下一次执行的 plan_id。报告只读冻结快照，用量落在原主工作区，绝不在已合并/删除的执行 worktree 中重新建目录。

## 验证入口

```powershell
# 默认离线后端回归；生命周期子服务不读取真实模型密钥或启动外部 MCP。
python -m pytest tests/python -m "not integration"
# pytest 中需要真实模型时必须显式 --live-models（可能产生费用）。
# 本次真实验收工具不属于 pytest，始终调用真实配置的模型：
python docs/archive/scripts/repair-20260923/verify_live.py ownership
python docs/archive/scripts/repair-20260923/verify_live.py shutdown

# 仅诊断，不修改全局 PATH 或安装软件：
powershell -ExecutionPolicy Bypass -File scripts/build-rust.ps1 -Doctor
# 自动选择 MSVC Developer 环境或 GNU + Zig；运行本次 cargo 返回的测试程序：
powershell -ExecutionPolicy Bypass -File scripts/build-rust.ps1 -Offline -SkipRelease
# 发布构建去掉 -SkipRelease；显式选择可用工具链：-Toolchain msvc / gnu。
```

`config/models.local.yaml` 是 gitignored 本机覆盖；其中 stepfun_chat 暂改为已验证可用的 qwen3.8-max / glm-5.2。此变更没有给 step-* 模型新增供应商权限，也没有变更密钥。其他机器应独立验证权限后配置，不应假定同一账号配额或授权可迁移。
