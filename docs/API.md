# EAS 2.0 API 文档

后端唯一入口是 `python/api/server.py`，默认 **http://127.0.0.1:8000**。

> 注意：`python/api/main.py` 是遗留的 9000 端口旧入口，已不在主链路上，不要按它开发。

## 认证

除 `GET /` 外，所有端点都需要 token，两种方式任选：

```http
Authorization: Bearer <token>
X-API-Token: <token>
```

token 来源优先级：环境变量 `EAS_API_TOKEN` → `config/api_token` 文件内容
（首次启动无任何来源时自动生成并写入该文件）。校验为常数时间比较；
失败返回 401。WebSocket 无法自定义请求头，用查询参数 `?token=<token>` 校验。

## 错误语义

- **401** token 缺失/无效
- **404** 资源不存在（设计书/计划）
- **409** 状态冲突：已有任务在执行、暂停非 Running/Pending、恢复非 Paused、终态计划跳步等
- **500** 服务端错误；数据库故障返回 `{"code": "database_error"}`，
  LLM 生成失败返回 502 `{"code": "generation_failed"}`
- 所有响应失败时 detail 为中文错误信息；日志通过 `/ws/logs` 广播

## 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 健康检查（无需 token） |
| GET | `/api/status` | 系统状态：providers、工具数、当前执行 |
| POST | `/api/design` | 生成设计书，body: `{"user_request": "..."}` |
| GET | `/api/designs?limit=10` | 设计书列表（摘要） |
| GET | `/api/design/{id}` | 设计书详情 |
| POST | `/api/design/{id}/approve` | 审批，body: `{"approved": bool, "feedback": "..."}`；批准→生成计划，拒绝+反馈→AI 修订设计书 |
| GET | `/api/plans?limit=10` | 执行计划列表 |
| GET | `/api/plan/{id}` | 计划详情（含 `current_branch`、`resume_from_step_id`；每个步骤含 `depends_on`、`branch_version` 和 `outputs`） |
| POST | `/api/plan/{id}/execute` | 开始执行；已有任务在跑返回 409 |
| POST | `/api/plan/{id}/pause` | 请求暂停（当前步骤完成后生效），同时原子写入恢复断点 |
| POST | `/api/plan/{id}/resume` | 从断点恢复，仅限 Paused 状态 |
| POST | `/api/plan/{id}/skip-step/{step_id}` | 跳过步骤；终态计划或非 Pending/Retry 步骤返回 409 |
| POST | `/api/plan/{id}/steps/{step_id}/input` | 向等待补充输入的步骤写回 `user_input`（仅 Paused 计划中恢复断点对应的 Pending 且 `outputs.needs_input=true` 的步骤，否则 409） |
| POST | `/api/plan/{id}/rollback` | 回滚到指定步骤，body: `{"step_id": "...", "strategy": "retry"}`；克隆出新分支并废弃旧分支步骤 |
| GET | `/api/audit-logs?limit=100&plan_id=` | 审计日志（工具调用记录，含被安全拦截的调用） |
| GET | `/api/tools` | 工具列表（10 个内置工具及能力） |
| GET | `/api/config` | 配置（密钥只返回是否已配置，不回显明文） |
| POST | `/api/config` | 保存密钥/地址到 `config/models.yaml`，重启后生效 |
| POST | `/api/chat` | AI 助手对话，body: `{"message": "...", "history": [{"role","content"}]}` |
| POST | `/api/restart` | 请求服务进程重启 |
| WS | `/ws/logs?token=` | 实时日志流（JSON 消息，含 level/content） |

## 调用示例

```bash
TOKEN=$(cat config/api_token)

# 生成设计书
curl -X POST http://127.0.0.1:8000/api/design \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_request": "制作一个待办事项应用"}'

# 审批（批准后自动生成执行计划）
curl -X POST http://127.0.0.1:8000/api/design/<design_id>/approve \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"approved": true}'

# 开始执行
curl -X POST http://127.0.0.1:8000/api/plan/<plan_id>/execute \
  -H "Authorization: Bearer $TOKEN"
```

执行进度通过 `GET /api/plan/{id}` 轮询或前端页面查看；日志事件由 `/ws/logs` 推送。
计划详情的步骤 `inputs` 来自步骤表的最新运行态（包括提交后的 `user_input`），
不会改写计划保存时的 `plans.segments` 设计快照。

## 智能体任务交接（handoff）

依赖步骤之间由运行时自动交接，不靠计划里预先写死的 inputs：

- **交接内容**：取当前分支上「本步骤直接依赖」的落库结果——`compressed_context`
  摘要优先，其次 `outputs` / `state_snapshot` 里的实际输出、`agent_facts`、
  `file://` 产物引用与错误。注入到 agent 步骤的 `inputs["upstream_context"]`，
  并同步填进 `CapabilityRequest.artifact_refs`。只给 `kind=agent_tool` 的步骤注入，
  普通工具的 inputs 保持原契约；交接只传引用，不会因此扩大 Grant 的资源面。
  交接过程落 `step_event`：`handoff_context`。
- **产物登记**：agent 结果被 executor 包了一层（`result["output"]["agent_artifacts"]`、
  `result["output"]["output"]["path"]`），登记器按 `result → output → output.output`
  逐层收集，避免 agent 产物永远登记不到。
- **等待补充输入**：agent 返回 `needs_input` 时不判 Failed、不自动重试；步骤回到
  `Pending`，计划转 `Paused` 并写入 `resume_from_step_id`，`step_event` 记
  `needs_input`。用户补充输入：

  ```bash
  curl -X POST http://127.0.0.1:8000/api/plan/<plan_id>/steps/<step_id>/input \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"input": {"repo_url": "https://example.invalid/repo"}}'
  ```

  也可在执行计划页查看提问并填写补充信息。再手动点击「恢复执行」或
  `POST /api/plan/{plan_id}/resume`，调度器从同一步继续（提交输入本身不启动执行）。
- **最终交付报告**：`_generate_final_report` 生成前重新读取数据库中的计划与各步骤
  结果，不使用认领执行时的计划快照，避免报告里出现 Pending / 空 outputs。
