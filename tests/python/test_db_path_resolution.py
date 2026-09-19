"""数据库路径解析（python/paths.py）：显式参数 > EAS_DB_PATH > 仓库根 data/eas.db，且不依赖 cwd。

注意：本文件不得以默认参数实例化 Database()，否则会碰到真实的 data/eas.db。
"""
from pathlib import Path

import pytest

import database as database_mod
from database import Database, get_database
from paths import DB_PATH_ENV, DEFAULT_DB_PATH, REPO_ROOT, resolve_db_path


def test_repo_root_points_at_repository_not_python_dir():
    assert (REPO_ROOT / "python" / "paths.py").is_file()
    assert (REPO_ROOT / "pytest.ini").is_file()
    assert DEFAULT_DB_PATH == REPO_ROOT / "data" / "eas.db"


def test_default_is_absolute_and_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv(DB_PATH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)  # 模拟从任意目录（例如 python/）启动服务
    resolved = Path(resolve_db_path())
    assert resolved.is_absolute()
    assert resolved == DEFAULT_DB_PATH
    assert not resolved.is_relative_to(tmp_path)


def test_env_override_wins_over_default(monkeypatch, tmp_path):
    custom = tmp_path / "custom.db"
    monkeypatch.setenv(DB_PATH_ENV, str(custom))
    assert resolve_db_path() == str(custom)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_env_falls_back_to_default(monkeypatch, blank):
    monkeypatch.setenv(DB_PATH_ENV, blank)
    assert resolve_db_path() == str(DEFAULT_DB_PATH)


def test_explicit_argument_wins_and_is_passed_through_untouched(monkeypatch, tmp_path):
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "env.db"))
    explicit = tmp_path / "explicit.db"
    assert resolve_db_path(str(explicit)) == str(explicit)
    assert resolve_db_path(explicit) == str(explicit)      # PathLike 同样接受
    assert resolve_db_path(":memory:") == ":memory:"       # 特殊值必须原样透传，不能 resolve 成文件路径
    assert resolve_db_path("relative/dir/x.db") == "relative/dir/x.db"


@pytest.mark.parametrize("empty", ["", "   "])
def test_explicit_blank_argument_behaves_like_omitted(monkeypatch, tmp_path, empty):
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "env.db"))
    assert resolve_db_path(empty) == str(tmp_path / "env.db")


def test_database_default_constructor_honours_env_and_creates_parent_dir(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "dir" / "eas.db"
    monkeypatch.setenv(DB_PATH_ENV, str(target))
    db = Database()
    assert db.db_path == str(target)
    assert target.is_file()
    assert db.list_plans() == []


def test_database_explicit_path_ignores_env(monkeypatch, tmp_path):
    monkeypatch.setenv(DB_PATH_ENV, str(tmp_path / "env.db"))
    explicit = tmp_path / "explicit.db"
    db = Database(str(explicit))
    assert db.db_path == str(explicit)
    assert explicit.is_file()
    assert not (tmp_path / "env.db").exists()


def test_get_database_singleton_uses_same_resolution(monkeypatch, tmp_path):
    target = tmp_path / "singleton.db"
    monkeypatch.setenv(DB_PATH_ENV, str(target))
    monkeypatch.setattr(database_mod, "_db_instance", None)  # 不污染其它测试的全局单例
    db = get_database()
    assert db.db_path == str(target)
    assert get_database() is db


def test_alembic_env_shares_runtime_resolver(monkeypatch, tmp_path):
    """migrations/env.py 的 URL 必须来自同一个解析器，而不是自己再读一遍环境变量/写死相对路径。"""
    source = (REPO_ROOT / "python" / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "from paths import resolve_db_path" in source
    assert 'os.environ.get("EAS_DB_PATH"' not in source
    assert 'sqlite:///data/eas.db' not in source
