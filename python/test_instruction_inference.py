"""
测试指令推断修复

验证 executor 能否正确推断缺失的 instruction 字段
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from executor import DefaultExecutor


def test_instruction_inference():
    """测试各种工具的指令推断"""
    
    # 创建executor（不需要llm_client和model_config，因为我们只测试_infer_instruction）
    executor = DefaultExecutor(None, {})
    
    # 测试用例
    test_cases = [
        {
            "name": "file_creator - 创建文件（有content）",
            "tool": "file_creator",
            "inputs": {"path": "test.py", "content": "print('hello')"},
            "expected": "create_file"
        },
        {
            "name": "file_creator - 创建目录（无扩展名）",
            "tool": "file_creator",
            "inputs": {"path": "src/utils"},
            "expected": "create_directory"
        },
        {
            "name": "file_creator - 创建目录（带斜杠）",
            "tool": "file_creator",
            "inputs": {"path": "src/"},
            "expected": "create_directory"
        },
        {
            "name": "git_operator - git init",
            "tool": "git_operator",
            "inputs": {"action": "init"},
            "expected": "git_init"
        },
        {
            "name": "git_operator - git add",
            "tool": "git_operator",
            "inputs": {"action": "add", "files": ["."]},
            "expected": "git_add"
        },
        {
            "name": "git_operator - 无action（默认）",
            "tool": "git_operator",
            "inputs": {},
            "expected": "git_init"
        },
        {
            "name": "package_installer - npm",
            "tool": "package_installer",
            "inputs": {"packages": ["express"]},
            "expected": "install_npm"
        },
        {
            "name": "package_installer - 无packages（默认）",
            "tool": "package_installer",
            "inputs": {},
            "expected": "install_npm"
        },
        {
            "name": "text_editor - write",
            "tool": "text_editor",
            "inputs": {"path": "test.txt", "content": "hello"},
            "expected": "write"
        },
        {
            "name": "command_runner",
            "tool": "command_runner",
            "inputs": {"command": "ls"},
            "expected": "run"
        },
        {
            "name": "web_fetcher",
            "tool": "web_fetcher",
            "inputs": {"url": "https://example.com"},
            "expected": "fetch"
        }
    ]
    
    print("\n" + "=" * 70)
    print("测试指令推断逻辑")
    print("=" * 70)
    
    passed = 0
    failed = 0
    
    for test in test_cases:
        inferred = executor._infer_instruction(test["tool"], test["inputs"])
        
        if inferred == test["expected"]:
            print(f"\n[OK] {test['name']}")
            print(f"     工具: {test['tool']}")
            print(f"     推断: {inferred}")
            passed += 1
        else:
            print(f"\n[FAIL] {test['name']}")
            print(f"       工具: {test['tool']}")
            print(f"       输入: {test['inputs']}")
            print(f"       期望: {test['expected']}")
            print(f"       实际: {inferred}")
            failed += 1
    
    print("\n" + "=" * 70)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 70)
    
    return failed == 0


if __name__ == "__main__":
    success = test_instruction_inference()
    sys.exit(0 if success else 1)
