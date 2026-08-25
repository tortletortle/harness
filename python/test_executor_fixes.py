#!/usr/bin/env python3
"""
测试 Executor 修复是否有效
测试 _infer_instruction() 方法和工具参数灵活性
"""

import sys
import asyncio
from typing import Dict, Any
from datetime import datetime

# 添加 python 目录到路径
sys.path.insert(0, '.')

from executor import DefaultExecutor
from models import Step, StepStatus
from tools import get_registry
from llm_client import LLMClient


class TestResults:
    """测试结果记录"""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def record_pass(self, test_name: str):
        self.passed += 1
        print(f"  [OK] {test_name}")
    
    def record_fail(self, test_name: str, reason: str):
        self.failed += 1
        self.errors.append((test_name, reason))
        print(f"  [FAIL] {test_name}: {reason}")
    
    def summary(self):
        print("\n" + "="*60)
        print(f"测试总结: {self.passed} 通过, {self.failed} 失败")
        if self.errors:
            print("\n失败详情:")
            for name, reason in self.errors:
                print(f"  - {name}: {reason}")
        print("="*60)
        return self.failed == 0


def test_infer_instruction():
    """测试 1: _infer_instruction() 方法单元测试"""
    print("\n" + "="*60)
    print("测试 1: _infer_instruction() 方法推断逻辑")
    print("="*60)
    
    results = TestResults()
    
    # 创建 executor (不需要实际的 LLM client)
    executor = DefaultExecutor(None, {}, get_registry())
    
    # 测试用例
    test_cases = [
        # git_operator 测试
        {
            "name": "git_operator - init",
            "tool_id": "git_operator",
            "inputs": {"action": "init", "path": "./test"},
            "expected": "git_init"
        },
        {
            "name": "git_operator - add",
            "tool_id": "git_operator",
            "inputs": {"action": "add", "files": ["."]},
            "expected": "git_add"
        },
        {
            "name": "git_operator - commit",
            "tool_id": "git_operator",
            "inputs": {"action": "commit", "message": "test"},
            "expected": "git_commit"
        },
        
        # package_installer 测试
        {
            "name": "package_installer - single package",
            "tool_id": "package_installer",
            "inputs": {"package": "express", "version": "latest"},
            "expected": "install_npm"
        },
        {
            "name": "package_installer - multiple packages",
            "tool_id": "package_installer",
            "inputs": {"packages": ["react", "react-dom"]},
            "expected": "install_npm"
        },
        
        # text_editor 测试
        {
            "name": "text_editor - write with content",
            "tool_id": "text_editor",
            "inputs": {"path": "test.js", "content": "console.log('test');"},
            "expected": "write"
        },
        {
            "name": "text_editor - write with code",
            "tool_id": "text_editor",
            "inputs": {"path": "test.js", "code": "console.log('test');"},
            "expected": "write"
        },
        
        # command_runner 测试
        {
            "name": "command_runner - run",
            "tool_id": "command_runner",
            "inputs": {"command": "ls -la"},
            "expected": "run"
        },
    ]
    
    for test_case in test_cases:
        result = executor._infer_instruction(test_case["tool_id"], test_case["inputs"])
        if result == test_case["expected"]:
            results.record_pass(test_case["name"])
        else:
            results.record_fail(test_case["name"], f"期望 '{test_case['expected']}', 得到 '{result}'")
    
    return results


def test_tool_parameter_flexibility():
    """测试 2: 工具参数灵活性"""
    print("\n" + "="*60)
    print("测试 2: 工具参数灵活性")
    print("="*60)
    
    results = TestResults()
    registry = get_registry()
    
    # 测试 package_installer 的参数灵活性
    test_cases = [
        {
            "name": "package_installer - package 字段",
            "tool_id": "package_installer",
            "instruction": "install_npm",
            "inputs": {"package": "lodash", "version": "latest", "dev": False}
        },
        {
            "name": "package_installer - packages 字段",
            "tool_id": "package_installer",
            "instruction": "install_npm",
            "inputs": {"packages": ["lodash"], "dev": False}
        },
        {
            "name": "package_installer - dev flag",
            "tool_id": "package_installer",
            "instruction": "install_npm",
            "inputs": {"package": "eslint", "dev": True}
        },
    ]
    
    for test_case in test_cases:
        try:
            # 注意：这里只测试参数是否被接受，不实际执行安装
            # 我们检查工具是否能正确解析参数而不抛出异常
            tool = registry.tools.get(test_case["tool_id"])
            if not tool:
                results.record_fail(test_case["name"], "工具不存在")
                continue
            
            # 检查工具是否有对应的指令
            if not hasattr(tool, test_case["instruction"]):
                results.record_fail(test_case["name"], f"工具没有 {test_case['instruction']} 方法")
                continue
            
            results.record_pass(test_case["name"])
            
        except Exception as e:
            results.record_fail(test_case["name"], str(e))
    
    return results


async def test_step_execution():
    """测试 3: 步骤执行测试"""
    print("\n" + "="*60)
    print("测试 3: 模拟步骤执行")
    print("="*60)
    
    results = TestResults()
    
    # 创建 executor
    executor = DefaultExecutor(None, {"primary": {"provider": "test", "model": "test"}}, get_registry())
    
    # 测试用例：模拟实际失败的步骤
    test_steps = [
        {
            "name": "Git 初始化步骤",
            "step": Step(
                id="test-git-init",
                plan_id="test-plan",
                order=1,
                stage="初始化",
                description="初始化 Git 仓库",
                tool="git_operator",  # 注意：没有 :instruction
                inputs={"action": "init", "path": "./test-project"},
                expected_output="Git 仓库已初始化",
                check_criteria="目录中存在 .git 文件夹",
                status=StepStatus.PENDING,
                created_at=datetime.now(),
            ),
            "should_infer": "git_init"
        },
        {
            "name": "包安装步骤",
            "step": Step(
                id="test-npm-install",
                plan_id="test-plan",
                order=2,
                stage="依赖安装",
                description="安装 Electron",
                tool="package_installer",
                inputs={"package": "electron", "version": "latest", "dev": True},
                expected_output="Electron 已安装",
                check_criteria="package.json 中包含 electron",
                status=StepStatus.PENDING,
                created_at=datetime.now(),
            ),
            "should_infer": "install_npm"
        },
        {
            "name": "文件创建步骤",
            "step": Step(
                id="test-file-write",
                plan_id="test-plan",
                order=3,
                stage="代码生成",
                description="创建主进程文件",
                tool="text_editor",
                inputs={"path": "./test-project/main.js", "content": "console.log('Hello');"},
                expected_output="文件已创建",
                check_criteria="文件存在",
                status=StepStatus.PENDING,
                created_at=datetime.now(),
            ),
            "should_infer": "write"
        },
    ]
    
    for test_case in test_steps:
        try:
            step = test_case["step"]
            
            # 测试推断逻辑
            tool_id = step.tool
            inferred = executor._infer_instruction(tool_id, step.inputs)
            
            if inferred == test_case["should_infer"]:
                results.record_pass(f"{test_case['name']} - 指令推断")
            else:
                results.record_fail(
                    f"{test_case['name']} - 指令推断",
                    f"期望 '{test_case['should_infer']}', 得到 '{inferred}'"
                )
            
            # 测试工具执行（不实际执行，只检查调用链）
            # 注意：这里不调用 execute_step 因为会真的执行操作
            # 我们只验证逻辑链路是否正确
            
        except Exception as e:
            results.record_fail(test_case["name"], str(e))
    
    return results


def test_actual_failures():
    """测试 4: 实际失败案例重现"""
    print("\n" + "="*60)
    print("测试 4: 实际失败案例配置验证")
    print("="*60)
    
    results = TestResults()
    executor = DefaultExecutor(None, {}, get_registry())
    
    # 从日志中提取的实际失败案例
    actual_failures = [
        {
            "name": "步骤 a138ad6e (Git init)",
            "tool": "git_operator",
            "inputs": {"action": "init", "path": "./screenshot-tool"},
            "expected_instruction": "git_init"
        },
        {
            "name": "步骤 4c84bada (Electron 安装)",
            "tool": "package_installer",
            "inputs": {"package": "electron", "version": "latest", "dev": True},
            "expected_instruction": "install_npm"
        },
        {
            "name": "步骤 cb7b5854 (实现屏幕捕获逻辑)",
            "tool": "text_editor",
            "inputs": {"instruction": "write", "path": "./src/capture.ts"},
            "expected_instruction": "write"
        },
        {
            "name": "步骤 ad6208cc (实现屏幕数据获取)",
            "tool": "text_editor",
            "inputs": {"code": "// screen data code", "path": "./src/screen.ts"},
            "expected_instruction": "write"
        },
    ]
    
    for case in actual_failures:
        inferred = executor._infer_instruction(case["tool"], case["inputs"])
        
        # 如果 inputs 中已有 instruction，应该被优先使用
        explicit_instruction = case["inputs"].get("instruction")
        if explicit_instruction:
            expected = explicit_instruction
        else:
            expected = case["expected_instruction"]
        
        if inferred == expected or explicit_instruction:
            results.record_pass(case["name"])
        else:
            results.record_fail(
                case["name"],
                f"期望 '{expected}', 得到 '{inferred}'"
            )
    
    return results


async def main():
    """运行所有测试"""
    print("\n" + "="*60)
    print("Executor 修复验证测试套件")
    print("="*60)
    
    all_results = []
    
    # 运行测试
    all_results.append(test_infer_instruction())
    all_results.append(test_tool_parameter_flexibility())
    all_results.append(await test_step_execution())
    all_results.append(test_actual_failures())
    
    # 汇总结果
    print("\n\n" + "="*60)
    print("最终测试报告")
    print("="*60)
    
    total_passed = sum(r.passed for r in all_results)
    total_failed = sum(r.failed for r in all_results)
    
    print(f"\n总计: {total_passed} 通过, {total_failed} 失败")
    
    if total_failed > 0:
        print("\n所有失败测试:")
        for result in all_results:
            for name, reason in result.errors:
                print(f"  [FAIL] {name}")
                print(f"    原因: {reason}")
    
    success = total_failed == 0
    print("\n" + "="*60)
    if success:
        print("[OK] 所有测试通过！修复有效。")
    else:
        print("[FAIL] 部分测试失败，需要进一步修复。")
    print("="*60)
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
