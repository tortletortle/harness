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
  - `GenerationError` → 502，`{"detail": ..., "code": "generation_failed"}`
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
