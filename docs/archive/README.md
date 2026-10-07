# 归档文档

这些文档描述的是**当时**的状态，已被更新的文档取代，仅作历史参考，不要按其中的结论理解现状。

当前权威文档留在 `docs/` 根下（如 `STATUS.md`、`AGENT_HARNESS_DESIGN.md`、`API.md`）。  
日期戳报告、旧架构稿、一次性修复/审计脚本与压测证据在本目录。

| 文件 | 当时内容 | 归档原因 |
|------|----------|----------|
| NEW_ARCHITECTURE_DESIGN.md / GUI_BEAUTIFY_RESEARCH.md | 早期架构与 UI 调研 | 主设计以 AGENT_HARNESS_DESIGN 为准 |
| *-20260923*.md / HARDENING_2026-08-30.md / checkpoint-* / daily-task-* / plan-cache-fk-* | 单次验收/修复/压测报告 | 结论已合入代码与 STATUS |
| scripts/repair-20260923 / scripts/audit-20260923 | 一次性运维脚本 | 非日常入口；日常启动见根目录 bat / `scripts/start_api.py` |
| reports/stress-20260923 | 压测原始 JSON | 证据归档 |
| TEST_REPORT_2026-08-24.md | 原根目录 README，实为一次测试行动的报告 | 引用的 test_smoke.py 等脚本已不存在；由新的根 README 取代 |
| TEST_REPORT 配套：QUICK_REFERENCE.md、TROUBLESHOOTING.md | 2026-08-24 测试总结与排障指南 | 围绕已删除的临时测试脚本编写 |
| TEST_COMPLETION_REPORT.md / TEST_COVERAGE_ANALYSIS.md / TEST_FIXES.md | 早期测试工作记录 | 一次性报告，数据已过时 |
| STABILITY_OPTIMIZATION_REPORT.md / PLAN_STABILITY_IMPROVEMENT.md / PLAN_STABILITY_SUMMARY.md | 计划生成稳定性优化记录 | 优化早已合入，报告内容凝结在代码与 STATUS.md 中 |
| CURRENT_STATUS.md / MISSING_FEATURES.md | 2026-08-21 完成度与缺失分析 | 结论严重过时（称"GUI 0%、错误恢复 0%"），由 [../STATUS.md](../STATUS.md) 取代 |
| QUICKSTART.md | 面向 9000/8090 双层旧架构的快速开始 | 架构已改为单后端 8000 + 前端 3000 |
| QUICKSTART_5MIN.md / 使用说明.md / 开箱即用.md | CLI 时代的入门文档 | 称"无图形界面、执行器未实现"，与现实不符；Web 入门见根目录《开始使用_WEB界面.md》 |
| approve2.json / approve3.json / approve_body.json / approve_resp.json / exec_body.json / reject_body.json | 手工调试 API 时的请求体/响应快照 | 一次性调试残留（2026-09-12 由根目录归档，STATUS 短板#5） |
| a.txt / test.py / out.html | `hello` 涂鸦、`print('hello')`、单页 HTML | 实验残留，同上 |
| eas_2_0_ui_platform.tsx | 早期单文件 UI 原型 | 已被 frontend/ 取代，仅作参考 |
| check_db_schema.py / check_plan.py / check_plans.py | python/ 临检脚本 | 全仓 0 引用，由 python/ 归档 |
| server.py.bak | server.py 拆分前备份 | 拆分已完成（3242→545 行），仅留历史 |
| start_backend.bat / launch_server.py / restart.bat | python/ 下的早期后端启动/重启脚本 | 写死旧机器路径 `D:\spot\harness\python`、从 python/ 目录启动导致产生第二份 `python/data`、`python/logs`；restart.bat 还会 `taskkill` 本机所有 >100MB 的 python.exe。全仓 0 引用，由根目录《启动后端.bat》/《重启后端.bat》→ `scripts/start_api.py` 取代（2026-09-19 归档） |

当前有效的文档索引见根目录 [README.md](../../README.md)。
