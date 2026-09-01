---
name: notion-archive
description: 把执行报告/交付物归档到 Notion 数据库的做法指导（配合 notion MCP 工具使用，纯知识包无脚本）
version: 1.0.0
---

# notion-archive · 报告归档到 Notion（知识包）

本技能没有可执行脚本——它教对话助手与 Designer 在需要"把报告存到 Notion"时怎么做。

## 何时用

- 用户要求"把这份报告/日报归档到 Notion"、"同步到我的 Notion 数据库"。
- 计划执行产出了 Markdown 报告（如 `data/reports/standup-*.md`），需要长期沉淀。

## 怎么做

1. **定位目标**：调用 notion MCP 的 `API-post-search`（按标题/关键词搜页面或数据库），
   找到目标数据库的 `database_id`；找不到就让用户给链接（从 URL 提取 id）。
2. **创建页面**：调用 `API-post-page`，parent 填 `{ "type": "database_id", "database_id": "<id>" }`，
   properties 按目标数据库的列结构填（至少 title），正文把 Markdown 报告分段转成
   paragraph / heading 块数组。
3. **确认结果**：检查返回的 `url`，如实回报给用户。

## 注意

- Notion token 在 `config/models.local.yaml` 的 `mcp_servers.notion.env.OPENAPI_MCP_HEADERS`
  里配置；未配置有效 token 时调用会 401——如实报告，不要伪造成功。
- notion MCP 工具属于 external 类能力：对话助手默认工具白名单会排除 external 类，
  归档操作请走正式计划流程（有审批）或在对话中明确授权。
- 工具清单以运行时 `tools/list` 为准（当前 24 个），不要凭记忆硬编码工具名。
