"""
工作区路径语义（所有文件类工具的统一约定）。

约定：
- **相对路径** → 解析到工作区根目录。解析优先级：
  1. 执行级覆盖（contextvar，由 Runtime 在计划绑定了隔离 worktree 时设置，
     随 asyncio 任务上下文传播到 to_thread 工具线程，多计划并行互不影响）；
  2. 环境变量 EAS_WORKSPACE_ROOT（进程级默认）；
  3. 仓库根目录。
- **绝对路径** → 原样使用。本系统是自托管环境，信任输入；不做白名单沙箱
  （command_runner 的工作目录白名单属于其自身的安全语义，保持不变）。

executor 的 _infer_instruction 依赖原始 path 字符串推断 create_file /
create_directory，因此解析必须发生在各工具内部（推断之后），不能前移。
"""

import contextvars
import os
from pathlib import Path


# 执行级工作区覆盖：Runtime 在执行绑定 worktree 的计划时设置。
# asyncio.to_thread 会拷贝上下文，工具线程内读取即得到本执行的值。
_workspace_override: contextvars.ContextVar = contextvars.ContextVar("eas_workspace_override", default=None)


def set_workspace_override(root) -> contextvars.Token:
    """设置当前执行上下文的工作区根覆盖；返回 token 供 reset 恢复。"""
    return _workspace_override.set(str(Path(root).resolve()))


def reset_workspace_override(token: contextvars.Token) -> None:
    _workspace_override.reset(token)


def resolve_workspace_root() -> Path:
    """工作区根目录：执行级覆盖 > EAS_WORKSPACE_ROOT > 仓库根。"""
    override = _workspace_override.get()
    if override:
        return Path(override).resolve()
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
