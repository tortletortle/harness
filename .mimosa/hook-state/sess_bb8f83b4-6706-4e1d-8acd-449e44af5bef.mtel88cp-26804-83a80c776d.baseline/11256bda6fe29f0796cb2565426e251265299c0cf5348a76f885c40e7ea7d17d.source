#!/usr/bin/env python3
"""
快速测试执行计划生成稳定性（单个用例）
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import yaml
from llm_client import LLMClient, OpenAICompatibleProvider
from designer import DefaultDesigner


async def test_single_case():
    """测试单个用例"""
    print("=" * 70)
    print("  执行计划生成稳定性测试（单个用例）")
    print("=" * 70)
    print()
    
    # 加载配置
    with open("config/models.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    # 创建LLM客户端
    llm_client = LLMClient()
    api_keys = config.get("api_keys", {})
    base_urls = config.get("base_urls", {})
    
    for provider_name, api_key in api_keys.items():
        if api_key and provider_name in base_urls:
            llm_client.register_provider(
                OpenAICompatibleProvider(provider_name, api_key, base_urls[provider_name])
            )
    
    # 创建Designer
    designer = DefaultDesigner(llm_client, config["models"]["designer"])
    
    user_request = "创建一个简单的待办事项应用"
    
    print(f"测试用例: {user_request}")
    print(f"{'='*70}\n")
    
    try:
        # 生成设计书
        print("📝 生成设计书...")
        design = await designer.generate_design(user_request)
        print(f"✅ 设计书生成成功 (ID: {design.design_id})")
        print(f"   - 组件数: {len(design.part1_design.architecture.components)}")
        print(f"   - 难度: {design.part1_design.difficulty.overall}")
        
        # 生成执行计划（带重试）
        print("\n📋 生成执行计划（带自动重试和JSON修复）...")
        plan = await designer.generate_plan(design)
        
        # 检查是否是错误计划
        if len(plan.segments) == 1 and "错误" in plan.segments[0].description:
            print(f"❌ 执行计划生成失败")
            print(f"   错误信息: {plan.segments[0].steps[0].description}")
            return False
        else:
            print(f"✅ 执行计划生成成功")
            print(f"   - 阶段数: {len(plan.segments)}")
            total_steps = sum(len(seg.steps) for seg in plan.segments)
            print(f"   - 总步骤数: {total_steps}")
            
            # 显示第一个阶段的详细信息
            if plan.segments:
                first_seg = plan.segments[0]
                print(f"\n   第一阶段: {first_seg.description}")
                for i, step in enumerate(first_seg.steps[:3], 1):  # 只显示前3步
                    print(f"      步骤 {i}: {step.description}")
                    print(f"           工具: {step.tool}")
            
            return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    try:
        success = asyncio.run(test_single_case())
        print("\n" + "="*70)
        if success:
            print("🎉 测试通过！执行计划生成稳定")
        else:
            print("❌ 测试失败！需要进一步优化")
        print("="*70)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n👋 用户中断")
        sys.exit(1)
