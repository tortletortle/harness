"""
LLM 用量记录与预算检查（P0 强化）

设计（参考 token budget 实践：按 agent/工作流做 token 记账是最直接的成本控制）：
- llm_client.LLMClient.chat 统一钩子：每个 provider 调用后记一行账；
- 上下文通过 ContextVar 传递（异步任务隔离）（phase / plan_id），由调用方
  （server 执行循环、设计端点）在进入/退出时 set/clear；
- 独立 SQLite 库 data/usage/llm_usage.db——不侵入主数据库 schema；
- 记账绝不抛异常、绝不阻断主链路（观察性副作用）。
"""

import sqlite3
import threading
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from tools import workspace as _ws
from logging_config import get_logger

logger = get_logger(__name__)

_local = threading.local()
_context = ContextVar("llm_usage_context", default=None)
_write_lock = threading.Lock()


def set_context(**kwargs: Any) -> None:
    """绑定当前调用的上下文（plan_id / phase 等），直到 clear_context。"""
    ctx = dict(_context.get() or {})
    ctx.update({k: v for k, v in kwargs.items() if v is not None})
    _context.set(ctx)


def clear_context() -> None:
    """清空当前任务的上下文，不影响并发执行或报告任务。"""
    _context.set({})


def get_context() -> Dict[str, Any]:
    return dict(_context.get() or {})


def _db_path() -> Any:
    return _ws.resolve_workspace_root() / "data" / "usage" / "llm_usage.db"


def _get_conn() -> sqlite3.Connection:
    """线程本地连接复用（按库路径切换自动重连）。

    开源标准打法（sqlite.org/wal.html、remusao SQLite perf、better-sqlite3 production）：
    - journal_mode=WAL：写不阻塞读，提交不做全库 fsync；
    - synchronous=NORMAL：WAL 下的推荐档位（性能/安全平衡）；
    - busy_timeout=5000：并发写时等锁而不是立刻报 BUSY；
    - 连接按 (线程, 库路径) 复用——省掉每笔记账的建连开销（强度测试 15ms/行的主要来源）。
    """
    path = str(_db_path())
    cached = getattr(_local, "conn_info", None)
    if cached is not None and cached[0] == path:
        return cached[1]
    if cached is not None:
        try:
            cached[1].close()
        except Exception:
            pass
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_usage ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts TEXT, provider TEXT, model TEXT, phase TEXT, plan_id TEXT, "
        "input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER)"
    )
    _local.conn_info = (path, conn)
    return conn


def close_thread_conn() -> None:
    """显式关闭当前线程的复用连接（测试/进程退出用；不调也由 GC 兜底）。"""
    cached = getattr(_local, "conn_info", None)
    if cached is not None:
        try:
            cached[1].close()
        except Exception:
            pass
        _local.conn_info = None


def _to_int(value: Any) -> int:
    """宽容整数转换：坏字符串/None → 0（记账尽量保留数据，不丢行）。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_tokens(usage: Any) -> tuple:
    """归一化 OpenAI（prompt/completion/total）与 Anthropic（input/output）两种口径。"""
    if usage is None:
        usage = {}
    if not isinstance(usage, dict):
        usage = dict(getattr(usage, "__dict__", {}) or {})
    input_t = _to_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    output_t = _to_int(usage.get("completion_tokens") or usage.get("output_tokens"))
    total = _to_int(usage.get("total_tokens")) or (input_t + output_t)
    return input_t, output_t, total


def record_usage(
    provider: str,
    model: str,
    usage: Any,
    phase: Optional[str] = None,
    plan_id: Optional[str] = None,
) -> None:
    """记录一次 LLM 调用的 token 用量。永不抛异常。"""
    try:
        input_t, output_t, total = _normalize_tokens(usage)
        ctx = get_context()
        phase = phase or ctx.get("phase") or "adhoc"
        plan_id = plan_id or ctx.get("plan_id")
        ts = datetime.now().isoformat(timespec="seconds")
        with _write_lock:
            conn = _get_conn()
            conn.execute(
                "INSERT INTO llm_usage (ts, provider, model, phase, plan_id, "
                "input_tokens, output_tokens, total_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, str(provider or ""), str(model or ""), str(phase),
                 str(plan_id) if plan_id else None, input_t, output_t, total),
            )
            conn.commit()
    except Exception as e:  # 观察性副作用：失败只记日志
        logger.debug("usage 记录失败（忽略）: {}", e)


def plan_total_tokens(plan_id: str) -> int:
    """指定计划累计消耗的 token 总量（无记录返回 0）。"""
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(total_tokens), 0) FROM llm_usage WHERE plan_id = ?",
            (str(plan_id),),
        ).fetchone()
        return int(row[0] or 0)
    except Exception as e:
        logger.debug("usage 查询失败（按 0 处理）: {}", e)
        return 0


def get_summary(plan_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
    """用量汇总 + 最近记录（供 GET /api/usage）。只读；失败抛给调用方。"""
    limit = max(1, min(int(limit or 50), 500))
    conn = _get_conn()
    where = "WHERE plan_id = ?" if plan_id else ""
    params = (str(plan_id),) if plan_id else ()
    row = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), "
        f"COALESCE(SUM(output_tokens), 0), COALESCE(SUM(total_tokens), 0) "
        f"FROM llm_usage {where}",
        params,
    ).fetchone()
    by_phase = [
        {"phase": r[0], "total_tokens": int(r[1] or 0), "calls": int(r[2] or 0)}
        for r in conn.execute(
            f"SELECT phase, SUM(total_tokens), COUNT(*) FROM llm_usage {where} "
            f"GROUP BY phase ORDER BY SUM(total_tokens) DESC",
            params,
        ).fetchall()
    ]
    recent = [
        {
            "ts": r[0], "provider": r[1], "model": r[2], "phase": r[3],
            "plan_id": r[4], "input_tokens": int(r[5] or 0),
            "output_tokens": int(r[6] or 0), "total_tokens": int(r[7] or 0),
        }
        for r in conn.execute(
            f"SELECT ts, provider, model, phase, plan_id, input_tokens, "
            f"output_tokens, total_tokens FROM llm_usage {where} "
            f"ORDER BY id DESC LIMIT {int(limit)}",
            params,
        ).fetchall()
    ]
    return {
        "plan_id": plan_id,
        "calls": int(row[0] or 0),
        "input_tokens": int(row[1] or 0),
        "output_tokens": int(row[2] or 0),
        "total_tokens": int(row[3] or 0),
        "by_phase": by_phase,
        "recent": recent,
    }
