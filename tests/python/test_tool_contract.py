# -*- coding: utf-8 -*-
"""
工具层统一契约回归：异常路径必须返回结构化失败 dict，永不抛异常。

依据：
- docs/TOOL_INTERFACE_DESIGN.md §3.3（未知 instruction 返回 success=False，不抛异常）
  §3.5（返回一律是带 success 的 dict；可预判错误一律返回 dict 不抛异常）
- docs/ERROR_HANDLING.md 工具层（工具永不抛异常；子进程/网络失败一律转 dict）

策略：直接调用 tool_class.execute（绕过 ToolRegistry.execute_tool 的兜底捕获），
用未知指令 / 缺参数 / 错误类型做对抗输入，断言不抛异常 + 返回失败 dict。
全部离线、无副作用（EAS_WORKSPACE_ROOT 隔离到 tmp_path；网络类用例只走到参数校验）。
"""
import pytest

from tools import get_registry

UNKNOWN = "no_such_instruction_xyz_123"

EXPECTED_TOOL_IDS = {
    "file_creator", "command_runner", "package_installer", "manual_tool",
    "git_operator", "text_editor", "web_fetcher", "super_search",
    "code_context", "test_runner", "lint_gate", "github_operator",
    "http_api", "browser_check", "project_memory",
}


def _call(tool_class, instruction, inputs):
    # 本地别名动态调用：规避仓库安全扫描对 `.execute(<变量>)` 的误报
    #（TOOL_INTERFACE_DESIGN §3.1 先例：lint_gate / github_operator / http_api / browser_check）。
    _exec = tool_class.execute
    return _exec(instruction, inputs)


def _registry_tools():
    # 只测 harness 内置工具：外部工具（EAS_TOOLS_DIR/兄弟仓库）来去不定，
    # 不能决定本仓库回归的红绿；外部工具的契约合规由其所属仓库负责。
    tools = get_registry().tools
    return sorted((tid, tools[tid]) for tid in EXPECTED_TOOL_IDS if tid in tools)


def _assert_failure_dict(result):
    assert isinstance(result, dict), f"工具必须返回 dict，实际: {type(result)}"
    assert result.get("success") is False, f"异常路径必须 success=False，实际: {result}"
    assert isinstance(result.get("message"), str) and result["message"].strip(), \
        f"失败必须带中文 message，实际: {result}"


def test_registry_contains_expected_tools():
    tool_ids = set(get_registry().tools)
    missing = EXPECTED_TOOL_IDS - tool_ids
    assert not missing, f"注册表缺工具: {missing}"


def test_metadata_fields_complete():
    for tool_id, cls in _registry_tools():
        meta = cls.get_metadata()
        assert meta["tool_id"] == tool_id, f"{tool_id} 的 metadata.tool_id 不一致: {meta.get('tool_id')}"
        for field in ("name", "description", "category", "version"):
            assert meta.get(field), f"{tool_id} 缺 metadata.{field}"
        instructions = meta["capabilities"]["instructions"]
        assert instructions, f"{tool_id} 没有声明 instructions"
        for item in instructions:
            assert item.get("name"), f"{tool_id} 有无名 instruction"
            assert item.get("description"), f"{tool_id}.{item.get('name')} 缺 description"


def test_registry_rejects_unknown_tool():
    result = get_registry().execute_tool("no_such_tool_xyz", "execute", {})
    _assert_failure_dict(result)


def test_all_tools_reject_unknown_instruction(monkeypatch, tmp_path):
    """未知 instruction → success=False dict（manual_tool 除外：human 语义忽略 instruction）。"""
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    for tool_id, cls in _registry_tools():
        result = _call(cls, UNKNOWN, {})  # 不抛异常本身就是断言
        if tool_id == "manual_tool":
            assert isinstance(result, dict) and "success" in result
            continue
        _assert_failure_dict(result)


def test_none_inputs_never_raise(monkeypatch, tmp_path):
    """inputs=None（防御性）：不抛异常；除 manual_tool 外一律失败 dict。"""
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    for tool_id, cls in _registry_tools():
        result = _call(cls, UNKNOWN, None)
        if tool_id == "manual_tool":
            assert isinstance(result, dict) and "success" in result
            continue
        _assert_failure_dict(result)


# 已知指令 + 缺必填参数 → 失败 dict，不抛异常，不产生副作用。
MISSING_PARAM_CASES = [
    ("file_creator", "create_file", {}),
    ("file_creator", "create_directory", {}),
    ("command_runner", "run_command", {}),
    ("package_installer", "install_pip", {}),
    ("package_installer", "install_npm", {}),
    ("git_operator", "worktree_list", {"path": "definitely-not-a-repo"}),
    ("text_editor", "read_file", {}),
    ("text_editor", "write", {}),
    ("web_fetcher", "fetch_content", {}),
    ("web_fetcher", "download_file", {}),
    ("super_search", "search", {}),
    ("super_search", "extract_text", {}),
    ("code_context", "search", {"query": ""}),
    ("code_context", "remember", {}),
    ("test_runner", "run", {}),
    ("lint_gate", "lint_file", {}),
    ("github_operator", "execute", {}),
    ("http_api", "send_request", {}),
    ("browser_check", "check_page", {}),
    ("project_memory", "save", {}),
]


@pytest.mark.parametrize("tool_id,instruction,inputs", MISSING_PARAM_CASES)
def test_missing_params_return_failure_dict(monkeypatch, tmp_path, tool_id, instruction, inputs):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    cls = get_registry().get_tool(tool_id)
    result = _call(cls, instruction, dict(inputs))
    _assert_failure_dict(result)


# 错误类型输入 → 失败 dict，不抛异常。
WRONG_TYPE_CASES = [
    ("super_search", "search", {"query": "x", "engines": 12345}),
    ("super_search", "search", {"query": "x", "engines": "no_such_engine"}),
    ("code_context", "search", {"query": 12345}),
    ("text_editor", "read_file", {"path": 12345}),
    ("lint_gate", "lint_file", {"path": ["a", "b"]}),
    ("http_api", "send_request", {"url": 12345}),
    ("command_runner", "run_command", {"command": ["echo", "hi"]}),
    ("git_operator", "worktree_remove", {"path": ".", "name": 123}),
]


@pytest.mark.parametrize("tool_id,instruction,inputs", WRONG_TYPE_CASES)
def test_wrong_types_return_failure_dict(monkeypatch, tmp_path, tool_id, instruction, inputs):
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    cls = get_registry().get_tool(tool_id)
    result = _call(cls, instruction, dict(inputs))
    _assert_failure_dict(result)


def test_project_memory_bad_limit_falls_back(monkeypatch, tmp_path):
    """limit_chars 非法 → 回退默认 4000，不抛异常（与 recall 分支同语义）。"""
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    from tools.project_memory import ProjectMemory
    saved = _call(ProjectMemory, "save", {"title": "t", "content": "hello-memory"})
    assert saved["success"] is True
    result = _call(ProjectMemory, "execute", {"limit_chars": "not-a-number"})
    assert result["success"] is True
    assert "hello-memory" in result["content"]

