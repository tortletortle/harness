# 归档：旧启动入口

曾堆在仓库根目录 / `scripts/` 的重复、过时启动脚本。现行入口见根 [README.md](../../../README.md)：

- 根目录仅保留：`启动完整系统.bat`、`启动后端.bat`、`启动前端.bat`
- 权威命令：`python scripts/start_api.py` + `cd frontend && npm run dev`
- 辅助：`scripts/restart_api.bat`、`scripts/dev_reload.bat`、`scripts/open-helm.cmd`

| 归档文件 | 问题 |
|----------|------|
| 一键启动.bat | 名似全系统，实际只开 CLI |
| 启动选择.bat / scripts/start.bat / scripts/dev.bat/.sh | 旧栈：9000 (`api.main`) + Rust 8090 |
| 一键测试.bat / 验证配置.bat | 一次性检查菜单 |
| scripts/init.bat/.sh | 早期 venv 初始化，非日常 |
| 开始使用_WEB界面.md | 过时入门稿 |
| harness*_task_*.md / task_three.md / _project.md | 验收涂鸦与个人笔记残留 |

归档日期：2026-10-07。
