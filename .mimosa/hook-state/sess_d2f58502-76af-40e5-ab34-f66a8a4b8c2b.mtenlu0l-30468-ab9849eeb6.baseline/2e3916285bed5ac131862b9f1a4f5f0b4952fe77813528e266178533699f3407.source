"""
工具能力评测（第 4 层：模型选工具的准确率）

三层判分（BFCL AST 匹配思路）：
  1. tool_id       —— 模型是否选对工具（精确匹配）
  2. instruction   —— 是否选对工具指令（精确匹配）
  3. key_params    —— 关键参数是否正确（子集包含匹配，宽松归一化）

场景集覆盖 7 个工具 + 2 个"无需工具"负例（BFCL relevance detection：
测模型是否会硬造一个工具调用）。

运行方式：
  离线部分（_infer_instruction 映射）随常规 pytest 运行；
  真实 LLM 评测需要显式开启：
    RUN_REAL_LLM_EVAL=1 python -m pytest tests/eval/test_tool_selection.py -q --no-cov -s
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))

from executor import DefaultExecutor  # noqa: E402
from llm_client import LLMClient, Message  # noqa: E402
from tools import get_registry  # noqa: E402

# ===== 场景集 =====
# expect_params 只写"必须正确"的关键键；值为 None 表示只要求键存在，
# 为字符串表示模型值包含该子串（路径分隔符归一化后）。

SCENARIOS = [
    # --- file_creator ---
    {
        "id": "file_create",
        "task": "在工作目录创建 src/main.py 文件，内容为 print('hello')",
        "expect_tool": "file_creator",
        "expect_instruction": "create_file",
        "expect_params": {"path": "main.py"},
    },
    {
        "id": "dir_create",
        "task": "创建目录 build/output",
        "expect_tool": "file_creator",
        "expect_instruction": "create_directory",
        "expect_params": {"path": "build/output"},
    },
    # --- text_editor ---
    {
        "id": "file_read",
        "task": "读取 config/app.yaml 的内容给我看",
        "expect_tool": "text_editor",
        "expect_instruction": "read_file",
        "expect_params": {"path": "app.yaml"},
    },
    {
        "id": "file_replace",
        "task": "把 docs/api.md 里的旧地址 http://old.example.com 全部替换成 http://new.example.com",
        "expect_tool": "text_editor",
        "expect_instruction": "replace_in_file",
        "expect_params": {"path": "api.md"},
    },
    # --- command_runner ---
    {
        "id": "run_tests",
        "task": "在项目根目录运行 pytest tests/ -q 看测试是否通过",
        "expect_tool": "command_runner",
        "expect_instruction": "run_command",
        "expect_params": {"command": "pytest"},
    },
    {
        "id": "run_list_dir",
        "task": "列出当前目录下有哪些文件",
        "expect_tool": "command_runner",
        "expect_instruction": "run_command",
        "expect_params": {"command": None},
    },
    # --- package_installer ---
    {
        "id": "npm_install",
        "task": "安装前端依赖包 lodash 到项目里（npm 项目）",
        "expect_tool": "package_installer",
        "expect_instruction": "install_npm",
        "expect_params": {"packages": "lodash"},
    },
    {
        "id": "pip_install",
        "task": "用 pip 安装 Python 包 requests",
        "expect_tool": "package_installer",
        "expect_instruction": "install_pip",
        "expect_params": {"packages": "requests"},
    },
    # --- git_operator ---
    {
        "id": "git_commit",
        "task": "把当前改动 git 提交，提交信息为 fix: login bug",
        "expect_tool": "git_operator",
        "expect_instruction": "git_commit",
        "expect_params": {},
    },
    {
        "id": "git_init",
        "task": "在 demo_project 目录初始化一个新的 git 仓库",
        "expect_tool": "git_operator",
        "expect_instruction": "git_init",
        "expect_params": {},
    },
    # --- web_fetcher ---
    {
        "id": "web_fetch",
        "task": "抓取 https://example.com 页面的文本内容",
        "expect_tool": "web_fetcher",
        "expect_instruction": "fetch_content",
        "expect_params": {"url": "example.com"},
    },
    {
        "id": "web_download",
        "task": "下载 https://example.com/data.csv 保存为 data/data.csv",
        "expect_tool": "web_fetcher",
        "expect_instruction": "download_file",
        "expect_params": {"url": "example.com"},
    },
    # --- manual_tool ---
    {
        "id": "manual_needed",
        "task": "生产数据库的清理操作风险太高，需要人工确认后执行",
        "expect_tool": "manual_tool",  # 注册 ID 已统一为 manual_tool（旧名 manual 保留为别名）
        "expect_instruction": None,
        "expect_params": {},
    },
    # --- 负例：无需工具（BFCL relevance detection） ---
    {
        "id": "no_tool_explain",
        "task": "用一句话解释什么是递归，不需要操作任何文件",
        "expect_tool": "none",
        "expect_instruction": None,
        "expect_params": {},
    },
    {
        "id": "no_tool_chat",
        "task": "刚才执行结果里的报错是什么意思？帮我分析一下原因（仅分析，不要执行任何命令）",
        "expect_tool": "none",
        "expect_instruction": None,
        "expect_params": {},
    },
]


# ===== 离线部分：_infer_instruction 确定性映射（常规回归） =====

def test_infer_instruction_mapping():
    executor = DefaultExecutor(llm_client=LLMClient(), model_config=None)
    cases = [
        ("file_creator", {"path": "a.txt"}, "create_file"),
        ("file_creator", {"path": "output/"}, "create_directory"),
        ("file_creator", {"path": "build"}, "create_directory"),
        ("git_operator", {"action": "commit"}, "git_commit"),
        ("package_installer", {"requirements_file": "req.txt"}, "install_pip"),
        ("text_editor", {"search": "a", "replace": "b"}, "replace_in_file"),
        ("command_runner", {"command": "ls"}, "run_command"),
        ("web_fetcher", {"url": "https://x.com"}, "fetch_content"),
        ("web_fetcher", {"url": "https://x.com", "save_to": "d.bin"}, "download_file"),
    ]
    for tool_id, inputs, expected in cases:
        got = executor._infer_instruction(tool_id, inputs)
        assert got == expected, f"{tool_id} {inputs} -> {got!r}, expected {expected!r}"


# ===== 真实 LLM 评测 =====

RUN_REAL = os.environ.get("RUN_REAL_LLM_EVAL") == "1"

pytestmark_real = pytest.mark.skipif(not RUN_REAL, reason="需要 RUN_REAL_LLM_EVAL=1")


def _build_tools_prompt() -> str:
    registry = get_registry()
    lines = []
    for meta in registry.list_tools():
        tool_id = meta.get("tool_id")
        lines.append(f"- tool_id: {tool_id} | {meta.get('description', '')}")
        for inst in meta.get("capabilities", {}).get("instructions", []):
            params = ", ".join(inst.get("parameters", {}).keys())
            lines.append(f"    instruction: {inst['name']}({params})")
    return "\n".join(lines)


SYSTEM_PROMPT = """你是 EAS 系统的工具路由器。根据用户任务，从可用工具中选择一个工具执行。
可用工具：
%s

规则：
1. 必须严格输出 JSON，格式：{"tool_id": "...", "instruction": "...", "inputs": {...}}
2. 如果任务不需要调用任何工具（例如只是提问、分析、解释），或缺少执行所需的必要信息，
   输出 {"tool_id": "none", "instruction": null, "inputs": {}, "note": "<说明原因，或指出缺什么信息>"}
   —— tool_id 为 none 时 note 字段必填。
3. inputs 中只包含完成任务必需的参数。
4. 不要输出 JSON 以外的任何内容。""" % _build_tools_prompt()


def _extract_json(text: str):
    """从模型输出提取 JSON 对象（容忍 markdown 代码块/前后缀）。"""
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    raw = text[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 轻量修复：去掉对象尾随逗号后重试
        import re

        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
        except Exception:
            return None


def _norm(v):
    return str(v).replace("\\", "/").lower().strip() if isinstance(v, str) else v


def _check_params(expected: dict, got: dict) -> dict:
    """返回 {key: ok}。值为 None 只要求键存在；字符串要求归一化后包含。"""
    result = {}
    for key, want in expected.items():
        if key not in got:
            result[key] = False
            continue
        if want is None:
            result[key] = got[key] not in (None, "")
        else:
            result[key] = _norm(want) in _norm(got.get(key))
    return result


async def _run_scenario(llm, cfg, scenario):
    resp = await llm.chat(
        provider_name=cfg["provider"],
        messages=[
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=scenario["task"]),
        ],
        model=cfg["model"],
        temperature=0.0,
        max_tokens=500,
    )
    parsed = _extract_json(resp.content)
    report = {
        "id": scenario["id"],
        "raw": resp.content[:200],
        "tool_ok": False,
        "instruction_ok": False,
        "params_ok": None,
        "contract_ok": True,
        "parsed": parsed,
    }
    if parsed is None:
        # 契约违规：没有输出 JSON。若输出中也没有任何工具调用意图，
        # 视为"追问澄清"（负例场景可接受），工具选型仍算对，但契约记违规。
        has_tool_intent = '"tool_id"' in resp.content or '"tool_id"' in resp.content.replace(" ", "")
        report["contract_ok"] = False
        if has_tool_intent:
            report["fail_reason"] = "json_parse"
        else:
            report["tool_ok"] = scenario["expect_tool"] == "none"
            report["fail_reason"] = "clarification_without_json"
        return report

    tool_id = str(parsed.get("tool_id", "")).strip()
    report["tool_ok"] = tool_id == scenario["expect_tool"]

    if scenario["expect_instruction"] is None:
        report["instruction_ok"] = True
    else:
        report["instruction_ok"] = str(parsed.get("instruction", "")).strip() == scenario["expect_instruction"]

    # 契约：tool_id=none 时必须带 note（澄清的结构化通道）
    if scenario["expect_tool"] == "none" and not str(parsed.get("note") or "").strip():
        report["contract_ok"] = False
        report["fail_reason"] = "missing_note_for_none"

    inputs = parsed.get("inputs") or {}
    checks = _check_params(scenario["expect_params"], inputs)
    report["params_ok"] = all(checks.values()) if checks else None
    if checks and not all(checks.values()):
        report["fail_reason"] = f"params {checks}"
    return report


@pytest.mark.asyncio
@pytest.mark.skipif(not RUN_REAL, reason="需要 RUN_REAL_LLM_EVAL=1")
async def test_tool_selection_accuracy_report():
    """真实 LLM 选型评测：输出逐场景结果与准确率，整体阈值 60% 兜底。"""
    import yaml

    cfg_path = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
    config = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    exec_cfg = config["models"]["executor"]["primary"]

    llm = LLMClient()
    from llm_client import OpenAICompatibleProvider

    llm.register_provider(OpenAICompatibleProvider(
        name=exec_cfg["provider"],
        base_url=config["base_urls"][exec_cfg["provider"]],
        api_key=config["api_keys"][exec_cfg["provider"]],
    ))

    reports = []
    for scenario in SCENARIOS:
        try:
            report = await _run_scenario(llm, exec_cfg, scenario)
        except Exception as e:
            report = {"id": scenario["id"], "tool_ok": False, "instruction_ok": False,
                      "params_ok": None, "fail_reason": f"api_error: {e}", "raw": ""}
        reports.append(report)

    # ===== 输出报告 =====
    print("\n" + "=" * 72)
    print(f"{'场景':<16}{'工具':<6}{'指令':<6}{'参数':<6}  说明")
    print("-" * 72)
    for r, s in zip(reports, SCENARIOS):
        tool = "OK" if r["tool_ok"] else "FAIL"
        instr = "OK" if r["instruction_ok"] else "FAIL"
        params = "-" if r["params_ok"] is None else ("OK" if r["params_ok"] else "FAIL")
        note = r.get("fail_reason", "")
        if not r["tool_ok"]:
            got_tool = (r.get("parsed") or {}).get("tool_id", "?")
            note = f"期望 {s['expect_tool']}，得到 {got_tool}" + (f" | {note}" if note else "")
        print(f"{s['id']:<16}{tool:<6}{instr:<6}{params:<6}  {note[:60]}")
    print("-" * 72)

    tool_acc = sum(r["tool_ok"] for r in reports) / len(reports)
    instr_acc = sum(r["instruction_ok"] for r in reports) / len(reports)
    contract_violations = sum(1 for r in reports if not r.get("contract_ok", True))
    print(f"工具选型准确率: {tool_acc:.0%} ({sum(r['tool_ok'] for r in reports)}/{len(reports)})")
    print(f"指令选型准确率: {instr_acc:.0%}")
    print(f"JSON 契约违规: {contract_violations}/{len(reports)}")

    # 逐工具统计
    by_tool = {}
    for r, s in zip(reports, SCENARIOS):
        by_tool.setdefault(s["expect_tool"], []).append(r["tool_ok"])
    print("\n按期望工具分组：")
    for tool_id, oks in sorted(by_tool.items()):
        print(f"  {tool_id:<18} {sum(oks)}/{len(oks)}")

    # 兜底阈值：低于 60% 视为回归（评测的主要价值在报告输出）
    assert tool_acc >= 0.6, f"工具选型准确率过低: {tool_acc:.0%}"
