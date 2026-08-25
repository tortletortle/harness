#!/usr/bin/env python3
"""
测试完整工作流程
生成设计书 -> 生成执行计划
"""

import asyncio
import sys
from pathlib import Path

# 添加python目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import yaml
from llm_client import LLMClient, OpenAICompatibleProvider
from designer import DefaultDesigner


async def main():
    """主函数"""
    print("=" * 70)
    print("  EAS 2.0 - 完整工作流程测试")
    print("=" * 70)
    print()
    
    # 加载配置
    print("📋 加载配置...")
    try:
        with open("config/models.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"❌ 无法加载配置文件: {e}")
        return
    
    # 创建LLM客户端
    print("🔧 初始化LLM客户端...")
    llm_client = LLMClient()
    
    # 注册所有provider
    api_keys = config.get("api_keys", {})
    base_urls = config.get("base_urls", {})
    
    for provider_name, api_key in api_keys.items():
        if api_key and provider_name in base_urls:
            base_url = base_urls[provider_name]
            llm_client.register_provider(
                OpenAICompatibleProvider(provider_name, api_key, base_url)
            )
    
    # 创建Designer
    print("🎨 创建Designer...")
    designer = DefaultDesigner(llm_client, config["models"]["designer"])
    print("✅ 初始化完成")
    print()
    
    # 测试用例
    test_cases = [
        "创建一个简单的待办事项应用",
        "制作一个计算器程序",
    ]
    
    for i, user_request in enumerate(test_cases, 1):
        print("=" * 70)
        print(f"测试用例 {i}/{len(test_cases)}: {user_request}")
        print("=" * 70)
        print()
        
        try:
            # 生成设计书
            print("📝 步骤 1: 生成设计书...")
            design = await designer.generate_design(user_request)
            
            print("✅ 设计书生成成功!")
            print(f"  - 设计ID: {design.design_id}")
            print(f"  - 整体难度: {design.part1_design.difficulty.overall}")
            print(f"  - 架构组件数: {len(design.part1_design.architecture.components)}")
            print(f"  - 所需工具数: {len(design.part2_tool_assessment.required_tools)}")
            print(f"  - 工具完备度: {design.part2_tool_assessment.overall_tool_completeness * 100:.0f}%")
            print()
            
            # 显示架构组件
            print("🏗️ 架构组件:")
            for j, component in enumerate(design.part1_design.architecture.components[:3], 1):
                print(f"  {j}. {component}")
            if len(design.part1_design.architecture.components) > 3:
                print(f"  ... (共 {len(design.part1_design.architecture.components)} 个)")
            print()
            
            # 生成执行计划
            print("📋 步骤 2: 生成执行计划...")
            plan = await designer.generate_plan(design)
            
            print("✅ 执行计划生成成功!")
            print(f"  - 计划ID: {plan.plan_id}")
            print(f"  - 执行阶段数: {len(plan.segments)}")
            
            total_steps = sum(len(seg.steps) for seg in plan.segments)
            print(f"  - 总步骤数: {total_steps}")
            print()
            
            # 显示阶段概览
            print("📦 阶段概览:")
            for j, segment in enumerate(plan.segments[:3], 1):
                print(f"  阶段 {j}: {segment.description}")
                print(f"    └─ {len(segment.steps)} 个步骤")
            if len(plan.segments) > 3:
                print(f"  ... (共 {len(plan.segments)} 个阶段)")
            print()
            
            print(f"✅ 测试用例 {i} 完成!")
            
        except Exception as e:
            print(f"❌ 测试用例 {i} 失败: {e}")
            import traceback
            traceback.print_exc()
        
        print()
    
    print("=" * 70)
    print("测试完成!")
    print("=" * 70)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print("👋 用户中断")
