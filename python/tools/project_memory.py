"""项目工作记忆：把执行状态持久化到工作区，让后续任务能"接着干"。

设计原则（用户定位：只为完成项目服务，不是用户画像）：
- 记录的是工作状态：做了什么、什么状态、失败原因、下一步；
- 存储位置固定：工作区根 PROJECT_MEMORY.md（不接受路径输入，杜绝越权）；
- Markdown 格式：人和 AI 都能读；追加式；读取方取尾部（最近优先）；
- 内容截断：单条 2000 字符、文件 20000 字符，防止撑爆上下文。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from logging_config import get_logger
from tools.workspace import resolve_workspace_root

logger = get_logger(__name__)

MEMORY_FILENAME = "PROJECT_MEMORY.md"
_MAX_FILE_CHARS = 20000
_MAX_ENTRY_CHARS = 2000


def _memory_path():
    return Path(resolve_workspace_root()) / MEMORY_FILENAME


def _tail(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else "...[早期记忆已截断]\n" + text[-limit:]


def save_entry(title: str, content: str) -> Dict[str, Any]:
    """追加一条工作记忆（供 executor 在计划结束时调用，也暴露为工具指令）。"""
    title = str(title or "").strip() or "执行记录"
    content = str(content or "").strip()
    if not content:
        return {"success": False, "message": "缺少 content", "error": "Missing content"}
    content = content[:_MAX_ENTRY_CHARS]
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n## [{ts}] {title}\n{content}\n"
    path = _memory_path()
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")
    merged = existing + entry
    if len(merged) > _MAX_FILE_CHARS:
        merged = _tail(merged, _MAX_FILE_CHARS)
    path.write_text(merged, encoding="utf-8")
    logger.info("项目记忆已记录: {}", title)
    return {"success": True, "message": f"已记录: {title}", "path": str(path)}


def recall(limit_chars: int = 4000) -> Dict[str, Any]:
    """读取项目记忆（最近优先）。无记忆时返回占位（不算失败）。"""
    path = _memory_path()
    if not path.exists():
        return {"success": True, "content": "（暂无项目记忆）", "empty": True}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"success": True, "content": _tail(text, max(200, int(limit_chars)))}


class ProjectMemory:
    """项目工作记忆工具"""

    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        return {
            "tool_id": "project_memory",
            "name": "项目工作记忆",
            "description": "记录/读取项目执行状态（工作区 PROJECT_MEMORY.md），让后续任务能接着干，而不是从零开始",
            "category": "other",
            "version": "1.0.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "save",
                        "description": "追加一条工作记忆（标题 + 内容）",
                        "parameters": {
                            "title": "条目标题（如：重构进度）",
                            "content": "工作状态/结论/下一步（必填）",
                        },
                    },
                    {
                        "name": "recall",
                        "description": "读取项目记忆（最近优先）",
                        "parameters": {
                            "limit_chars": "最多返回的字符数（可选，默认 4000）",
                        },
                    },
                ]
            },
        }

    @staticmethod
    def execute(instruction: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        inputs = inputs or {}
        if instruction in ("save", "write", "append"):
            return save_entry(inputs.get("title"), inputs.get("content"))
        if instruction in ("recall", "read"):
            try:
                limit = int(inputs.get("limit_chars", 4000))
            except (TypeError, ValueError):
                limit = 4000
            return recall(limit)
        return {"success": False, "message": f"未知指令: {instruction}", "error": "Unknown instruction"}
