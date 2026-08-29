"""
pytest 全局配置

python/ 目录下的模块互相之间使用顶层导入（例如 `from models import ...`，
不是 `from python.models import ...`），因为它们原本被设计为
`sys.path` 已包含 `python/` 目录时运行（参考 api/server.py 的
`sys.path.insert(0, str(Path(__file__).parent.parent))`）。

为了让 tests/ 下的测试可以直接 `from designer import DefaultDesigner`
而不必逐个文件手写 sys.path hack，这里在会话级别统一插入路径。
"""

import sys
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parent.parent / "python"
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))
