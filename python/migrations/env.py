"""Alembic 迁移环境。

设计约束：
- 数据库路径与运行时一致：默认 data/eas.db，环境变量 EAS_DB_PATH 优先。
- 不使用 SQLAlchemy 模型自动生成（运行时是手写 sqlite3 SQL），
  所有迁移必须手写 upgrade/downgrade，并在迁移内保持幂等防御。
- SQLite 大多数 ALTER 受限，改列/改约束请使用 batch_alter_table
  或"新表-拷贝-换名"模式，参见 docs/DB_MIGRATIONS.md。
"""
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    """与 python/database.py 的默认路径保持一致；EAS_DB_PATH 优先。"""
    db_path = os.environ.get("EAS_DB_PATH", "data/eas.db")
    return f"sqlite:///{db_path}"


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

