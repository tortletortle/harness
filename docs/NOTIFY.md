# IM 通知通道（入口层）

> 目标：harness 在"任务完成 / 失败 / 暂停 / 需要审批 / 设计书就绪"时**主动找你**，
> 而不是让你开 Web 轮询。实现：`python/notify.py`；接线：`python/api/server.py`。

## 事件清单

| 事件 | 触发时机 |
|------|----------|
| `plan_completed` | 计划终态 Completed（断点续跑补完也覆盖） |
| `plan_failed` | 计划终态 Failed |
| `plan_paused` | 计划 Paused（含 token 预算超限自动暂停） |
| `approval_required` | 高风险工具执行前挂起等待人工批准 |
| `design_ready` | 设计书生成完成、等待审批 |

所有计划状态写入点都经过 `broadcast_plan_update`（单一收口），通知挂在其内——
状态机怎么演进（执行/续跑/回滚/跳步）都不会漏发。

## 配置通道

在 `config/models.yaml` 有 `notify:` 段（默认全部空 = 仅本地审计）。
**建议在 `config/models.local.yaml`（gitignored）覆盖 webhook**，避免泄漏：

```yaml
notify:
  feishu:
    webhook: https://open.feishu.cn/open-apis/bot/v2/hook/<你的token>
    secret: <机器人开了签名校验才需要>
```

| 通道 | 说明 |
|------|------|
| `feishu` | 飞书群自定义机器人；可选加签（secret） |
| `dingtalk` | 钉钉群自定义机器人；可选加签 |
| `wecom` | 企业微信群机器人 |
| `telegram` | Bot token + chat_id（需能直连 api.telegram.org） |
| `webhook` | 通用：POST `{event, title, text, payload}` JSON |

## 行为契约

- **fail-open**：任何通道失败只记日志与本地审计，绝不打断执行循环或端点成功路径。
- **防轰炸**：同 `(event, dedup_key)` 在 `cooldown_seconds`（默认 300s）内只发一次；
  计划状态以 `(plan_id, 状态)` 去重——同一计划反复 Paused/Running 不会刷屏。
- **本地审计**：每次发送（含失败）追加 `data/notifications.jsonl`；未配置任何通道时
  这是唯一出口，机制照常可验证。
- **连通性自检**：`POST /api/notify/test`（带 token）→ 向所有已配置通道发测试消息，
  返回逐通道结果。

## 快速开始

1. 飞书群里添加"自定义机器人"→ 拿到 webhook → 填进 `models.local.yaml` 的 notify 段。
2. 重启后端（或 `POST /api/restart`）。
3. `curl -X POST -H "X-API-Token: <token>" http://127.0.0.1:8000/api/notify/test` → 群里收到消息即打通。

## 生态层现状（2026-09-01）

| 项 | 状态 |
|----|------|
| 通知器 + 5 通道适配 | ✅ 本文所述 |
| 三个事件挂点 | ✅ broadcast_plan_update（计划）/ 工具审批 / 设计书就绪 |
| 入站指令（IM 里回"批准"） | ⬜ 下一阶段：双向通道（飞书卡片按钮 / Telegram callback） |
