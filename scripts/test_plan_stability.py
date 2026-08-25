#!/usr/bin/env python3
"""
测试执行计划生成的稳定性改进
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import yaml
from llm_client import LLMClient, OpenAICompatibleProvider
from designer import DefaultDesigner


async def test_plan_generation():
    """测试执行计划生成"""
    print("=" * 70)
    print("  执行计划生成稳定性测试")
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
    
    # 测试用例
    test_cases = [
        "创建一个简单的待办事项应用",
        "制作一个计算器程序",
        "开发一个博客系统",
    ]
    
    results = {
        "success": 0,
        "failed": 0,
        "total": len(test_cases)
    }
    
    for i, user_request in enumerate(test_cases, 1):
        print(f"\n{'='*70}")
        print(f"测试 {i}/{len(test_cases)}: {user_request}")
        print(f"{'='*70}\n")
        
        try:
            # 生成设计书
            print("📝 生成设计书...")
            design = await designer.generate_design(user_request)
            print(f"✅ 设计书生成成功 (ID: {design.design_id})")
            
            # 生成执行计划（带重试）
            print("\n📋 生成执行计划（带自动重试和JSON修复）...")
            plan = await designer.generate_plan(design)
            
            # 检查是否是错误计划
            if len(plan.segments) == 1 and "错误" in plan.segments[0].description:
                print(f"❌ 执行计划生成失败")
                results["failed"] += 1
            else:
                print(f"✅ 执行计划生成成功")
                print(f"   - 阶段数: {len(plan.segments)}")
                total_steps = sum(len(seg.steps) for seg in plan.segments)
                print(f"   - 总步骤数: {total_steps}")
                results["success"] += 1
            
        except Exception as e:
            print(f"❌ 测试失败: {e}")
            results["failed"] += 1
    
    # 汇总结果
    print(f"\n{'='*70}")
    print("测试结果汇总")
    print(f"{'='*70}\n")
    
    success_rate = (results["success"] / results["total"]) * 100
    print(f"总计: {results['success']}/{results['total']} 成功")
    print(f"成功率: {success_rate:.1f}%")
    print(f"失败: {results['failed']}")
    print()
    
    if success_rate >= 90:
        print("🎉 稳定性优秀！（≥90%）")
    elif success_rate >= 80:
        print("✅ 稳定性良好（80-90%）")
    elif success_rate >= 70:
        print("⚠️  稳定性一般（70-80%）")
    else:
        print("❌ 稳定性较差（<70%）")
    
    return results


if __name__ == "__main__":
    try:
        results = asyncio.run(test_plan_generation())
        sys.exit(0 if results["success"] == results["total"] else 1)
    except KeyboardInterrupt:
        print("\n👋 用户中断")
        sys.exit(1)
