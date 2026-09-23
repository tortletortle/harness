"""固定路径解析（python/paths.py）：显式参数 > 环境变量 > 仓库根默认值，且不依赖 cwd。

覆盖数据库文件（EAS_DB_PATH）、日志目录（EAS_LOG_DIR）与缓存文件默认位置。

注意：本文件不得以默认参数实例化 Database()，否则会碰到真实的 data/eas.db。
"""
from pathlib import Path

import pytest

import database as database_mod
from database import Database, get_database
from paths import (
    DATA_DIR, DB_PATH_ENV, DEFAULT_DB_PATH, DEFAULT_LOG_DIR, LOG_DIR_ENV, REPO_ROOT,
    resolve_db_path, resolve_log_dir,
)


def test_repo_root_points_at_repository_not_python_dir():
    assert (REPO_ROOT / "python" / "paths.py").is_file()
    assert (REPO_ROOT / "pytest.ini").is_file()
    assert DATA_DIR == REPO_ROOT / "data"
    assert DEFAULT_DB_PATH == DATA_DIR / "eas.db"
    assert DEFAULT_LOG_DIR == REPO_ROOT / "logs"


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


def test_log_dir_default_is_repo_root_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv(LOG_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    assert resolve_log_dir() == DEFAULT_LOG_DIR
    assert resolve_log_dir().is_absolute()


@pytest.mark.parametrize("blank", ["", "   "])
def test_log_dir_blank_env_falls_back_to_default(monkeypatch, blank):
    monkeypatch.setenv(LOG_DIR_ENV, blank)
    assert resolve_log_dir() == DEFAULT_LOG_DIR


def test_log_dir_env_and_explicit_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv(LOG_DIR_ENV, str(tmp_path / "from-env"))
    assert resolve_log_dir() == tmp_path / "from-env"
    assert resolve_log_dir(tmp_path / "explicit") == tmp_path / "explicit"
    assert resolve_log_dir("relative/logs") == Path("relative/logs")  # 显式值原样透传


def test_cache_defaults_live_under_repo_data_dir(monkeypatch, tmp_path):
    """DesignCache()/PlanCache() 默认文件固定在仓库根 data/，不再跟随 cwd（python/data 残留的另一来源）。"""
    from cache import DesignCache, PlanCache

    monkeypatch.chdir(tmp_path)
    # 只检查类级默认值，不以默认参数实例化，避免读写真实 data/ 下的缓存文件
    assert DesignCache.DEFAULT_CACHE_FILE == DATA_DIR / "design_cache.json"
    assert PlanCache.DEFAULT_CACHE_FILE == DATA_DIR / "plan_cache.json"
    explicit = PlanCache(cache_file=str(tmp_path / "c.json"))
    assert explicit.cache_file == tmp_path / "c.json"


def test_default_caches_follow_isolated_db_path_even_when_env_is_set_late(monkeypatch, tmp_path):
    """隔离 DB 不能读写仓库共享缓存；环境变量可能在 cache 模块导入后设置。"""
    from cache import DesignCache, PlanCache

    isolated_db = tmp_path / "private" / "eas.db"
    monkeypatch.setenv(DB_PATH_ENV, str(isolated_db))
    design = DesignCache()
    plan = PlanCache()
    assert design.cache_file == isolated_db.parent / "design_cache.json"
    assert plan.cache_file == isolated_db.parent / "plan_cache.json"
    design.set("stress-test", {"design_id": "private"})
    plan.set("private", {"plan_id": "private"})
    assert design.cache_file.is_file() and plan.cache_file.is_file()
    assert PlanCache(cache_file=str(tmp_path / "explicit.json")).cache_file == tmp_path / "explicit.json"
