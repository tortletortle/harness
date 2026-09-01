# 技能包机制（生态层）

> 设计文档：`docs/AGENT_HARNESS_DESIGN.md`（内核）/ `docs/TOOL_INTERFACE_DESIGN.md`（工具接口）。
> 本文定义 skills/ 目录的技能包格式与加载行为。实现：`python/skills_loader.py`。

## 定位

技能包 = "SKILL.md 说明 + 可选脚本" 的目录化能力扩展，是 ToolRegistry 地基上的楼：

- **入口技能**（有 `entry`）：注册成普通工具（`tool_id = skill_<目录名>`）——
  计划步骤可执行、Designer 工具评估可见、对话助手可调用，统一走 Grant 授权与审计。
- **知识包**（无 `entry`）：只进技能清单与对话助手系统提示词，提供做法指导（如 notion-archive 教怎么归档）。

## 目录格式

```
skills/
└── <skill-id>/                # 目录名即技能 ID（sanitize 后用于 tool_id）
    ├── SKILL.md               # 必需：YAML frontmatter + 正文说明
    └── scripts/xxx.py         # entry 指向的脚本（可选）
```

## SKILL.md frontmatter

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 否 | 显示名（缺省用目录名） |
| `description` | **是** | 一句话说明（进工具目录与提示词，缺省即加载失败） |
| `version` | 否 | 默认 1.0.0 |
| `entry` | 否 | 脚本相对路径；缺省 = 知识包 |
| `runtime` | 否 | `python`（默认，用当前解释器）/ `node` / `cmd`（直接可执行文件） |
| `timeout` | 否 | 脚本超时秒数（默认取 config `skills.timeout`，再默认 120） |
| `side_effect` | 否 | `none`（默认）/ `reversible` / `external`；external 会进 external 类被对话白名单排除 |
| `input_schema` | 否 | OpenAI function parameters 格式的入参 schema |

## 执行协议

脚本从 **stdin** 收 `{"instruction": "...", "inputs": {...}}`，向 **stdout** 输出 JSON：

```json
{"success": true, "report": "...", "saved_to": "data/reports/xxx.md"}
```

- `success: false` 时尽量带 `error`（诚实失败，禁止伪造成功）。
- 非 JSON 输出按原文返回（截断 8000 字符），不视为失败。
- 脚本工作目录 = 技能目录；项目根可用 `EAS_REPO_ROOT` 环境变量或按脚本位置向上推导。
- entry 越出技能目录 → 加载/执行失败（防路径逃逸）。

## 生态层现状（2026-09-01）

| 项 | 状态 |
|----|------|
| Skills 机制 | ✅ 本文档所述：SkillManager + ToolRegistry/Capability 双注册 + GET /api/skills |
| MCP 根因修复 | ✅ notion tools/list 超时的根因是 asyncio 流默认 64KB 行上限（实测响应 76KB/6s 正常到达），放宽到 16MB；自愈重启复位 _closed、TimeoutError 也重试 |
| notion MCP | ✅ 已启用（tools/list 24 个工具）；有效 token 需在 models.local.yaml 配置，未配置时调用 401 如实失败 |
| 第一批技能 | ✅ daily-standup（入口）/ notion-archive（知识包） |
