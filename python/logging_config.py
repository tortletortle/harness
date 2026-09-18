"""
统一日志配置（阶段4.1）

替换项目里散落的 print() 为结构化、分级、可按大小轮转的日志。

用法：
    from logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("消息")
    logger.warning("警告: {}", detail)
    logger.error("失败: {}", err)

设计要点：
- 单一 loguru sink 写入 logs/eas.log，按 10MB 轮转、保留 5 个历史文件、自动压缩，
  避免旧版 execution_debug.log 手写 append 导致的无限增长问题。
- 同时保留一份控制台输出（stderr），级别 INFO，方便本地开发直接看到日志。
- trace_id（阶段4.2）通过 loguru 的 contextvars-based `bind()` 机制注入，
  未绑定 trace_id 时格式化为 "-"，不会报错。
"""

import sys
from pathlib import Path

from loguru import logger

_configured = False


def _resolve_log_dir() -> Path:
    """定位 logs/ 目录：优先当前工作目录下的 logs/，不存在则退回 python/ 所在目录的上一级。"""
    candidates = [Path("logs"), Path(__file__).resolve().parent.parent / "logs"]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    # 兜底：当前目录
    fallback = Path(".")
    return fallback


def configure_logging(log_dir: Path = None) -> None:
    """初始化全局 loguru sink。多次调用是安全的（只会真正配置一次）。"""
    global _configured
    if _configured:
        return

    log_dir = log_dir or _resolve_log_dir()
    log_path = log_dir / "eas.log"

    logger.remove()  # 移除 loguru 默认的 stderr sink，换成下面自定义格式

    fmt = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | trace={extra[trace_id]} | "
        "{name}:{function}:{line} - {message}"
    )

    def _inject_trace_id(record):
        # correlation ID（P2）：每条日志自动带上当前上下文的 correlation id，
        # 显式 bind(trace_id=...) 的值优先于上下文值。
        # 注意 configure(extra=...) 会给每条记录预置 trace_id="-"，
        # 因此按"仍是占位值"判断，而不是按 key 是否存在。
        if record["extra"].get("trace_id", "-") == "-":
            from correlation import get_correlation_id

            record["extra"]["trace_id"] = get_correlation_id()

    logger.configure(extra={"trace_id": "-"}, patcher=_inject_trace_id)

    # 控制台输出：本地开发直接可见
    logger.add(sys.stderr, level="INFO", format=fmt, enqueue=False)

    # 文件输出：按大小轮转 + 保留历史 + 压缩，替代手写 execution_debug.log 的无限增长问题
    logger.add(
        str(log_path),
        level="DEBUG",
        format=fmt,
        rotation="10 MB",
        retention=5,
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )

    _configured = True


def get_logger(name: str = None):
    """获取 logger。若尚未配置全局 sink，会自动配置一次。

    loguru 会自动记录调用点所在的模块名（{name} 字段），无需手动传入 name 参数；
    保留 name 形参仅为了兼容 `get_logger(__name__)` 这种常见调用习惯。
    """
    configure_logging()
    return logger


# 模块加载时立即初始化，保证任何 `from logging_config import get_logger` 之后直接可用
configure_logging()
