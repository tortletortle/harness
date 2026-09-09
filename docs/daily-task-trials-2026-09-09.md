# Harness 真实日常任务试跑记录（2026-09-09）

## 目标

验证 Harness 能通过真实 LLM 完成连续的本地日常任务，并检查设计 → 审批 → 执行 → 验收 → 历史记录链路。

## 运行环境

- 后端：`127.0.0.1:8000`
- LLM：LongAI OpenAI-compatible endpoint，凭据仅通过临时进程环境变量注入，未写入仓库
- API token：本地 `config/api_token`，值不记录
- 工具注册数：16（包含运行时 Skills/MCP 能力；默认内置工具为 15）

## 成功任务

| 类型 | 计划 ID | 结果 | 证据 |
|---|---|---|---|
| Markdown 文件创建 | `ef3319f7-5924-46ac-8c38-1beb1cc9f03d` | Completed | `out/daily-task-2.md`，file_exists/content_contains 均通过 |
| 文件创建 + 命令/语法验证 | `fdad6fdb-2059-41fd-a304-2290b1990524` | Completed | `out/daily-task-3b.py`，file_creator + lint_gate 通过 |
| Git 历史摘要报告 | `afbf579b-af42-4157-a943-72fc9f145cfd` | Completed | `out/daily-task-4-git-summary.md`，报告大小 405 bytes |
| 文件创建 + pytest | `f5fc61e2-0f7c-4317-8286-0cdbc6095458` | Completed | `out/daily-task-3c.py`，test_runner 明确 suite=pytest，7 tests passed |
| 文档检查报告 | `c885530d-decd-4d34-a6a8-659e43cdc13a` | Completed | `out/daily-task-7-links.md`，file_creator + lint_gate 通过 |
| Python 测试文件 + pytest | `ad9b4e2d-6a26-430b-a42a-d3acc990c21a` | Completed | `out/daily-task-9-test.py`，test_runner exit_code=0，1 passed |

## 失败与恢复观察

- `c884224e-77b4-4640-8a5f-6b2966e3b7ea`：真实设计生成成功，但首步 `project_memory` 的 LLM 计划验收标准要求人工确认，执行 Failed。
- `d831b7c3-8108-44e8-b64e-7b405430e406`：设计生成成功，但 `test_runner` 缺少明确 `suite`，执行 Failed。
- `aa6a9014-77e7-41fd-858a-184ff50579c9`：首步 file_creator 成功，test_runner 因无 pytest 测试用例失败；同一计划再次执行仍 Failed。该记录证明了失败 checkpoint 与重试入口存在，但不证明恢复成功。
- `20269259-f770-448c-b35a-68e013d0d55f`：生成的计划把预期失败命令改写成 file_creator，因此直接 Completed，不能作为真实 checkpoint 恢复证据。

## 当前结论

已证明真实 LLM 下至少 6 类日常任务可以完成，且每次任务都经过设计、审批、执行和确定性验收。尚未满足最终目标的全部验收条件：

1. 真实失败 checkpoint 的“第一次失败、第二次恢复成功”尚未成功复现；
2. LLM 仍可能生成缺少 `test_runner.suite` 或把 command_runner 改写成其他工具的计划；
3. 因此不能声称连续 5 次全部无需人工救火，也不能声称失败定位 ≤ 2 次点击已被完整验证。
