---
name: daily-standup
description: 生成项目每日进度报告——汇总最近计划执行统计、失败步骤与工作记忆，落盘为 Markdown 日报
version: 1.0.0
entry: scripts/standup.py
runtime: python
timeout: 60
side_effect: reversible
input_schema:
  type: object
  properties:
    days:
      type: integer
      description: 统计最近几天的执行记录（默认 1 = 今天）
---

# daily-standup · 每日进度报告

把项目最近的真实执行记录变成一份可读的日报：

1. 读 `data/eas.db`：最近 N 天计划状态分布、步骤执行分布（工具 × 状态）、最近失败步骤（如实列出）。
2. 读 `PROJECT_MEMORY.md`：工作记忆摘录。
3. 生成 Markdown 日报并写入 `data/reports/standup-<日期时间>.md`，同时在返回值里带全文。

## 调用方式

- 对话助手："帮我生成今天的进度报告" → 助手调用 `skill_daily-standup`。
- 计划步骤：tool 填 `skill_daily-standup`，inputs 可带 `{"days": 3}`。

## 返回结构

```json
{"success": true, "report": "# EAS 每日进度报告 ...", "saved_to": "data/reports/standup-xxx.md"}
```
