"""Alembic 迁移环境。

设计约束：
- 数据库路径与运行时共用 python/paths.resolve_db_path：EAS_DB_PATH 优先，
  否则仓库根 data/eas.db（不依赖当前工作目录）。
- 不使用 SQLAlchemy 模型自动生成（运行时是手写 sqlite3 SQL），
  所有迁移必须手写 upgrade/downgrade，并在迁移内保持幂等防御。
- SQLite 大多数 ALTER 受限，改列/改约束请使用 batch_alter_table
  或"新表-拷贝-换名"模式，参见 docs/DB_MIGRATIONS.md。
"""
from logging.config import fileConfig
from pathlib import Path
import sys

from sqlalchemy import engine_from_config, pool

from alembic import context

# alembic.ini 的 prepend_sys_path = python 已把 python/ 放进 sys.path；
# 这里再兜底一次，避免从其它目录以 -c 指定 alembic.ini 调用时导入失败。
_PYTHON_DIR = str(Path(__file__).resolve().parents[1])
if _PYTHON_DIR not in sys.path:
    sys.path.insert(0, _PYTHON_DIR)
from paths import resolve_db_path  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    """与运行时完全一致（python/paths.resolve_db_path）：EAS_DB_PATH 优先，否则仓库根 data/eas.db。"""
    return f"sqlite:///{resolve_db_path()}"


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

