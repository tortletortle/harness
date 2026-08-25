"""
简化测试：只测试数据库更新逻辑，不实际执行步骤
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from database import Database


def test_update_step_logic():
    """测试update_step是否正常工作"""
    print("="*60)
    print("测试: 数据库步骤状态更新")
    print("="*60)
    
    # 1. 初始化数据库
    print("\n[1] 初始化数据库...")
    db = Database()
    print("[OK] 数据库初始化完成")
    
    # 2. 获取测试计划
    print("\n[2] 获取测试计划...")
    plan_id = "5abe01d6-107b-43f4-a9d5-095c025b48ac"
    plan = db.get_plan(plan_id)
    
    if not plan:
        print("[FAIL] 计划不存在")
        print("       请先运行 run_full_verification.py 创建测试计划")
        return False
    
    print(f"[OK] 获取计划: {plan.plan_id}")
    print(f"     segments: {len(plan.segments)}")
    
    # 3. 显示初始状态
    print("\n[3] 初始步骤状态:")
    steps = db.get_steps(plan_id)
    for i, step in enumerate(steps, 1):
        print(f"  步骤{i}: {step['description'][:50]}")
        print(f"         状态: {step['status']}")
        print(f"         step_id: {step['step_id']}")
    
    if not steps:
        print("[FAIL] 没有步骤")
        return False
    
    # 4. 测试更新步骤状态
    print("\n[4] 测试更新步骤状态...")
    
    step1_id = steps[0]['step_id']
    print(f"\n  测试步骤 1: {step1_id}")
    
    # 测试1: 更新为 RUNNING
    print(f"    → 更新为 RUNNING...")
    success1 = db.update_step(
        step_id=step1_id,
        status="RUNNING",
        executed_at=datetime.now()
    )
    print(f"    → 结果: {success1}")
    
    # 验证
    steps = db.get_steps(plan_id)
    current_status = steps[0]['status']
    print(f"    → 当前状态: {current_status}")
    
    if current_status.upper() == "RUNNING":
        print("    ✓ 状态更新成功")
    else:
        print(f"    ✗ 状态未更新 (仍然是 {current_status})")
        return False
    
    # 测试2: 更新为 COMPLETED
    print(f"\n    → 更新为 COMPLETED...")
    success2 = db.update_step(
        step_id=step1_id,
        status="COMPLETED",
        outputs={"success": True, "message": "测试成功"},
        executed_at=datetime.now()
    )
    print(f"    → 结果: {success2}")
    
    # 验证
    steps = db.get_steps(plan_id)
    current_status = steps[0]['status']
    current_outputs = steps[0].get('outputs')
    print(f"    → 当前状态: {current_status}")
    print(f"    → 输出: {current_outputs}")
    
    if current_status.upper() == "COMPLETED":
        print("    ✓ 状态更新成功")
    else:
        print(f"    ✗ 状态未更新 (仍然是 {current_status})")
        return False
    
    if current_outputs:
        print("    ✓ 输出保存成功")
    else:
        print("    ✗ 输出未保存")
        return False
    
    # 5. 如果有第二个步骤，测试 FAILED
    if len(steps) > 1:
        step2_id = steps[1]['step_id']
        print(f"\n  测试步骤 2: {step2_id}")
        
        print(f"    → 更新为 FAILED...")
        success3 = db.update_step(
            step_id=step2_id,
            status="FAILED",
            outputs={"error": "测试失败"},
            executed_at=datetime.now()
        )
        print(f"    → 结果: {success3}")
        
        # 验证
        steps = db.get_steps(plan_id)
        current_status = steps[1]['status']
        print(f"    → 当前状态: {current_status}")
        
        if current_status.upper() == "FAILED":
            print("    ✓ 状态更新成功")
        else:
            print(f"    ✗ 状态未更新 (仍然是 {current_status})")
            return False
    
    # 6. 最终验证
    print("\n[5] 最终状态:")
    steps = db.get_steps(plan_id)
    
    for i, step in enumerate(steps, 1):
        print(f"  步骤{i}: {step['description'][:50]}")
        print(f"         状态: {step['status']}")
        print(f"         执行时间: {step.get('executed_at', 'N/A')}")
        if step.get('outputs'):
            print(f"         输出: {str(step['outputs'])[:60]}...")
    
    # 统计
    pending = sum(1 for s in steps if s['status'].lower() == 'pending')
    running = sum(1 for s in steps if s['status'].lower() == 'running')
    completed = sum(1 for s in steps if s['status'].lower() == 'completed')
    failed = sum(1 for s in steps if s['status'].lower() == 'failed')
    
    print(f"\n状态统计:")
    print(f"  PENDING:   {pending}")
    print(f"  RUNNING:   {running}")
    print(f"  COMPLETED: {completed}")
    print(f"  FAILED:    {failed}")
    
    if pending < len(steps):
        print("\n[OK] 数据库更新逻辑正常工作！")
        print("\n结论:")
        print("  ✓ update_step() 方法可以正确更新状态")
        print("  ✓ 状态变化可以持久化到数据库")
        print("  ✓ 输出数据可以正确保存")
        print("\n问题可能在于:")
        print("  → execute_plan_background() 函数没有被调用")
        print("  → 或者 executor 没有正确初始化")
        print("  → 或者后台任务执行时出现异常")
        return True
    else:
        print("\n[FAIL] 数据库更新逻辑有问题")
        return False


def main():
    success = test_update_step_logic()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
