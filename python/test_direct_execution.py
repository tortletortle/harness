"""
直接测试 execute_plan_background 函数
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from database import Database
from executor import DefaultExecutor
from llm_client import LLMClient
from tools import get_registry
from datetime import datetime
import yaml


async def test_direct_execution():
    """直接测试执行逻辑"""
    print("="*60)
    print("直接测试 execute_plan_background 逻辑")
    print("="*60)
    
    # 1. 初始化组件
    print("\n[1] 初始化组件...")
    db = Database()
    
    # 加载配置
    from pathlib import Path
    config_path = Path("config/models.yaml")
    if not config_path.exists():
        config_path = Path("../config/models.yaml")
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    # 创建 LLM 客户端和 Executor
    llm_client = LLMClient(config)
    executor = DefaultExecutor(
        llm_client=llm_client,
        model_config=config.get("models", {}).get("executor", {}),
        tool_registry=get_registry()
    )
    
    print("[OK] 组件初始化完成")
    
    # 2. 获取计划
    print("\n[2] 获取测试计划...")
    plan_id = "5abe01d6-107b-43f4-a9d5-095c025b48ac"
    plan = db.get_plan(plan_id)
    
    if not plan:
        print("[FAIL] 计划不存在")
        return False
    
    print(f"[OK] 获取计划: {plan.plan_id}")
    print(f"     segments: {len(plan.segments)}")
    
    # 3. 显示初始状态
    print("\n[3] 初始步骤状态:")
    steps = db.get_steps(plan_id)
    for step in steps:
        print(f"  - {step['description'][:50]}")
        print(f"    状态: {step['status']}")
    
    # 4. 执行计划（模拟 execute_plan_background）
    print("\n[4] 执行计划...")
    print("="*60)
    
    try:
        for i, segment in enumerate(plan.segments, 1):
            print(f"\n执行 Segment {i}/{len(plan.segments)}")
            
            for j, step in enumerate(segment.steps, 1):
                print(f"\n步骤 {j}/{len(segment.steps)}: {step.description[:50]}")
                
                # 更新状态为 RUNNING
                print(f"  → 更新状态为 RUNNING...")
                success = db.update_step(
                    step_id=str(step.step_id),
                    status="RUNNING",
                    executed_at=datetime.now()
                )
                print(f"  → 更新结果: {success}")
                
                # 执行步骤
                print(f"  → 执行步骤...")
                result = await executor.execute_step(step)
                print(f"  → 执行结果: success={result.get('success')}")
                
                # 根据结果更新状态
                if result.get("success"):
                    print(f"  → 更新状态为 COMPLETED...")
                    success = db.update_step(
                        step_id=str(step.step_id),
                        status="COMPLETED",
                        outputs=result.get("output"),
                        executed_at=datetime.now()
                    )
                    print(f"  → 更新结果: {success}")
                else:
                    error = result.get("error", "未知错误")
                    print(f"  → 执行失败: {error}")
                    print(f"  → 更新状态为 FAILED...")
                    success = db.update_step(
                        step_id=str(step.step_id),
                        status="FAILED",
                        outputs={"error": error},
                        executed_at=datetime.now()
                    )
                    print(f"  → 更新结果: {success}")
                    break
        
        print("\n" + "="*60)
        print("执行完成")
        
    except Exception as e:
        print(f"\n[ERROR] 执行时出错: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # 5. 显示最终状态
    print("\n[5] 最终步骤状态:")
    steps = db.get_steps(plan_id)
    for step in steps:
        print(f"  - {step['description'][:50]}")
        print(f"    状态: {step['status']}")
        print(f"    执行时间: {step.get('executed_at', 'N/A')}")
        if step.get('outputs'):
            print(f"    输出: {str(step['outputs'])[:60]}...")
    
    # 6. 验证
    print("\n[6] 验证结果...")
    pending_count = sum(1 for s in steps if s['status'].lower() == 'pending')
    completed_count = sum(1 for s in steps if s['status'].lower() == 'completed')
    failed_count = sum(1 for s in steps if s['status'].lower() == 'failed')
    
    print(f"  PENDING: {pending_count}")
    print(f"  COMPLETED: {completed_count}")
    print(f"  FAILED: {failed_count}")
    
    if pending_count < len(steps):
        print("\n[OK] 步骤状态已更新！")
        return True
    else:
        print("\n[FAIL] 步骤状态未更新")
        return False


async def main():
    success = await test_direct_execution()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
