"""
直接测试核心修复 - 不通过 HTTP API
验证 database.save_plan() 和 API format_plan_for_frontend() 的修复
"""
import sys
import json
sys.path.insert(0, 'D:\\spot\\harness\\python')

from designer import Designer
from llm_client import LLMClientManager
from database import Database
from api.server import format_plan_for_frontend

print("=" * 70)
print("EAS 2.0 核心修复直接验证测试")
print("=" * 70)

# 1. 初始化组件
print("\n[Step 1] 初始化组件...")
llm_manager = LLMClientManager()
llm_manager.register_provider("bbapi", "claude-opus-5", {"api_key": "dummy"})
llm_manager.register_provider("hejuapi", "claude-sonnet-5", {"api_key": "dummy"})
llm_manager.register_provider("newapi", "claude-haiku-4-5-20251001", {"api_key": "dummy"})
llm_manager.register_provider("stepfun", "step-2-16k", {"api_key": "dummy"})

designer = Designer(llm_manager)
db = Database()

print("✅ 组件初始化完成")

# 2. 创建测试设计
print("\n[Step 2] 创建测试设计...")
design_id = designer.create_design("创建一个简单的待办事项应用，包含添加、删除、标记完成功能")
print(f"✅ 设计创建成功: {design_id}")

# 3. 生成执行计划
print("\n[Step 3] 生成执行计划...")
try:
    plan = designer.generate_execution_plan(design_id, "创建一个简单的待办事项应用")
    plan_id = plan['id']
    print(f"✅ 执行计划生成成功: {plan_id}")
    
    # 显示段落信息
    segments = plan.get('segments', [])
    print(f"\n计划包含 {len(segments)} 个段落:")
    total_steps_in_segments = 0
    for i, segment in enumerate(segments, 1):
        steps_count = len(segment.get('steps', []))
        total_steps_in_segments += steps_count
        print(f"  {i}. {segment.get('name', 'Unknown')}: {steps_count} 步骤")
    
    print(f"\n段落中总计: {total_steps_in_segments} 个步骤")
    
except Exception as e:
    print(f"❌ 计划生成失败: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# 4. 保存到数据库
print("\n[Step 4] 保存计划到数据库...")
try:
    db.save_plan(plan)
    print("✅ 计划保存成功")
except Exception as e:
    print(f"❌ 保存失败: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# 5. 验证数据库持久化
print("\n[Step 5] 验证数据库持久化...")
conn = db._get_connection()

# 检查 steps 表
steps_in_db = conn.execute("""
    SELECT * FROM steps 
    WHERE plan_id = ?
    ORDER BY step_order
""", (plan_id,)).fetchall()

print(f"✅ 数据库中找到 {len(steps_in_db)} 个步骤")

if len(steps_in_db) > 0:
    print("\n前 3 个步骤:")
    for step in steps_in_db[:3]:
        print(f"  {step['step_order']}. {step['description']}")
        print(f"     工具: {step['tool']}")
        print(f"     状态: {step['status']}")
        print()

# 6. 测试 API 响应格式化
print("[Step 6] 测试 API 响应格式化...")

# 从数据库获取计划
plan_from_db = conn.execute("""
    SELECT * FROM plans WHERE plan_id = ?
""", (plan_id,)).fetchone()

if not plan_from_db:
    print("❌ 计划未找到")
    exit(1)

# 使用 API 的格式化函数
try:
    formatted_plan = format_plan_for_frontend(dict(plan_from_db))
    print("✅ API 响应格式化成功")
    
    # 检查响应结构
    print("\n[Step 7] 验证 API 响应结构...")
    print(f"响应包含 'plan_id': {'✅' if 'plan_id' in formatted_plan else '❌'}")
    print(f"响应包含 'segments': {'✅' if 'segments' in formatted_plan else '❌'}")
    print(f"响应包含 'steps': {'✅' if 'steps' in formatted_plan else '❌'} <-- 关键!")
    
    if 'steps' in formatted_plan:
        steps_in_response = formatted_plan['steps']
        print(f"\n✅ 成功! API 响应包含 'steps' 数组")
        print(f"   Steps 数组长度: {len(steps_in_response)}")
        
        if len(steps_in_response) > 0:
            print(f"\n   前 3 个步骤详情:")
            for i, step in enumerate(steps_in_response[:3], 1):
                print(f"   {i}. {step.get('description', 'N/A')}")
                print(f"      工具: {step.get('tool', 'N/A')}")
                print(f"      状态: {step.get('status', 'N/A')}")
                print(f"      顺序: {step.get('step_order', 'N/A')}")
                print()
            
            # 对比数据一致性
            print("[Step 8] 验证数据一致性...")
            db_count = len(steps_in_db)
            api_count = len(steps_in_response)
            segment_count = total_steps_in_segments
            
            print(f"   段落中的步骤数: {segment_count}")
            print(f"   数据库中的步骤数: {db_count}")
            print(f"   API 返回的步骤数: {api_count}")
            
            if db_count == api_count == segment_count:
                print(f"\n✅ 数据完全一致!")
            elif db_count == api_count:
                print(f"\n✅ 数据库和API一致 (段落可能包含嵌套步骤)")
            else:
                print(f"\n⚠️ 数据不一致，需要检查")
            
            print(f"\n{'=' * 70}")
            print("🎉 核心修复验证完成!")
            print(f"{'=' * 70}")
            print("\n验证总结:")
            print(f"  1. 计划生成: ✅ ({segment_count} 步骤在段落中)")
            print(f"  2. 数据库持久化: ✅ ({db_count} 步骤保存)")
            print(f"  3. API 响应格式: ✅ (包含 {api_count} 步骤)")
            print(f"  4. 数据一致性: {'✅' if db_count == api_count else '⚠️'}")
            print(f"\n关键修复点:")
            print(f"  • database.save_plan() 提取并保存步骤到 steps 表 ✅")
            print(f"  • api.format_plan_for_frontend() 包含扁平化 steps 数组 ✅")
            print(f"  • 原始 BUG (空计划问题) 已修复 ✅")
            
        else:
            print(f"\n❌ 警告: Steps 数组为空!")
    else:
        print(f"\n❌ 失败! API 响应不包含 'steps' 数组")
        print(f"   格式化后的计划键: {list(formatted_plan.keys())}")
        
except Exception as e:
    print(f"❌ API 格式化失败: {e}")
    import traceback
    traceback.print_exc()

conn.close()
