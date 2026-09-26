"""设计书审批后的项目密钥录入钩子（外部工具仓 design_secrets 的宿主侧接线）。

全部 fail-closed：外部工具仓缺席、解析失败、任何异常，都返回
{"prompt_shown": False, "available": False, ...}，绝不改变既有审批行为，
也不会让模型拿到任何密钥值（密钥值只在用户本地录入界面里，直达加密写盘）。

本模块只依赖标准库；对 harness_tools 的导入是守卫式、按需的。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_tools():
    """守卫式导入外部工具仓；失败返回 (None, None)。

    优先直接导入；若 EAS_TOOLS_DIR 指向 <工具仓>/harness_tools/tools，
    则把工具仓根补进 sys.path 再试一次。缺席或仍失败都优雅降级。
    """
    try:
        from harness_tools import design_secrets  # noqa: F401
        from harness_tools.secrets_store import SecretStore  # noqa: F401

        return design_secrets, SecretStore
    except Exception:
        pass
    try:
        raw = os.environ.get("EAS_TOOLS_DIR")
        if raw:
            tools_root = Path(raw).resolve().parents[1]
            if str(tools_root) not in sys.path:
                sys.path.insert(0, str(tools_root))
            from harness_tools import design_secrets  # noqa: F401
            from harness_tools.secrets_store import SecretStore  # noqa: F401

            return design_secrets, SecretStore
    except Exception:
        pass
    return None, None


def _workspace_root() -> Path:
    raw = os.environ.get("EAS_WORKSPACE_ROOT")
    if raw:
        return Path(raw)
    return Path.cwd()


def _text_blob(design) -> str:
    """把设计书对象拼成一段可被 design_secrets 扫描的文本。"""
    parts: List[str] = []
    user_request = getattr(design, "user_request", None) or (
        design.get("user_request", "") if isinstance(design, dict) else "")
    if user_request:
        parts.append(str(user_request))
    for field in ("part1_design", "part2_tool_assessment"):
        value = getattr(design, field, None)
        if value is None and isinstance(design, dict):
            value = design.get(field)
        if value is None:
            continue
        try:
            dump = value.model_dump() if hasattr(value, "model_dump") else value
            parts.append(json.dumps(dump, ensure_ascii=False, default=str))
        except Exception:
            parts.append(str(value))
    comments = getattr(design, "user_comments", None) or (
        design.get("user_comments") if isinstance(design, dict) else None)
    if comments:
        parts.append(str(comments))
    return "\n".join(parts)


def _normalize_declaration(text: str) -> Any:
    """把宿主的密钥声明规范成外部 design_secrets 认识的形态。

    外部 parse_required 对 str 只扫 fenced 代码块；对 dict 才取 required_secrets /
    secrets。宿主把声明当 JSON 字符串传时若直接透传，程序化声明会被静默忽略
    （此前 names/declaration 路径永远不弹录入页）。这里把可解析的 JSON 归一成 dict：
      {"required_secrets": [...]}  → dict（保留）
      [...]                        → {"required_secrets": [...]}
      其它（YAML / fenced 块 / 设计正文）→ 原样 str，交给 fenced 扫描。
    """
    if not text or not text.strip():
        return text
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    if isinstance(parsed, dict) and ("required_secrets" in parsed or "secrets" in parsed):
        return parsed
    if isinstance(parsed, list):
        return {"required_secrets": parsed}
    return text


def build_secrets_prompt(design, *, declaration: Optional[str] = None,
                         launcher=None) -> Dict[str, Any]:
    """设计书审批通过后调用；返回模型可读的提示计划（不含密钥值）。

    - declaration 非空时优先用它作为密钥声明（作者贴在审批请求里的 fenced block）；
    - 否则扫设计书文本里有没有 required_secrets 声明；
    - 有还没写的 → 打开一次性本地录入界面，返回 entry_url；
    - 没有 / 全写了 / 工具仓缺席 / 任何异常 → prompt_shown=False，不弹、不报错。
    """
    design_secrets, SecretStore = _load_tools()
    if design_secrets is None:
        return {"prompt_shown": False, "available": False, "pending": [],
                "already_set": []}
    try:
        root = _workspace_root()
        root.mkdir(parents=True, exist_ok=True)
        store = SecretStore(root)
        document = _normalize_declaration(declaration) if (declaration and declaration.strip()) else _text_blob(design)
        result = design_secrets.after_approval(document, store, root=root,
                                               launcher=launcher)
        result["available"] = True
        return result
    except Exception:
        return {"prompt_shown": False, "available": True, "pending": [],
                "already_set": []}


def list_project_secrets() -> Dict[str, Any]:
    """列出已写入的项目密钥名（不含值）；工具仓缺席或读取失败时优雅降级。"""
    design_secrets, SecretStore = _load_tools()
    if design_secrets is None:
        return {"available": False, "count": 0, "entries": []}
    try:
        root = _workspace_root()
        store = SecretStore(root)
        rows = store.list()
        return {"available": True, "count": len(rows), "entries": rows}
    except Exception:
        return {"available": True, "count": 0, "entries": []}
