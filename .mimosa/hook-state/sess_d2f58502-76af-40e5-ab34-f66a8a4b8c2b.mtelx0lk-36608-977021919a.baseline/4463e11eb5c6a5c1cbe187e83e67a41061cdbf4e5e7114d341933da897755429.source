#!/usr/bin/env python3
"""
完整执行流程测试
生成设计书 -> 生成执行计划 -> 执行计划
"""

import asyncio
import sys
from pathlib import Path

# 添加python目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import yaml
from llm_client import LLMClient, OpenAICompatibleProvider
from designer import DefaultDesigner
from executor import DefaultExecutor
from models import StepStatus


async def execute_plan(executor, plan):
    """执行整个计划"""
    print()
    print("=" * 70)
    print("开始执行计划")
    print("=" * 70)
    print()
    
    total_steps = sum(len(seg.steps) for seg in plan.segments)
    completed_steps = 0
    failed_steps = 0
    
    for seg_idx, segment in enumerate(plan.segments, 1):
        print(f"📦 阶段 {seg_idx}/{len(plan.segments)}: {segment.description}")
        print(f"   共 {len(segment.steps)} 个步骤")
        print()
        
        for step in segment.steps:
            result = await executor.execute_step(step)
            
            if result["success"] and result["check_passed"]:
                step.status = StepStatus.SUCCESS
                completed_steps += 1
            else:
                step.status = StepStatus.FAILED
                failed_steps += 1
                
                # 询问是否继续
                print()
                print(f"⚠️  步骤失败，是否继续执行？(y/n)")
                # 自动继续（测试模式）
                print("   [自动继续]")
                print()
        
        print()
    
    print("=" * 70)
    print("执行完成")
    print("=" * 70)
    print()
    print(f"✅ 成功: {completed_steps}/{total_steps}")
    print(f"❌ 失败: {failed_steps}/{total_steps}")
    print()
    
    return {
        "total": total_steps,
        "completed": completed_steps,
        "failed": failed_steps
    }


async def main():
    """主函数"""
    print("=" * 70)
    print("  EAS 2.0 - 完整执行流程测试")
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
    
    # 创建Designer和Executor
    print("🎨 创建Designer...")
    designer = DefaultDesigner(llm_client, config["models"]["designer"])
    
    print("⚙️  创建Executor...")
    executor = DefaultExecutor(llm_client, config["models"]["executor"])
    
    print("✅ 初始化完成")
    print()
    
    # 测试用例：创建一个简单的项目
    user_request = "创建一个简单的Hello World项目，包含一个Python文件输出Hello World"
    
    print("=" * 70)
    print(f"用户需求: {user_request}")
    print("=" * 70)
    print()
    
    try:
        # 步骤1: 生成设计书
        print("📝 步骤 1/3: 生成设计书...")
        design = await designer.generate_design(user_request)
        
        print("✅ 设计书生成成功!")
        print(f"  - 设计ID: {design.design_id}")
        print(f"  - 整体难度: {design.part1_design.difficulty.overall}")
        print(f"  - 架构组件数: {len(design.part1_design.architecture.components)}")
        print()
        
        # 步骤2: 生成执行计划
        print("📋 步骤 2/3: 生成执行计划...")
        plan = await designer.generate_plan(design)
        
        total_steps = sum(len(seg.steps) for seg in plan.segments)
        print("✅ 执行计划生成成功!")
        print(f"  - 计划ID: {plan.plan_id}")
        print(f"  - 执行阶段数: {len(plan.segments)}")
        print(f"  - 总步骤数: {total_steps}")
        print()
        
        # 显示计划概览
        print("📦 计划概览:")
        for i, segment in enumerate(plan.segments, 1):
            print(f"  阶段 {i}: {segment.description}")
            print(f"    └─ {len(segment.steps)} 个步骤")
            for j, step in enumerate(segment.steps[:3], 1):
                print(f"       {j}. {step.description}")
            if len(segment.steps) > 3:
                print(f"       ... (共 {len(segment.steps)} 个)")
        print()
        
        # 步骤3: 执行计划
        print("⚙️  步骤 3/3: 执行计划...")
        print()
        
        # 询问是否执行
        print("是否执行该计划？(y/n)")
        # 自动执行（测试模式）
        print("   [自动执行]")
        print()
        
        result = await execute_plan(executor, plan)
        
        print("🎉 完整流程测试完成!")
        print()
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        print()
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print("👋 用户中断")
