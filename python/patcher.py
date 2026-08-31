# -*- coding: utf-8 -*-
"""确定性补丁应用器（削接缝动作 A 的地基）。

coding_agent 的输出契约计划从"JSON 里带文件全文"改为 search/replace 补丁块；
本模块负责把补丁块确定性地应用到文件内容上。代码内容从此不再进 JSON 字符串。

块格式（Aider 式，纯文本标记，模型无需做 JSON 转义）：

    <<<<<<< SEARCH
    需要被替换的现有内容（必须在当前文件中恰好出现一次）
    =======
    替换后的内容（为空表示删除该片段）
    >>>>>>> REPLACE

规则：
- SEARCH 段为空白：表示新建文件。仅当当前内容为 None 或空字符串时合法，
  REPLACE 段即为初始内容；文件已有内容 → file_exists_conflict。
- SEARCH 非空：精确匹配必须恰好 1 次；0 次 → not_found；>=2 次 → ambiguous。
  精确失败时尝试 CRLF -> LF 行尾归一后再匹配（命中则全文以 LF 处理），
  仍失败才报错——错误附 SEARCH 首行作为定位线索，供 feedback 通道回流。
- 多个块按出现顺序依次应用，前一块结果作为后一块输入。
- 块外的解释文字一律忽略；允许 markdown 代码围栏包裹整个补丁。
- 不做任何"聪明"修复（空白容忍、模糊匹配）：宁可报错走 feedback 重试，
  也不静默改错位置。

与 json_repair 的分工：json_repair 修控制面 JSON；本模块处理代码内容。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

SEARCH_MARKER = re.compile(r"^\s*<{7}\s*SEARCH\s*$")
DIVIDER_MARKER = re.compile(r"^\s*={7}\s*$")
REPLACE_MARKER = re.compile(r"^\s*>{7}\s*REPLACE\s*$")


@dataclass
class PatchBlock:
    search: str
    replace: str


@dataclass
class PatchError:
    kind: str  # parse_error | not_found | ambiguous | file_exists_conflict
    message: str
    block_index: Optional[int] = None
    hint: str = ""


@dataclass
class PatchResult:
    ok: bool
    content: Optional[str] = None  # 成功时为应用后的完整内容
    applied: int = 0
    error: Optional[PatchError] = None


def _strip_fence(text: str) -> str:
    """模型爱用 ``` 围栏包补丁；仅当全文以围栏开头时剥掉最外层。"""
    t = (text or "").strip()
    if not t.startswith("```"):
        return text
    first_nl = t.find("\n")
    if first_nl == -1:
        return text
    body = t[first_nl + 1:]
    closing = body.rfind("```")
    if closing != -1 and body[closing:].strip() == "```":
        body = body[:closing]
    return body.strip("\n")


def _hint(search: str) -> str:
    for line in (search or "").splitlines():
        line = line.strip()
        if line:
            return line[:60]
    return ""


def parse_blocks(patch_text: str) -> Tuple[List[PatchBlock], Optional[PatchError]]:
    """从模型输出中提取全部完整补丁块；块外文字忽略。"""
    lines = _strip_fence(patch_text or "").split("\n")
    blocks: List[PatchBlock] = []
    i, n = 0, len(lines)
    while i < n:
        if SEARCH_MARKER.match(lines[i]):
            search_lines: List[str] = []
            i += 1
            divider_at = None
            while i < n:
                if DIVIDER_MARKER.match(lines[i]):
                    divider_at = i
                    break
                search_lines.append(lines[i])
                i += 1
            if divider_at is None:
                return [], PatchError(
                    "parse_error", "补丁块缺少 ======= 分隔线", len(blocks), _hint("\n".join(search_lines))
                )
            replace_lines: List[str] = []
            i += 1
            end_at = None
            while i < n:
                if REPLACE_MARKER.match(lines[i]):
                    end_at = i
                    break
                replace_lines.append(lines[i])
                i += 1
            if end_at is None:
                return [], PatchError(
                    "parse_error", "补丁块缺少 >>>>>>> REPLACE 结束线", len(blocks), _hint("\n".join(search_lines))
                )
            blocks.append(PatchBlock(search="\n".join(search_lines), replace="\n".join(replace_lines)))
            i += 1
        else:
            i += 1
    if not blocks:
        return [], PatchError(
            "parse_error", "未找到任何补丁块（需要 <<<<<<< SEARCH / ======= / >>>>>>> REPLACE 三段标记）"
        )
    return blocks, None


def apply_patches(current: Optional[str], patch_text: str) -> PatchResult:
    """把补丁块应用到 current（None/空字符串 表示文件不存在/空）。

    纯函数：不做任何文件 IO，调用方负责读写与路径校验。
    """
    blocks, err = parse_blocks(patch_text)
    if err:
        return PatchResult(ok=False, error=err)
    content = current if current is not None else None
    for idx, block in enumerate(blocks):
        search, replace = block.search, block.replace
        if search.strip() == "":
            # 新建文件语义
            if content is not None and content.strip() != "":
                return PatchResult(
                    ok=False,
                    applied=idx,
                    error=PatchError(
                        "file_exists_conflict",
                        f"第 {idx + 1} 个块的 SEARCH 为空（新建文件），但目标文件已有内容",
                        idx,
                    ),
                )
            content = replace if not content else content + replace
            continue
        if content is None:
            return PatchResult(
                ok=False,
                applied=idx,
                error=PatchError(
                    "not_found",
                    f"第 {idx + 1} 个块要求修改现有文件，但文件不存在",
                    idx,
                    _hint(search),
                ),
            )
        count = content.count(search)
        if count == 0 and "\r\n" in content:
            normalized = content.replace("\r\n", "\n")
            if normalized.count(search) == 1:
                content = normalized
                count = 1
        if count == 0:
            return PatchResult(
                ok=False,
                applied=idx,
                error=PatchError(
                    "not_found",
                    f"第 {idx + 1} 个块的 SEARCH 内容在文件中不存在",
                    idx,
                    _hint(search),
                ),
            )
        if count > 1:
            return PatchResult(
                ok=False,
                applied=idx,
                error=PatchError(
                    "ambiguous",
                    f"第 {idx + 1} 个块的 SEARCH 内容在文件中出现 {count} 次，无法确定位置",
                    idx,
                    _hint(search),
                ),
            )
        content = content.replace(search, replace, 1)
    return PatchResult(ok=True, content=content, applied=len(blocks), error=None)


_KIND_BASE = {
    "parse_error": "补丁格式无法解析",
    "not_found": "SEARCH 内容在文件中不存在",
    "ambiguous": "SEARCH 内容命中多处",
    "file_exists_conflict": "尝试新建文件，但文件已存在",
}


def error_feedback_text(result: PatchResult) -> Optional[str]:
    """把补丁失败转成给 coding_agent 的 feedback 文本（复用 inputs.feedback 通道）。"""
    if result.ok or result.error is None:
        return None
    e = result.error
    base = _KIND_BASE.get(e.kind, "补丁应用失败")
    msg = f"上一轮补丁未应用成功：{base}——{e.message}。"
    if e.hint:
        msg += f"定位线索：SEARCH 首行为「{e.hint}」。"
    msg += "请重新输出补丁块：<<<<<<< SEARCH（现有内容，必须唯一命中）=======（替换后内容）>>>>>>> REPLACE。"
    return msg
