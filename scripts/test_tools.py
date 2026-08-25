#!/usr/bin/env python3
"""
直接测试工具执行
不依赖AI生成计划，手动创建简单步骤测试
"""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4
from datetime import datetime

# 添加python目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import yaml
from llm_client import LLMClient, OpenAICompatibleProvider
from executor import DefaultExecutor
from models import Step, StepStatus, ExecutionPlan, PlanSegment


async def main():
    """主函数"""
    print("=" * 70)
    print("  EAS 2.0 - 工具执行测试")
    print("=" * 70)
    print()
    
    # 加载配置
    print("📋 加载配置...")
    with open("config/models.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    # 创建LLM客户端
    print("🔧 初始化LLM客户端...")
    llm_client = LLMClient()
    
    api_keys = config.get("api_keys", {})
    base_urls = config.get("base_urls", {})
    
    for provider_name, api_key in api_keys.items():
        if api_key and provider_name in base_urls:
            llm_client.register_provider(
                OpenAICompatibleProvider(provider_name, api_key, base_urls[provider_name])
            )
    
    # 创建Executor
    print("⚙️  创建Executor...")
    executor = DefaultExecutor(llm_client, config["models"]["executor"])
    print("✅ 初始化完成")
    print()
    
    # 手动创建测试计划
    print("=" * 70)
    print("测试计划: 创建Hello World项目")
    print("=" * 70)
    print()
    
    test_steps = [
        Step(
            step_id=uuid4(),
            order=1,
            description="创建项目目录",
            tool="file_creator",
            inputs={
                "instruction": "create_directory",
                "path": "./test_hello_world"
            },
            check_criteria="目录存在",
            status=StepStatus.PENDING
        ),
        Step(
            step_id=uuid4(),
            order=2,
            description="创建main.py文件",
            tool="file_creator",
            inputs={
                "instruction": "create_file",
                "path": "./test_hello_world/main.py",
                "content": 'print("Hello World!")\n'
            },
            check_criteria="文件存在且包含Hello World",
            status=StepStatus.PENDING
        ),
        Step(
            step_id=uuid4(),
            order=3,
            description="创建README文件",
            tool="file_creator",
            inputs={
                "instruction": "create_file",
                "path": "./test_hello_world/README.md",
                "content": "# Hello World Project\n\nA simple Hello World project.\n"
            },
            check_criteria="README文件存在",
            status=StepStatus.PENDING
        ),
        Step(
            step_id=uuid4(),
            order=4,
            description="运行程序",
            tool="command_runner",
            inputs={
                "instruction": "run_command",
                "command": "python main.py",
                "cwd": "./test_hello_world"
            },
            check_criteria="输出包含Hello World",
            status=StepStatus.PENDING
        ),
    ]
    
    segment = PlanSegment(
        segment_id=uuid4(),
        description="创建并测试Hello World项目",
        steps=test_steps
    )
    
    plan = ExecutionPlan(
        plan_id=uuid4(),
        design_id=uuid4(),
        created_at=datetime.utcnow(),
        status="Pending",
        segments=[segment],
        implementation_list=["main.py", "README.md"]
    )
    
    # 执行计划
    print("开始执行...")
    print()
    
    success_count = 0
    fail_count = 0
    
    for step in test_steps:
        result = await executor.execute_step(step)
        
        if result["success"]:
            step.status = StepStatus.SUCCESS
            success_count += 1
        else:
            step.status = StepStatus.FAILED
            fail_count += 1
        
        print()
    
    print("=" * 70)
    print("执行完成")
    print("=" * 70)
    print()
    print(f"✅ 成功: {success_count}/{len(test_steps)}")
    print(f"❌ 失败: {fail_count}/{len(test_steps)}")
    print()
    
    if success_count == len(test_steps):
        print("🎉 所有步骤执行成功！")
        print()
        print("生成的文件：")
        print("  - test_hello_world/")
        print("  - test_hello_world/main.py")
        print("  - test_hello_world/README.md")
        print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print("👋 用户中断")
