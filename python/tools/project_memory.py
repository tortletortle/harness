"""项目工作记忆：把执行状态持久化到工作区，让后续任务能"接着干"。

设计原则（用户定位：只为完成项目服务，不是用户画像）：
- 记录的是工作状态：做了什么、什么状态、失败原因、下一步；
- 存储位置固定：工作区根 PROJECT_MEMORY.md（不接受路径输入，杜绝越权）；
- Markdown 格式：人和 AI 都能读；追加式；读取方取尾部（最近优先）；
- 容量：**条数与字符数双限**（默认 60 条 / 20000 字符），超限时把**最旧的整条**
  滚动到同目录 PROJECT_MEMORY-archive.md（归档默认 2000 条 / 200000 字符）。
  旧实现从头部硬切字符，会留下半截条目——2026-09-19 整理时文件第 1 行就是半条
  `or] Success —— 步骤2`；
- 重复：`collapse_duplicates=True` 时同一身份的记忆覆盖更新而不是追加。身份优先用
  显式 `dedupe_key`（写进标题的括号里，如 `执行摘要（计划 3f7fd751）`），因为执行
  摘要的首行含最终状态、Failed→Completed 会变，不能拿它当键；未给 key 时退化为
  「同标题 + 同首行」匹配。

容量可用环境变量覆盖（进程级，非法值回落默认并告警）：
- `EAS_PROJECT_MEMORY_MAX_CHARS`：本文件字符上限（默认 20000）
- `EAS_PROJECT_MEMORY_MAX_ENTRIES`：本文件保留条数（默认 60）
- `EAS_PROJECT_MEMORY_ARCHIVE_MAX_CHARS`：归档字符上限（默认 200000）
- `EAS_PROJECT_MEMORY_ARCHIVE_MAX_ENTRIES`：归档保留条数（默认 2000）
例如只想保留最近 20 条：`EAS_PROJECT_MEMORY_MAX_ENTRIES=20`。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from logging_config import get_logger
from tools.workspace import resolve_workspace_root

logger = get_logger(__name__)

MEMORY_FILENAME = "PROJECT_MEMORY.md"
ARCHIVE_FILENAME = "PROJECT_MEMORY-archive.md"
_ENTRY_PREFIX = "## ["

# 单条上限（防止单条记忆撑爆上下文）。
_MAX_ENTRY_CHARS = 2000

# 默认容量：条数与字符数双限（环境变量可覆盖，见 _limits()）。
_MAX_FILE_CHARS = 20000
_MAX_FILE_ENTRIES = 60
_MAX_ARCHIVE_CHARS = 200000
_MAX_ARCHIVE_ENTRIES = 2000
_ARCHIVE_KEEP_MARGIN = 400

ENV_MAX_FILE_CHARS = "EAS_PROJECT_MEMORY_MAX_CHARS"
ENV_MAX_FILE_ENTRIES = "EAS_PROJECT_MEMORY_MAX_ENTRIES"
ENV_MAX_ARCHIVE_CHARS = "EAS_PROJECT_MEMORY_ARCHIVE_MAX_CHARS"
ENV_MAX_ARCHIVE_ENTRIES = "EAS_PROJECT_MEMORY_ARCHIVE_MAX_ENTRIES"


def _memory_path():
    return Path(resolve_workspace_root()) / MEMORY_FILENAME


def _archive_path():
    return Path(resolve_workspace_root()) / ARCHIVE_FILENAME


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _int_env(name: str, default: int) -> int:
    """读正整数环境变量；缺失/非法/非正数一律回落默认值并告警。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("忽略非法环境变量 {}={!r}，使用默认 {}", name, raw, default)
        return default
    if value <= 0:
        logger.warning("忽略非正数环境变量 {}={}，使用默认 {}", name, value, default)
        return default
    return value


def _limits() -> Dict[str, int]:
    """每次调用现读，测试与运维都可在运行期调整（环境变量优先于模块常量）。"""
    return {
        "file_chars": _int_env(ENV_MAX_FILE_CHARS, _MAX_FILE_CHARS),
        "file_entries": _int_env(ENV_MAX_FILE_ENTRIES, _MAX_FILE_ENTRIES),
        "archive_chars": _int_env(ENV_MAX_ARCHIVE_CHARS, _MAX_ARCHIVE_CHARS),
        "archive_entries": _int_env(ENV_MAX_ARCHIVE_ENTRIES, _MAX_ARCHIVE_ENTRIES),
    }


def _split_entries(text: str) -> Tuple[str, List[str]]:
    """切成（前导区, 条目列表）。条目以 `## [` 开头。

    按**整条**切分是"不产生半截记录"的前提：滚动与归档裁剪都只在条目边界动刀。
    """
    preamble: List[str] = []
    entries: List[List[str]] = []
    for line in (text or "").splitlines(keepends=True):
        if line.startswith(_ENTRY_PREFIX):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
        else:
            preamble.append(line)
    return "".join(preamble), ["".join(block) for block in entries]


def _preamble(dropped: int, when: str, limits: Dict[str, int]) -> str:
    return (
        "# 项目工作记忆（PROJECT_MEMORY.md）\n\n"
        f"> 更早的记忆已按整条边界滚动归档到 {ARCHIVE_FILENAME}"
        f"（最近归档 {when}，本次 {dropped} 条）；本文件保留最近 "
        f"{limits['file_entries']} 条 / {limits['file_chars']} 字符以内的条目，读取方取尾部。\n\n"
    )


def _archive_preamble(kept: int, when: str, limits: Dict[str, int]) -> str:
    return (
        f"# 项目记忆归档（{ARCHIVE_FILENAME}）\n\n"
        f"> 归档同样按整条边界滚动：超过容量上限时丢弃最旧的条目"
        f"（当前上限 {limits['archive_entries']} 条 / {limits['archive_chars']} 字符，"
        f"可用 {ENV_MAX_ARCHIVE_ENTRIES} / {ENV_MAX_ARCHIVE_CHARS} 调整）。\n"
        f"> 最近归档 {when}；当前保留 {kept} 条。\n\n"
    )


def _header_title_and_key(block: str) -> Tuple[str, str]:
    """从 `## [ts] 标题（键）` 里取（标题, 键）；无键时键为空串。"""
    header = block.split("\n", 1)[0]
    _, _, rest = header.partition("] ")
    rest = rest.strip()
    if rest.endswith("）") and "（" in rest:
        title, _, key = rest.rpartition("（")
        return title.strip(), key[:-1].strip()
    return rest, ""


def _first_body_line(block: str) -> str:
    _, _, rest = block.partition("\n")
    for line in rest.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _matches(block: str, title: str, dedupe_key: str, first_line: str) -> bool:
    block_title, block_key = _header_title_and_key(block)
    if block_title != title:
        return False
    if dedupe_key:
        return block_key == dedupe_key
    return bool(first_line) and _first_body_line(block) == first_line


def _keep_newest(entries: List[str], budget: int, max_entries: int) -> List[str]:
    """从最新往回保留：条数与字符数双限；最新一条无论多大都保留（否则会丢现场）。"""
    max_entries = max(1, int(max_entries))
    keep: List[str] = []
    used = 0
    for block in reversed(entries):
        if keep and (len(keep) >= max_entries or used + len(block) > budget):
            break
        keep.append(block)
        used += len(block)
    keep.reverse()
    return keep


def _append_archive(blocks: List[str], when: str) -> int:
    """把整条记忆滚动进归档文件（同目录）；归档本身也按整条 + 双限滚动。"""
    if not blocks:
        return 0
    limits = _limits()
    path = _archive_path()
    combined = _read_text(path) + "".join(blocks)
    _, entries = _split_entries(combined)
    keep = _keep_newest(
        entries,
        max(1, limits["archive_chars"] - _ARCHIVE_KEEP_MARGIN),
        limits["archive_entries"],
    )
    path.write_text(_archive_preamble(len(keep), when, limits) + "".join(keep), encoding="utf-8")
    return len(blocks)


def save_entry(
    title: str,
    content: str,
    collapse_duplicates: bool = False,
    dedupe_key: str = "",
) -> Dict[str, Any]:
    """追加一条工作记忆（供 executor 在计划结束时调用，也暴露为工具指令）。

    collapse_duplicates=True 时，同一身份的记忆覆盖更新：
    - 给了 dedupe_key：匹配「同标题 + 同键」的条目（键会写进标题，如
      `执行摘要（计划 3f7fd751）`）；
    - 没给 key：退化为匹配「同标题 + 同首行」。
    容量双限（条数 / 字符数）超限时，最旧的整条滚动进归档文件。
    """
    title = str(title or "").strip() or "执行记录"
    content = str(content or "").strip()
    if not content:
        return {"success": False, "message": "缺少 content", "error": "Missing content"}
    content = content[:_MAX_ENTRY_CHARS]
    dedupe_key = str(dedupe_key or "").strip()
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    header_title = f"{title}（{dedupe_key}）" if dedupe_key else title
    entry = f"\n## [{ts}] {header_title}\n{content}\n"

    limits = _limits()
    path = _memory_path()
    preamble, entries = _split_entries(_read_text(path))

    replaced = False
    if collapse_duplicates:
        first_line = content.split("\n", 1)[0].strip()
        for index, block in enumerate(entries):
            if _matches(block, title, dedupe_key, first_line):
                entries[index] = entry
                replaced = True
                break
    if not replaced:
        entries.append(entry)

    archived = 0
    text = preamble + "".join(entries)
    too_big = len(text) > limits["file_chars"]
    too_many = len(entries) > limits["file_entries"]
    if too_big or too_many:
        budget = max(1, limits["file_chars"] - len(_preamble(len(entries), ts, limits)))
        keep = _keep_newest(entries, budget, limits["file_entries"])
        dropped = entries[: len(entries) - len(keep)]
        archived = _append_archive(dropped, ts)
        text = _preamble(len(dropped), ts, limits) + "".join(keep)
        entries = keep
    path.write_text(text, encoding="utf-8")

    logger.info("项目记忆已记录: {}{}", header_title, "（覆盖旧条目）" if replaced else "")
    result: Dict[str, Any] = {
        "success": True,
        "message": f"已记录: {header_title}",
        "path": str(path),
        "retained": len(entries),
    }
    if replaced:
        result["replaced"] = True
    if archived:
        result["archived"] = archived
    return result


def recall(limit_chars: int = 4000) -> Dict[str, Any]:
    """读取项目记忆（最近优先）。无记忆时返回占位（不算失败）。"""
    path = _memory_path()
    if not path.exists():
        return {"success": True, "content": "（暂无项目记忆）", "empty": True}
    text = _read_text(path)
    limit = max(200, int(limit_chars))
    content = text if len(text) <= limit else "...[早期记忆已截断]\n" + text[-limit:]
    return {"success": True, "content": content}


class ProjectMemory:
    """项目工作记忆工具"""

    @staticmethod
    def get_metadata() -> Dict[str, Any]:
        return {
            "tool_id": "project_memory",
            "name": "项目工作记忆",
            "description": "记录/读取项目执行状态（工作区 PROJECT_MEMORY.md），让后续任务能接着干，而不是从零开始",
            "category": "other",
            "version": "1.2.0",
            "capabilities": {
                "instructions": [
                    {
                        "name": "save",
                        "description": "追加一条工作记忆（标题 + 内容）；超限时按整条滚动归档（条数/字符双限，环境变量可调）",
                        "parameters": {
                            "title": "条目标题（如：重构进度）",
                            "content": "工作状态/结论/下一步（必填）",
                            "collapse_duplicates": "同一身份覆盖旧条目而非追加（可选，默认 false）",
                            "dedupe_key": "折叠身份键，写进标题括号（可选，如 计划 3f7fd751）",
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
        collapse = bool(inputs.get("collapse_duplicates", False))
        key = inputs.get("dedupe_key", "")
        if instruction in ("save", "write", "append"):
            return save_entry(inputs.get("title"), inputs.get("content"),
                              collapse_duplicates=collapse, dedupe_key=key)
        if instruction in ("recall", "read"):
            try:
                limit = int(inputs.get("limit_chars", 4000))
            except (TypeError, ValueError):
                limit = 4000
            return recall(limit)
        if instruction == "execute":
            # Capability 统一入口的缺省指令：按参数形状分派
            # （有 content → 保存；否则 → 读取）。
            if "content" in inputs:
                return save_entry(inputs.get("title"), inputs.get("content"),
                                  collapse_duplicates=collapse, dedupe_key=key)
            try:
                limit = int(inputs.get("limit_chars", 4000) or 4000)
            except (TypeError, ValueError):
                limit = 4000
            return recall(limit)
        return {"success": False, "message": f"未知指令: {instruction}", "error": "Unknown instruction"}
