"""
端到端测试: 验证步骤状态更新

创建一个计划并执行，验证步骤状态是否正确更新
"""

import asyncio
import sys
import os
import requests
import time
import json

BASE_URL = "http://127.0.0.1:8000"


def test_api_connection():
    """测试API连接"""
    try:
        response = requests.get(f"{BASE_URL}/", timeout=5)
        if response.status_code == 200:
            print("[OK] API服务器连接正常")
            return True
        else:
            print(f"[FAIL] API服务器响应异常: {response.status_code}")
            return False
    except Exception as e:
        print(f"[FAIL] 无法连接到API服务器: {e}")
        print("      请先启动服务器: python launch_server.py")
        return False


def create_design():
    """创建设计书"""
    print("\n[步骤1] 创建设计书...")
    try:
        response = requests.post(
            f"{BASE_URL}/api/design",
            json={"user_request": "创建一个简单的测试文件test.txt，内容为Hello World"},
            timeout=60
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                design_id = data["data"]["design"]["design_id"]
                print(f"[OK] 设计书创建成功: {design_id}")
                return design_id
            else:
                print(f"[FAIL] 设计书创建失败: {data.get('message')}")
                return None
        else:
            print(f"[FAIL] API调用失败: {response.status_code}")
            return None
    except Exception as e:
        print(f"[FAIL] 创建设计书时出错: {e}")
        return None


def approve_design(design_id):
    """批准设计书并生成计划"""
    print("\n[步骤2] 批准设计书并生成计划...")
    try:
        response = requests.post(
            f"{BASE_URL}/api/design/approve",
            json={"design_id": design_id, "approved": True},
            timeout=120
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                plan_id = data["data"]["plan_id"]
                print(f"[OK] 执行计划生成成功: {plan_id}")
                return plan_id
            else:
                print(f"[FAIL] 计划生成失败: {data.get('message')}")
                return None
        else:
            print(f"[FAIL] API调用失败: {response.status_code}")
            return None
    except Exception as e:
        print(f"[FAIL] 批准设计书时出错: {e}")
        return None


def get_plan_steps(plan_id):
    """获取计划的步骤"""
    print("\n[步骤3] 获取计划步骤...")
    try:
        response = requests.get(f"{BASE_URL}/api/plan/{plan_id}", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                plan = data["data"]["plan"]
                steps = plan.get("steps", [])
                print(f"[OK] 找到 {len(steps)} 个步骤")
                
                if len(steps) == 0:
                    print("[FAIL] 计划没有步骤！")
                    return []
                
                # 显示步骤信息
                for i, step in enumerate(steps, 1):
                    print(f"  步骤{i}: {step.get('description', 'N/A')[:60]}")
                    print(f"        状态: {step.get('status', 'N/A')}")
                
                return steps
            else:
                print(f"[FAIL] 获取计划失败: {data.get('message')}")
                return []
        else:
            print(f"[FAIL] API调用失败: {response.status_code}")
            return []
    except Exception as e:
        print(f"[FAIL] 获取计划步骤时出错: {e}")
        return []


def execute_plan(plan_id):
    """执行计划"""
    print("\n[步骤4] 执行计划...")
    try:
        response = requests.post(f"{BASE_URL}/api/execute/{plan_id}", timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                print(f"[OK] 计划开始执行")
                return True
            else:
                print(f"[FAIL] 执行失败: {data.get('message')}")
                return False
        else:
            print(f"[FAIL] API调用失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"[FAIL] 执行计划时出错: {e}")
        return False


def check_step_status(plan_id, max_wait=30):
    """检查步骤状态更新"""
    print("\n[步骤5] 监控步骤状态变化...")
    print(f"最多等待 {max_wait} 秒...")
    
    start_time = time.time()
    last_statuses = {}
    
    while time.time() - start_time < max_wait:
        try:
            response = requests.get(f"{BASE_URL}/api/plan/{plan_id}", timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    steps = data["data"]["plan"].get("steps", [])
                    
                    # 检查状态变化
                    for step in steps:
                        step_id = step.get("step_id")
                        status = step.get("status")
                        
                        if step_id not in last_statuses or last_statuses[step_id] != status:
                            desc = step.get("description", "N/A")[:50]
                            print(f"  [{time.strftime('%H:%M:%S')}] 步骤 {desc}...")
                            print(f"         状态: {last_statuses.get(step_id, 'UNKNOWN')} → {status}")
                            last_statuses[step_id] = status
                    
                    # 检查是否所有步骤都完成
                    all_done = all(
                        s.get("status") in ["COMPLETED", "FAILED", "Completed", "Failed"]
                        for s in steps
                    )
                    
                    if all_done:
                        print(f"\n[OK] 所有步骤执行完成")
                        return steps
            
            time.sleep(2)
            
        except Exception as e:
            print(f"[WARN] 监控时出错: {e}")
            time.sleep(2)
    
    print(f"\n[WARN] 超时 ({max_wait}秒)，停止监控")
    
    # 最后再获取一次
    try:
        response = requests.get(f"{BASE_URL}/api/plan/{plan_id}", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                return data["data"]["plan"].get("steps", [])
    except:
        pass
    
    return []


def verify_status_updates(steps):
    """验证状态更新"""
    print("\n[步骤6] 验证状态更新结果...")
    
    results = {
        "total": len(steps),
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "unknown": 0
    }
    
    for step in steps:
        status = step.get("status", "UNKNOWN").lower()
        
        if status in ["pending"]:
            results["pending"] += 1
        elif status in ["running"]:
            results["running"] += 1
        elif status in ["completed"]:
            results["completed"] += 1
        elif status in ["failed"]:
            results["failed"] += 1
        else:
            results["unknown"] += 1
    
    print(f"\n状态统计:")
    print(f"  总步骤数: {results['total']}")
    print(f"  PENDING:   {results['pending']}")
    print(f"  RUNNING:   {results['running']}")
    print(f"  COMPLETED: {results['completed']}")
    print(f"  FAILED:    {results['failed']}")
    print(f"  UNKNOWN:   {results['unknown']}")
    
    # 判断是否成功
    if results["pending"] == results["total"]:
        print("\n[FAIL] 所有步骤仍然是PENDING状态 - 状态未更新！")
        return False
    elif results["completed"] > 0 or results["failed"] > 0:
        print("\n[OK] 步骤状态已更新！修复生效！")
        return True
    else:
        print("\n[WARN] 步骤状态部分更新")
        return results["unknown"] == 0


def main():
    """主测试流程"""
    print("="*60)
    print("端到端测试: 步骤状态更新验证")
    print("="*60)
    
    # 1. 测试连接
    if not test_api_connection():
        return 1
    
    # 2. 创建设计书
    design_id = create_design()
    if not design_id:
        print("\n[FAIL] 无法创建设计书，测试终止")
        return 1
    
    # 3. 生成计划
    plan_id = approve_design(design_id)
    if not plan_id:
        print("\n[FAIL] 无法生成计划，测试终止")
        return 1
    
    # 4. 获取初始步骤状态
    initial_steps = get_plan_steps(plan_id)
    if not initial_steps:
        print("\n[FAIL] 无法获取步骤，测试终止")
        return 1
    
    # 5. 执行计划
    if not execute_plan(plan_id):
        print("\n[FAIL] 无法启动执行，测试终止")
        return 1
    
    # 6. 监控状态变化
    final_steps = check_step_status(plan_id, max_wait=60)
    
    # 7. 验证结果
    success = verify_status_updates(final_steps)
    
    print("\n" + "="*60)
    if success:
        print("测试结果: 通过 ✓")
        print("步骤状态更新机制已修复并验证")
        return 0
    else:
        print("测试结果: 失败 ✗")
        print("步骤状态未正确更新，需要进一步检查")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
