#!/usr/bin/env python3
"""
EAS 2.0 - 命令行界面

简单的CLI工具，用于测试和使用EAS系统
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


async def main():
    """主函数"""
    print("=" * 70)
    print("  EAS 2.0 - AI任务编排系统")
    print("  简单命令行界面")
    print("=" * 70)
    print()
    
    # 初始化服务
    print("🔧 正在初始化AI服务...")
    from llm_client import LLMClient, OpenAICompatibleProvider
    from designer import DefaultDesigner
    import yaml
    
    try:
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
        print("✅ AI服务初始化完成")
        print()
        
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        return
    
    # 主循环
    while True:
        print("-" * 70)
        print("请输入您的需求 (或输入 'quit' 退出):")
        print()
        
        # 获取用户输入
        user_request = input("> ").strip()
        
        if user_request.lower() in ['quit', 'exit', 'q']:
            print()
            print("👋 再见！")
            break
        
        if not user_request:
            continue
        
        print()
        print("=" * 70)
        print("步骤 1/2: 生成设计书")
        print("=" * 70)
        print()
        print("⏳ AI正在分析您的需求...")
        
        try:
            # 生成设计书
            design = await designer.generate_design(user_request)
            
            print()
            print("✅ 设计书生成完成！")
            print()
            print("📋 设计摘要:")
            print(f"  - 设计ID: {design.design_id}")
            print(f"  - 整体难度: {design.part1_design.difficulty.overall}")
            print()
            
            print("🏗️ 架构组件:")
            for i, component in enumerate(design.part1_design.architecture.components[:5], 1):
                print(f"  {i}. {component}")
            if len(design.part1_design.architecture.components) > 5:
                print(f"  ... (共 {len(design.part1_design.architecture.components)} 个)")
            print()
            
            print("🔧 所需工具:")
            for tool in design.part2_tool_assessment.required_tools[:5]:
                print(f"  - {tool.tool_id} [{tool.availability}]")
            if len(design.part2_tool_assessment.required_tools) > 5:
                print(f"  ... (共 {len(design.part2_tool_assessment.required_tools)} 个)")
            print()
            
            print(f"✅ 工具完备度: {design.part2_tool_assessment.overall_tool_completeness * 100:.0f}%")
            print()
            
            # 询问是否继续
            print("-" * 70)
            proceed = input("是否生成执行计划? (y/n): ").strip().lower()
            
            if proceed != 'y':
                print("⏸️ 已跳过执行计划生成")
                print()
                continue
            
            print()
            print("=" * 70)
            print("步骤 2/2: 生成执行计划")
            print("=" * 70)
            print()
            print("⏳ AI正在制定执行计划...")
            
            # 生成执行计划
            plan = await designer.generate_plan(design)
            
            print()
            print("✅ 执行计划生成完成！")
            print()
            print(f"📋 计划ID: {plan.plan_id}")
            print(f"📊 共有 {len(plan.segments)} 个执行阶段")
            print()
            
            total_steps = sum(len(seg.steps) for seg in plan.segments)
            print(f"📝 总步骤数: {total_steps}")
            print()
            
            print("📦 阶段概览:")
            for i, segment in enumerate(plan.segments, 1):
                print(f"  阶段 {i}: {segment.description}")
                print(f"    └─ {len(segment.steps)} 个步骤")
            print()
            
            if plan.implementation_list:
                print("📝 关键文件:")
                for file in plan.implementation_list[:10]:
                    print(f"  - {file}")
                if len(plan.implementation_list) > 10:
                    print(f"  ... (共 {len(plan.implementation_list)} 个)")
                print()
            
            print("💡 提示: 设计书和计划已生成，可以保存或执行")
            print()
            
        except Exception as e:
            print()
            print(f"❌ 错误: {e}")
            print()
            import traceback
            traceback.print_exc()
            continue


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print()
        print("👋 用户中断，再见！")
