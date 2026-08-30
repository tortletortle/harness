"""
知识注入层（P2 记忆编排）—— 把 code_context 的检索能力编排进 agent 提示词。

- build_design_memory_block(user_request)：Designer 生成设计书前，检索与需求相关的
  知识笔记/历史经验/代码位置，注入设计上下文（CrewAI 记忆注入模式）。
- build_repo_map_block(max_chars)：coding_agent 组装提示词时附仓库地图
  （Aider repo map 模式），让单轮代码修改"看得到全仓库签名"。

约定：
- 纯读 + fail-open：任何失败返回 None，绝不阻断设计/执行主流程；
- 进程内直调（不走 Grant/审计）：只读工作区文本，无网络、无宿主写
  （code_context 已排除 .env*/api_token 等敏感文件）；
- 调用方用 asyncio.to_thread 包装，避免阻塞事件循环（首次调用会建索引）。
"""

import re
from pathlib import Path
from typing import List, Optional

_MAX_KEYWORDS = 6
_SEARCH_RESULTS = 4
_RECENT_NOTES_LIMIT = 2
_NOTE_EXCERPT_CHARS = 140

# 高频但无检索价值的词（极简停用表；中文按 2-6 字连续串切分后过滤）
_STOPWORDS = {
    "一个", "制作", "实现", "生成", "需要", "帮我", "请帮", "系统", "使用", "进行",
    "支持", "功能", "应用", "项目", "文件", "代码", "这个", "那个", "以及", "还有",
    "并且", "然后", "可以", "能够", "应该", "需要", "请求", "内容", "信息",
}

_TOKEN_RE = re.compile(r"[A-Za-z_][\w]{1,}|[\u4e00-\u9fff]{2,6}")


def _keywords(user_request: str) -> str:
    """从需求文本提取检索关键词（去重、去停用词、限量）。"""
    seen = set()
    picked: List[str] = []
    for token in _TOKEN_RE.findall(user_request or ""):
        token = token.strip()
        if len(token) < 2 or token.lower() in seen or token in _STOPWORDS:
            continue
        seen.add(token.lower())
        picked.append(token)
        if len(picked) >= _MAX_KEYWORDS:
            break
    return " ".join(picked)


def _recent_notes(root: Path, exclude_paths: set) -> List[str]:
    """最近的知识笔记（按修改时间倒序），返回格式化行。"""
    lines: List[str] = []
    try:
        notes_dir = root / "data" / "knowledge" / "notes"
        if not notes_dir.exists():
            return lines
        notes = sorted(notes_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        for note in notes[:_RECENT_NOTES_LIMIT]:
            try:
                rel = note.relative_to(root).as_posix()
                if rel in exclude_paths:
                    continue
                head = note.read_text(encoding="utf-8", errors="replace")[:160].replace("\n", " ")
                lines.append(f"- {rel}: {head}")
            except Exception:
                continue
    except Exception:
        pass
    return lines


def build_design_memory_block(user_request: str, max_chars: int = 2000) -> Optional[str]:
    """为设计书生成检索相关记忆/代码位置；无命中或任何失败返回 None。"""
    try:
        query = _keywords(user_request)
        if not query:
            return None

        from tools.code_context import CodeContext  # 延迟导入，避免循环依赖

        lines: List[str] = []
        exclude: set = set()
        result = CodeContext.search(query, max_results=_SEARCH_RESULTS)
        if result.get("success") and result.get("results"):
            for r in result["results"][:_SEARCH_RESULTS]:
                exclude.add(r.get("path"))
                excerpt = (r.get("excerpt") or "").replace("\n", " ")[:_NOTE_EXCERPT_CHARS]
                lines.append(f"- {r.get('path')}:{r.get('line')} {excerpt}")

        # 最近笔记补充时间近因（搜索是相关性排序，不体现"最近"）
        try:
            from tools import workspace as _ws

            root = _ws.resolve_workspace_root()
            lines.extend(_recent_notes(root, exclude))
        except Exception:
            pass

        if not lines:
            return None
        block = "【相关历史经验与代码检索】（code_context 自动注入，仅供参考）\n" + "\n".join(lines)
        return block[:max_chars]
    except Exception:
        return None


def build_repo_map_block(max_chars: int = 2500, paths: Optional[list] = None) -> Optional[str]:
    """生成仓库地图注入块（Aider repo map 模式）；无文件或失败返回 None。"""
    try:
        from tools.code_context import CodeContext  # 延迟导入

        result = CodeContext.repo_map(max_chars=max_chars, paths=paths)
        if not result.get("success") or not result.get("map"):
            return None
        return "【仓库地图】（code_context 自动注入，供定位代码，不要修改目标以外的文件）\n" + result["map"]
    except Exception:
        return None


__all__ = ["build_design_memory_block", "build_repo_map_block"]
