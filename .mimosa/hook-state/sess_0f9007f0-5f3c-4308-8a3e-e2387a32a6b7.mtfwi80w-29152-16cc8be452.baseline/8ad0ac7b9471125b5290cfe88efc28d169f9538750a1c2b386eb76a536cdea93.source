#!/usr/bin/env python3
"""
端到端测试 - 测试完整的AI工作流程
"""

import asyncio
import sys
from pathlib import Path

# 添加python目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

from dotenv import load_dotenv
import os

# 加载环境变量
load_dotenv()


async def test_full_workflow():
    """测试完整工作流程"""
    print("=" * 60)
    print("端EAS 2.0 - 到端工作流程测试")
    print("=" * 60)
    print()
    
    # 初始化服务
    print("🔧 初始化服务...")
    from llm_client import LLMClient, OpenAICompatibleProvider
    from designer import DefaultDesigner
    import yaml
    
    # 加载配置
    with open("config/models.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    # 创建LLM客户端
    llm_client = LLMClient()
    
    # 从配置文件注册所有provider
    api_keys = config.get("api_keys", {})
    base_urls = config.get("base_urls", {})
    
    for provider_name, api_key in api_keys.items():
        if api_key and provider_name in base_urls:
            base_url = base_urls[provider_name]
            llm_client.register_provider(
                OpenAICompatibleProvider(provider_name, api_key, base_url)
            )
    
    # 创建Designer
    designer = DefaultDesigner(llm_client, config["models"]["designer"])
    print("✅ 服务初始化完成")
    print()
    
    # 测试1: 生成设计书
    print("-" * 60)
    print("测试1: 生成设计书")
    print("-" * 60)
    
    user_request = "创建一个简单的待办事项Web应用，用户可以添加、删除和标记完成待办事项"
    print(f"用户需求: {user_request}")
    print()
    print("⏳ 正在生成设计书...")
    
    try:
        design = await designer.generate_design(user_request)
        
        print("✅ 设计书生成成功！")
        print()
        print(f"📋 设计ID: {design.design_id}")
        print(f"📅 生成时间: {design.timestamp}")
        print()
        print("🏗️ 架构组件:")
        for component in design.part1_design.architecture.components:
            print(f"  - {component}")
        print()
        print("🔗 架构交互:")
        for interaction in design.part1_design.architecture.interactions:
            print(f"  - {interaction}")
        print()
        print(f"📊 整体难度: {design.part1_design.difficulty.overall}")
        print()
        print("🔧 所需工具:")
        for tool in design.part2_tool_assessment.required_tools:
            print(f"  - {tool.tool_id} (可用性: {tool.availability})")
        print()
        print(f"✅ 工具完备度: {design.part2_tool_assessment.overall_tool_completeness * 100:.0f}%")
        print()
        
        if design.part2_tool_assessment.risks:
            print("⚠️ 潜在风险:")
            for risk in design.part2_tool_assessment.risks:
                print(f"  - {risk}")
            print()
        
    except Exception as e:
        print(f"❌ 设计书生成失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 测试2: 生成执行计划
    print("-" * 60)
    print("测试2: 生成执行计划")
    print("-" * 60)
    print()
    print("⏳ 正在生成执行计划...")
    
    try:
        plan = await designer.generate_plan(design)
        
        print("✅ 执行计划生成成功！")
        print()
        print(f"📋 计划ID: {plan.plan_id}")
        print(f"📅 创建时间: {plan.created_at}")
        print(f"📊 状态: {plan.status}")
        print()
        print(f"📦 共有 {len(plan.segments)} 个阶段:")
        print()
        
        for i, segment in enumerate(plan.segments, 1):
            print(f"  阶段 {i}: {segment.description}")
            print(f"  └─ 共 {len(segment.steps)} 个步骤:")
            for step in segment.steps:
                print(f"     {step.order}. {step.description}")
                print(f"        工具: {step.tool}")
                print(f"        验证: {step.check_criteria}")
            print()
        
        if plan.implementation_list:
            print("📝 关键实现文件:")
            for file in plan.implementation_list:
                print(f"  - {file}")
            print()
        
    except Exception as e:
        print(f"❌ 执行计划生成失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 总结
    print("=" * 60)
    print("✅ 端到端测试完成！")
    print()
    print("测试结果:")
    print("  ✅ 设计书生成 - 成功")
    print("  ✅ 执行计划生成 - 成功")
    print()
    print("下一步:")
    print("  1. 实现Executor来执行这些步骤")
    print("  2. 实现GUI界面来显示设计书和计划")
    print("  3. 添加用户审阅和修改功能")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_full_workflow())
