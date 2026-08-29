"""
工作区路径语义（所有文件类工具的统一约定）。

约定：
- **相对路径** → 解析到工作区根目录。工作区根由环境变量 EAS_WORKSPACE_ROOT
  指定，未设置时回落到仓库根目录（与 command_runner 的历史行为一致）。
- **绝对路径** → 原样使用。本系统是自托管环境，信任输入；不做白名单沙箱
  （command_runner 的工作目录白名单属于其自身的安全语义，保持不变）。

executor 的 _infer_instruction 依赖原始 path 字符串推断 create_file /
create_directory，因此解析必须发生在各工具内部（推断之后），不能前移。
"""

import os
from pathlib import Path


def resolve_workspace_root() -> Path:
    """工作区根目录：EAS_WORKSPACE_ROOT 优先，否则仓库根。"""
    root = os.environ.get("EAS_WORKSPACE_ROOT")
    if root:
        return Path(root).resolve()
    return Path(__file__).resolve().parent.parent.parent


def resolve_workspace_path(path) -> Path:
    """把工具输入的路径解析为实际路径。

    绝对路径原样返回；相对路径基于工作区根解析（不 resolve 符号链接，
    保持与用户输入一致的形式，由调用方决定是否需要 resolve）。
    """
    p = Path(path)
    if p.is_absolute():
        return p
    return resolve_workspace_root() / p
