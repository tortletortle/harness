"""仓库内固定路径的唯一解析入口（目前只负责数据库文件路径）。

历史问题：api/server.py 用 cwd 相对路径（data/eas.db，找不到再回退 ../data/eas.db），
migrations/env.py 则读 EAS_DB_PATH，两者并不一致——从 python/ 目录启动服务会在
python/data/ 下产生第二份数据库，而 Alembic 迁移的库与服务实际使用的库可能不是同一个。

统一规则（server / database / migrations 三处共用）：
1. 显式传入的路径优先，并且原样透传（测试与脚本传临时库、":memory:" 等特殊值）；
2. 其次环境变量 EAS_DB_PATH；
3. 否则固定为仓库根目录下的 data/eas.db —— 基于 __file__ 定位，不依赖启动目录，
   与 config/、.env、workspace 根的解析方式保持一致。

本模块刻意不依赖任何项目内模块与第三方库，以便 Alembic env.py 轻量导入。
"""
import os
from pathlib import Path
from typing import Optional, Union

DB_PATH_ENV = "EAS_DB_PATH"

# python/paths.py -> python/ -> 仓库根
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "eas.db"


def resolve_db_path(explicit: Optional[Union[str, os.PathLike]] = None) -> str:
    """按 显式参数 > EAS_DB_PATH > 仓库根 data/eas.db 的顺序解析数据库路径。

    显式参数与环境变量的值不做任何改写（不 resolve、不 expanduser），
    保持与 sqlite3.connect 完全一致的语义；只有默认值是绝对路径。
    """
    if explicit is not None and str(explicit).strip():
        return str(explicit)
    env_value = os.environ.get(DB_PATH_ENV, "").strip()
    if env_value:
        return env_value
    return str(DEFAULT_DB_PATH)
