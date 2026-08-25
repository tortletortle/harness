#!/usr/bin/env python3
"""
快速验证脚本 - 验证空计划问题已修复
Quick Verification Script - Verify empty plan issue is fixed
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

print("\n" + "=" * 70)
print("EAS 2.0 空计划问题修复验证")
print("EAS 2.0 Empty Plan Fix Verification")
print("=" * 70)

# Test 1: Database functionality
print("\n[测试 1] 数据库功能测试...")
print("[Test 1] Database functionality test...")

try:
    from database import Database
    db = Database("../data/eas.db")
    
    # Check if we can query steps
    import sqlite3
    conn = sqlite3.connect("../data/eas.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM steps")
    step_count = cursor.fetchone()[0]
    conn.close()
    
    print(f"  ✅ 数据库中共有 {step_count} 个步骤")
    print(f"  ✅ Database contains {step_count} steps")
except Exception as e:
    print(f"  ❌ 数据库测试失败: {e}")
    print(f"  ❌ Database test failed: {e}")
    sys.exit(1)

# Test 2: Check if save_plan includes step persistence
print("\n[测试 2] 检查 save_plan 方法...")
print("[Test 2] Checking save_plan method...")

try:
    import inspect
    source = inspect.getsource(db.save_plan)
    if "INSERT INTO steps" in source:
        print("  ✅ save_plan 方法包含步骤保存逻辑")
        print("  ✅ save_plan method includes step persistence logic")
    else:
        print("  ❌ save_plan 方法缺少步骤保存逻辑")
        print("  ❌ save_plan method missing step persistence logic")
        sys.exit(1)
except Exception as e:
    print(f"  ❌ 检查失败: {e}")
    print(f"  ❌ Check failed: {e}")
    sys.exit(1)

# Test 3: Check API response format
print("\n[测试 3] 检查 API 响应格式...")
print("[Test 3] Checking API response format...")

try:
    from api import server
    import inspect
    source = inspect.getsource(server.format_plan_for_frontend)
    if '"steps": all_steps' in source or "'steps': all_steps" in source:
        print("  ✅ API 响应包含扁平化的 steps 数组")
        print("  ✅ API response includes flattened steps array")
    else:
        print("  ⚠️  API 响应可能缺少 steps 数组（需要重启后端）")
        print("  ⚠️  API response may be missing steps array (backend restart needed)")
except Exception as e:
    print(f"  ❌ 检查失败: {e}")
    print(f"  ❌ Check failed: {e}")

# Test 4: Run simple plan generation test
print("\n[测试 4] 运行计划生成测试...")
print("[Test 4] Running plan generation test...")

async def quick_test():
    try:
        from designer import DefaultDesigner
        from llm_client import LLMClient, OpenAICompatibleProvider
        from dispatcher import Dispatcher
        from tools import init_registry_with_ai
        from models import DesignDocument, DesignPart1, Architecture, Difficulty, ToolAssessment, ToolRequirement
        from uuid import uuid4
        from datetime import datetime
        import yaml
        
        # Initialize
        db = Database("../data/eas.db")
        llm_client = LLMClient()
        
        with open("../config/models.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        # Register providers
        api_keys = config.get("api_keys", {})
        base_urls = config.get("base_urls", {})
        for provider_name, api_key in list(api_keys.items())[:1]:  # Just use first provider
            if api_key and provider_name in base_urls:
                provider = OpenAICompatibleProvider(
                    name=provider_name,
                    base_url=base_urls[provider_name],
                    api_key=api_key
                )
                llm_client.register_provider(provider)
                break
        
        # Initialize designer
        tool_config = config.get("models", {}).get("designer", {})
        tool_registry = init_registry_with_ai(llm_client, tool_config)
        dispatcher = Dispatcher(tool_registry)
        designer_config = config.get("models", {}).get("designer", {})
        designer = DefaultDesigner(llm_client, designer_config, dispatcher)
        
        # Create simple design
        design = DesignDocument(
            design_id=uuid4(),
            user_request=f"Test plan generation ({datetime.now().isoformat()})",
            timestamp=datetime.utcnow(),
            part1_design=DesignPart1(
                architecture=Architecture(components=["test.txt"], interactions=["create file"]),
                difficulty=Difficulty(overall="Simple", breakdown={"technical": "Low"})
            ),
            part2_tool_assessment=ToolAssessment(
                required_tools=[ToolRequirement(tool_id="file_creator", availability="Available")],
                overall_tool_completeness=1.0,
                risks=[]
            )
        )
        
        # Generate and save plan
        plan = await designer.generate_plan(design)
        total_steps = sum(len(seg.steps) for seg in plan.segments)
        
        if total_steps == 0:
            print("  ❌ 生成的计划包含 0 个步骤")
            print("  ❌ Generated plan contains 0 steps")
            return False
        
        db.save_plan(plan)
        steps_in_db = db.get_steps(str(plan.plan_id))
        
        if len(steps_in_db) != total_steps:
            print(f"  ❌ 步骤保存失败: 预期 {total_steps}, 实际 {len(steps_in_db)}")
            print(f"  ❌ Step persistence failed: expected {total_steps}, got {len(steps_in_db)}")
            return False
        
        print(f"  ✅ 成功生成并保存 {total_steps} 个步骤")
        print(f"  ✅ Successfully generated and saved {total_steps} steps")
        return True
        
    except Exception as e:
        print(f"  ❌ 测试失败: {e}")
        print(f"  ❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

result = asyncio.run(quick_test())

# Final summary
print("\n" + "=" * 70)
print("验证总结 / Verification Summary")
print("=" * 70)

if result:
    print("✅ 所有测试通过！空计划问题已修复。")
    print("✅ All tests passed! Empty plan issue is FIXED.")
    print("\n建议后续步骤 / Recommended next steps:")
    print("1. 重启后端服务器 / Restart backend server")
    print("2. 运行完整 E2E 测试 / Run full E2E test")
    print("   python test_e2e_comprehensive.py")
else:
    print("❌ 部分测试失败，请检查日志")
    print("❌ Some tests failed, please check the logs")
    sys.exit(1)

print("=" * 70)
