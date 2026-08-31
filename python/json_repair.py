# -*- coding: utf-8 -*-
"""JSON 修复器：LLM 结构化输出的保守修复（不引入依赖、不做危险猜测）。

处理两类最高频的 LLM JSON 错误（三期观察实证）：
1. 尾逗号：{"a": 1,} / [1, 2,]
2. 字符串字面量内的裸控制字符：content 值里的真实换行/制表符未转义

明确不修（风险大于收益）：
- 单引号键/值（内容里合法单引号会被误伤）
- 截断的 JSON（无法可靠推断意图——截断交给上层"反馈重试"）
- 注释（// 与 /* 在字符串值里是合法内容）

解析策略：repair 前后都走标准 json.loads——修复只负责消除已知的语法噪声，
不改变任何语义。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def strip_code_fence(text: str) -> str:
    """剥离 markdown 代码块包裹（```json ... ```）。"""
    text = (text or "").strip()
    if "```" not in text:
        return text
    for part in text.split("```"):
        part = part.strip()
        if part.startswith("json"):
            part = part[4:].strip()
        if part.startswith("{"):
            return part
    return text


def escape_bare_control_chars(text: str) -> str:
    """把 JSON 字符串字面量内的裸换行/回车/制表符转义为 \\n 等合法转义。

    状态机实现：追踪是否在字符串内（处理 \\" 转义），字符串外的一切原样保留。
    """
    out: list[str] = []
    in_str = False
    escaped = False
    replace_map = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for ch in text:
        if in_str:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == '"':
                in_str = False
                out.append(ch)
                continue
            if ch in replace_map:
                out.append(replace_map[ch])
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def repair_json_text(text: str) -> str:
    """按保守顺序修复已知 JSON 噪声，返回修复后的文本（可能无变化）。"""
    text = strip_code_fence(text)
    text = _TRAILING_COMMA.sub(r"\1", text)
    text = escape_bare_control_chars(text)
    return text


def parse_loose(text: str) -> Optional[Any]:
    """宽容解析：直接解析 → 修复后解析。均失败返回 None。"""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    fenced = strip_code_fence(text)
    start, end = fenced.find("{"), fenced.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = fenced[start : end + 1]
    try:
        return json.loads(candidate)
    except Exception:
        pass
    try:
        return json.loads(repair_json_text(candidate))
    except Exception:
        return None
