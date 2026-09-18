"""EAS 2.0 版本单一来源。

规则（docs/VERSIONING.md）：
- 发版时唯一需要手改的 Python 侧版本号就在这里；
- 与根 Cargo.toml 的 workspace version、package.json 保持一致；
- 其余模块一律 `from version import __version__`，禁止再硬编码。
"""

__version__ = "0.2.0"

