"""任务模式识别（设计稿 §4 任务模式路由第一阶段）。

把用户请求分为两类：
- direct（直接工具模式）：读取/搜索/测试/检查类单一动作，跳过设计书直接调用工具。
- graph（图模式）：创建/修改/多动作类开发任务，走 设计书 → 审批 → 计划 → 执行。

规则是确定性、可解释的：创建类动词优先判定为 graph（保守），其余命中
单一工具意图的模式才判 direct；未命中一律 graph 兜底。direct 模式不绕过
安全边界——工具调用仍走 Grant、审计与 blocked 语义。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional


# 创建/修改类信号：出现即判 graph（保守优先，宁可多走一轮设计书）
_GRAPH_SIGNALS = re.compile(
    r"创建|新建|写入|修改|编辑|删除|重构|实现|开发|部署|安装|生成|编写|"
    r"write|create|modify|edit|delete|refactor|implement|build|deploy|install",
    re.IGNORECASE,
)

# 复合动作信号：多步请求不适合直接工具模式
_COMPOUND_SIGNALS = re.compile(r"然后|接着|再|并且|同时|之后|then|and then", re.IGNORECASE)

# 直接工具意图（按序匹配，第一条命中即返回）
_DIRECT_PATTERNS = [
    {
        "pattern": re.compile(r"^(?:帮我)?(?:搜索|搜一下|查一下?|搜|search)\s*(?P<query>.+)"),
        "tool_id": "super_search",
        "instruction": "search",
        "inputs": lambda m: {"query": m.group("query").strip()},
        "reason": "搜索请求 → super_search",
    },
    {
        "pattern": re.compile(r"^(?:读取|读一下|读|查看|看看|打开|read)\s*(?:文件\s*)?(?P<path>\S+)"),
        "tool_id": "text_editor",
        "instruction": "read_file",
        "inputs": lambda m: {"path": m.group("path").strip()},
        "reason": "读取文件请求 → text_editor:read_file",
    },
    {
        "pattern": re.compile(r"^(?:运行|跑)(?:一下)?测试(?:\s*(?P<target>.+))?"),
        "tool_id": "test_runner",
        "instruction": "run",
        "inputs": lambda m: (
            {"suite": "pytest", "test_path": m.group("target").strip()}
            if m.group("target") else {"suite": "pytest"}
        ),
        "reason": "运行测试请求 → test_runner",
    },
    {
        "pattern": re.compile(r"^(?:项目结构|仓库地图|repo\s*map|repo_map)\s*$"),
        "tool_id": "code_context",
        "instruction": "repo_map",
        "inputs": lambda m: {},
        "reason": "仓库地图请求 → code_context:repo_map",
    },
    {
        "pattern": re.compile(r"^(?:语法检查|lint)\s*(?P<path>\S+)"),
        "tool_id": "lint_gate",
        "instruction": "lint_file",
        "inputs": lambda m: {"path": m.group("path").strip()},
        "reason": "语法检查请求 → lint_gate",
    },
]


def classify_task(user_request: str) -> Dict[str, Any]:
    """分类用户请求的任务模式。

    返回 {"mode", "tool_id"?, "instruction"?, "inputs"?, "reason"}；
    direct 附带执行参数，graph 附带原因（引导走 /api/design）。
    """
    text = str(user_request or "").strip()
    if not text:
        return {"mode": "graph", "reason": "空请求，走设计书流程"}

    if _GRAPH_SIGNALS.search(text) or _COMPOUND_SIGNALS.search(text):
        return {
            "mode": "graph",
            "reason": "包含创建/修改或多步动作，走设计书 → 计划 → 执行流程",
        }

    for entry in _DIRECT_PATTERNS:
        match = entry["pattern"].match(text)
        if match:
            return {
                "mode": "direct",
                "tool_id": entry["tool_id"],
                "instruction": entry["instruction"],
                "inputs": entry["inputs"](match),
                "reason": entry["reason"],
            }

    return {
        "mode": "graph",
        "reason": "未识别为单一工具动作，保守走设计书流程",
    }
