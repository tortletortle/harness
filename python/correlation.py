"""
Correlation ID（P2 可观测性）

一次"因果链"共享同一个 correlation id：
- HTTP 请求：middleware 从 X-Correlation-ID 头读取（无则生成），响应头回传；
- 后台执行：execute_plan_background 以 run-{run_id} 绑定整条执行链路；
- 日志：logging_config 的 loguru patcher 自动把当前值写进每条日志的 trace= 字段，
  业务代码无需显式传递。

实现基于 contextvars，天然隔离并发协程/线程，兼容 asyncio 任务继承语义。
"""

import uuid
from contextvars import ContextVar

_correlation_id: ContextVar[str] = ContextVar("eas_correlation_id", default="-")


def new_correlation_id() -> str:
    """生成短 correlation id（12 hex，足够在单实例日志里唯一）。"""
    return uuid.uuid4().hex[:12]


def get_correlation_id() -> str:
    """当前上下文的 correlation id；未绑定时为 "-"（与日志占位一致）。"""
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    """绑定 correlation id 到当前上下文（HTTP 请求或后台任务开头调用一次）。"""
    _correlation_id.set(value or "-")

