# 版本管理规范

> 自 v0.2.0（2026-09-19）起执行。

## 版本号

语义化版本 `MAJOR.MINOR.PATCH`。0.x 阶段：功能里程碑升 MINOR，修复升 PATCH。

## 单一版本源（发版时需要同步改的地方）

| 位置 | 作用域 |
|---|---|
| `Cargo.toml` `[workspace.package] version` | 全部 Rust crate（成员用 `version.workspace = true`） |
| `python/version.py` `__version__` | 全部 Python（其余模块一律 import，禁止硬编码） |
| `python/pyproject.toml` `[project] version` | Python 包元数据（与 version.py 保持一致） |
| `frontend/package.json` `version` | 前端 |

以上各处必须一致。根 `package.json` 无版本号（仅依赖桩）。

## Git 溯源注入（Rust 产物）

`rust/server/build.rs` 和 `rust/gui/build.rs` 在编译期注入
`git describe --tags --always --dirty` 与提交日期：

```
eas-server.exe --version
# eas-server 0.2.0 (v0.2.0-3-gabc1234, 2026-09-19)
```

- `-dirty` 后缀 = 构建时工作区有未提交改动，**发布件不允许带 dirty**
- 无 .git 环境（如 CI 浅克隆异常）退化为 `unknown`，不阻塞构建

## 发版流程

1. 更新三处版本号 + `CHANGELOG.md` 增加条目
2. 全量验证：Python 回归 / vitest / cargo test + clippy
3. 提交后打 annotated tag 并推送：
   ```bash
   git tag -a v0.X.Y -m "vX.Y: 一句话摘要"
   git push origin main --tags   # 有远端时
   ```
4. tag 之后构建 release 产物，`--version` 输出应显示干净的 `v0.X.Y`
5. 产物哈希与版本记录到 `out/release-*.md`

## CHANGELOG 规则

- 面向使用者写"变了什么"，不是 git log 的搬运
- 分类：Added / Changed / Fixed / Security / Removed
- 未发布的改动累积在 `## [Unreleased]` 段（发版时改名）

