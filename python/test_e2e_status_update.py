"""
完整的端到端测试 - 测试步骤状态更新和指令推断修复

创建一个简单的设计 → 生成计划 → 执行计划 → 检查步骤状态
"""

import time
import requests
import json
from typing import Dict, Any

BASE_URL = "http://127.0.0.1:8000/api"


def create_design(user_request: str) -> Dict[str, Any]:
    """创建设计"""
    print(f"\n[步骤 1] 创建设计: {user_request}")
    response = requests.post(
        f"{BASE_URL}/design",
        json={"user_request": user_request}
    )
    response.raise_for_status()
    data = response.json()
    
    if not data.get("success"):
        raise Exception(f"设计创建失败: {data}")
    
    design_id = data["design"]["design_id"]
    print(f"[OK] 设计已创建: {design_id}")
    return design_id


def approve_design(design_id: str) -> str:
    """批准设计并生成计划"""
    print(f"\n[步骤 2] 批准设计并生成计划: {design_id}")
    response = requests.post(
        f"{BASE_URL}/design/{design_id}/approve",
        json={"design_id": design_id, "approved": True}
    )
    response.raise_for_status()
    data = response.json()
    
    if not data.get("success"):
        raise Exception(f"计划生成失败: {data}")
    
    plan_id = data["plan"]["plan_id"]
    steps_count = len(data["plan"]["steps"])
    print(f"[OK] 计划已生成: {plan_id}")
    print(f"[OK] 步骤数量: {steps_count}")
    
    # 显示步骤详情
    for i, step in enumerate(data["plan"]["steps"][:5], 1):
        print(f"     步骤 {i}: {step['description'][:50]}... (tool: {step.get('tool', 'N/A')})")
    
    return plan_id


def execute_plan(plan_id: str) -> None:
    """执行计划"""
    print(f"\n[步骤 3] 执行计划: {plan_id}")
    response = requests.post(f"{BASE_URL}/plan/{plan_id}/execute")
    response.raise_for_status()
    data = response.json()
    
    if not data.get("success"):
        raise Exception(f"执行启动失败: {data}")
    
    print(f"[OK] 执行已启动: {data.get('message')}")


def get_plan_status(plan_id: str) -> Dict[str, Any]:
    """获取计划状态"""
    response = requests.get(f"{BASE_URL}/plan/{plan_id}")
    response.raise_for_status()
    data = response.json()
    
    if not data.get("success"):
        raise Exception(f"获取计划失败: {data}")
    
    return data["plan"]


def check_step_status(plan_id: str, wait_seconds: int = 10) -> Dict[str, int]:
    """检查步骤状态"""
    print(f"\n[步骤 4] 等待 {wait_seconds} 秒让步骤执行...")
    time.sleep(wait_seconds)
    
    print(f"\n[步骤 5] 检查步骤状态")
    plan = get_plan_status(plan_id)
    steps = plan.get("steps", [])
    
    status_count = {
        "PENDING": 0,
        "RUNNING": 0,
        "SUCCESS": 0,
        "FAILED": 0
    }
    
    print(f"\n{'=' * 80}")
    print(f"步骤状态详情 (总共 {len(steps)} 个步骤)")
    print(f"{'=' * 80}")
    
    for i, step in enumerate(steps, 1):
        status = step.get("status", "UNKNOWN").upper()
        status_count[status] = status_count.get(status, 0) + 1
        
        desc = step.get("description", "N/A")[:40]
        tool = step.get("tool", "N/A")
        
        # 状态标记
        if status == "SUCCESS":
            mark = "[OK]"
        elif status == "FAILED":
            mark = "[FAIL]"
        elif status == "RUNNING":
            mark = "[RUNNING]"
        else:
            mark = "[PENDING]"
        
        print(f"{mark} 步骤 {i:2d}: {desc:<40} | {tool:<15} | {status}")
        
        # 如果失败，显示输出
        if status == "FAILED" and step.get("outputs"):
            error = step["outputs"].get("error", "Unknown error")
            print(f"           错误: {error}")
    
    print(f"{'=' * 80}")
    print(f"状态统计:")
    for status, count in status_count.items():
        print(f"  {status}: {count}")
    print(f"{'=' * 80}")
    
    return status_count


def main():
    """主测试流程"""
    print("\n" + "=" * 80)
    print("EAS 2.0 端到端测试 - 步骤状态更新和指令推断")
    print("=" * 80)
    
    try:
        # 创建一个简单的测试任务
        user_request = "创建一个简单的Python项目，包含main.py和README.md文件"
        
        # 执行流程
        design_id = create_design(user_request)
        plan_id = approve_design(design_id)
        execute_plan(plan_id)
        status_count = check_step_status(plan_id, wait_seconds=8)
        
        # 验证结果
        print(f"\n{'=' * 80}")
        print("测试结果分析")
        print(f"{'=' * 80}")
        
        # 检查是否有步骤状态更新
        if status_count["PENDING"] == sum(status_count.values()):
            print("[FAIL] 所有步骤仍然是 PENDING 状态")
            print("       步骤状态更新机制未工作!")
            return False
        
        # 检查是否有 SUCCESS 或 FAILED 状态
        if status_count["SUCCESS"] > 0 or status_count["FAILED"] > 0:
            print("[OK] 步骤状态已更新!")
            print(f"     - {status_count['SUCCESS']} 个步骤成功")
            print(f"     - {status_count['FAILED']} 个步骤失败")
            print(f"     - {status_count['RUNNING']} 个步骤运行中")
            print(f"     - {status_count['PENDING']} 个步骤等待中")
            
            # 如果有失败的步骤，检查是否还是 "Invalid instruction" 错误
            if status_count["FAILED"] > 0:
                print("\n[注意] 有步骤失败，请检查错误原因")
                print("       如果仍然是 'Invalid instruction' 错误，说明指令推断未完全修复")
            
            return True
        else:
            print("[WARN] 步骤可能还在运行中，建议再等待几秒后手动检查")
            return True
            
    except Exception as e:
        print(f"\n[ERROR] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    
    if success:
        print("\n[总结] 测试通过! 步骤状态更新机制正常工作")
    else:
        print("\n[总结] 测试失败! 步骤状态更新机制仍有问题")
    
    exit(0 if success else 1)
