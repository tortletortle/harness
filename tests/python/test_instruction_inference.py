"""
测试指令推断（替换已删除的 python/test_instruction_inference.py 和
python/test_tool_support.py）。

原测试文件的问题：断言的是作者自己假设的指令名（"write"/"run"/"fetch"/
"create_file"），与 `_infer_instruction()` 的实际返回值和各工具
`execute()` 实际支持的指令名不一致，导致测试即使代码有真实bug也会一直
"通过"。本文件的断言全部对照当前源码实际行为编写，并额外验证推断出的
指令名确实被对应工具的 execute() 接受（不会命中 "Invalid instruction"）。
"""

import pytest

from executor import DefaultExecutor
from tools import get_registry


@pytest.fixture
def executor():
    """只测试 _infer_instruction，不需要真实 llm_client/model_config"""
    return DefaultExecutor(None, {})


# (tool_id, inputs, expected_instruction)
INFERENCE_CASES = [
    ("file_creator", {"path": "test.py", "content": "print('hello')"}, "create_file"),
    ("file_creator", {"path": "src/utils"}, "create_directory"),
    ("file_creator", {"path": "src/"}, "create_directory"),
    ("git_operator", {"action": "init"}, "git_init"),
    ("git_operator", {"action": "add", "files": ["."]}, "git_add"),
    ("git_operator", {"action": "commit", "message": "x"}, "git_commit"),
    ("git_operator", {"action": "clone", "url": "https://x"}, "git_clone"),
    ("git_operator", {}, "git_init"),
    ("package_installer", {"packages": ["express"]}, "install_npm"),
    ("package_installer", {"requirements_file": "requirements.txt"}, "install_pip"),
    ("package_installer", {}, "install_npm"),
    ("text_editor", {"path": "a.txt", "search": "x", "replace": "y"}, "replace_in_file"),
    ("text_editor", {"path": "a.txt", "old_text": "x", "new_text": "y"}, "replace_in_file"),
    ("text_editor", {"path": "a.txt", "content": "hello"}, "append_to_file"),
    ("text_editor", {"path": "a.txt", "line_number": 3}, "insert_at_line"),
    ("text_editor", {"path": "a.txt"}, "read_file"),
    ("command_runner", {"command": "echo hi"}, "run_command"),
    ("web_fetcher", {"url": "https://example.com", "path": "out.html"}, "download_file"),
    ("web_fetcher", {"url": "https://example.com"}, "fetch_content"),
]


@pytest.mark.parametrize("tool_id,inputs,expected", INFERENCE_CASES)
def test_infer_instruction_matches_actual_behavior(executor, tool_id, inputs, expected):
    """_infer_instruction() 的返回值必须与当前源码实际逻辑一致"""
    inferred = executor._infer_instruction(tool_id, inputs)
    assert inferred == expected


@pytest.mark.parametrize("tool_id,inputs,expected", INFERENCE_CASES)
def test_inferred_instruction_is_actually_supported(monkeypatch, tmp_path, tool_id, inputs, expected):
    """
    推断出的指令名必须被对应工具的 execute() 接受，
    即不应该命中 execute() 里的 "未知指令" / "Invalid instruction" 分支。

    这是被删除的旧 test_tool_support.py 真正想验证但断言写错了的内容：
    "推断出的指令" 与 "工具实际支持的指令" 必须是同一套名字。
    """
    # 真实执行工具：隔离工作区，产物落 tmp，不污染仓库根目录。
    monkeypatch.setenv("EAS_WORKSPACE_ROOT", str(tmp_path))
    registry = get_registry()
    tool = registry.get_tool(tool_id)
    assert tool is not None, f"工具不存在: {tool_id}"

    result = tool.execute(expected, inputs)
    assert result.get("error") != "Invalid instruction", (
        f"{tool_id}:{expected} 被 execute() 判定为未知指令，"
        f"说明 _infer_instruction() 推断出的名字与工具实际支持的指令名不一致"
    )
