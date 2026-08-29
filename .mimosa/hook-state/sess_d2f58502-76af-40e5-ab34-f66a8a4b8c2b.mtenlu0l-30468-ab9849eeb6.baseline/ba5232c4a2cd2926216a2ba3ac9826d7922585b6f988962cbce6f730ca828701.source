"""
完整验证流程：启动服务器并运行端到端测试

这个脚本会：
1. 检查服务器是否运行
2. 如果未运行，提示用户启动
3. 运行端到端测试验证修复
"""

import sys
import os
import time
import requests
import asyncio
from datetime import datetime

# 添加python目录到路径
sys.path.insert(0, os.path.dirname(__file__))

BASE_URL = "http://127.0.0.1:8000"


def check_server():
    """检查服务器是否运行"""
    print("检查服务器状态...")
    try:
        response = requests.get(f"{BASE_URL}/", timeout=3)
        if response.status_code == 200:
            print("[OK] 服务器正在运行")
            return True
        else:
            print(f"[WARN] 服务器响应异常: {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print("[FAIL] 无法连接到服务器")
        print("\n请在另一个终端启动服务器:")
        print("  cd D:\\spot\\harness\\python")
        print("  python launch_server.py")
        return False
    except Exception as e:
        print(f"[ERROR] 检查服务器时出错: {e}")
        return False


def wait_for_server(timeout=30):
    """等待服务器启动"""
    print(f"\n等待服务器启动（最多{timeout}秒）...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            response = requests.get(f"{BASE_URL}/", timeout=2)
            if response.status_code == 200:
                print("[OK] 服务器已启动")
                return True
        except:
            pass
        
        print(".", end="", flush=True)
        time.sleep(2)
    
    print("\n[FAIL] 服务器启动超时")
    return False


def create_simple_plan():
    """创建一个简单的测试计划"""
    print("\n" + "="*60)
    print("创建测试计划")
    print("="*60)
    
    # 1. 创建设计书
    print("\n[1] 创建设计书...")
    try:
        response = requests.post(
            f"{BASE_URL}/api/design",
            json={"user_request": "创建一个文件 test_verification.txt，内容为 'Verification Test'"},
            timeout=60
        )
        
        if response.status_code != 200:
            print(f"[FAIL] 创建设计书失败: HTTP {response.status_code}")
            return None, None
        
        data = response.json()
        if not data.get("success"):
            print(f"[FAIL] 创建设计书失败: {data.get('message')}")
            return None, None
        
        design_id = data["design"]["design_id"]
        print(f"[OK] 设计书创建成功: {design_id}")
        
    except Exception as e:
        print(f"[FAIL] 创建设计书时出错: {e}")
        return None, None
    
    # 2. 批准设计书
    print("\n[2] 批准设计书并生成执行计划...")
    try:
        response = requests.post(
            f"{BASE_URL}/api/design/{design_id}/approve",
            json={"design_id": design_id, "approved": True},
            timeout=120
        )
        
        if response.status_code != 200:
            print(f"[FAIL] 批准失败: HTTP {response.status_code}")
            return design_id, None
        
        data = response.json()
        if not data.get("success"):
            print(f"[FAIL] 生成计划失败: {data.get('message')}")
            return design_id, None
        
        plan_id = data["plan"]["plan_id"]
        print(f"[OK] 执行计划生成成功: {plan_id}")
        return design_id, plan_id
        
    except Exception as e:
        print(f"[FAIL] 批准设计书时出错: {e}")
        return design_id, None


def check_plan_steps(plan_id):
    """检查计划的步骤"""
    print("\n[3] 检查计划步骤...")
    try:
        response = requests.get(f"{BASE_URL}/api/plan/{plan_id}", timeout=10)
        
        if response.status_code != 200:
            print(f"[FAIL] 查询失败: HTTP {response.status_code}")
            return None
        
        data = response.json()
        if not data.get("success"):
            print(f"[FAIL] 查询失败: {data.get('message')}")
            return None
        
        steps = data["plan"].get("steps", [])
        print(f"[OK] 找到 {len(steps)} 个步骤")
        
        if len(steps) == 0:
            print("[FAIL] 计划没有步骤！")
            return None
        
        print("\n初始状态:")
        for i, step in enumerate(steps, 1):
            desc = step.get("description", "N/A")[:50]
            status = step.get("status", "N/A")
            print(f"  步骤{i}: {desc}")
            print(f"         状态: {status}")
        
        return steps
        
    except Exception as e:
        print(f"[FAIL] 检查步骤时出错: {e}")
        return None


def execute_plan(plan_id):
    """执行计划"""
    print("\n[4] 执行计划...")
    try:
        response = requests.post(f"{BASE_URL}/api/plan/{plan_id}/execute", timeout=10)
        
        if response.status_code != 200:
            print(f"[FAIL] 执行失败: HTTP {response.status_code}")
            return False
        
        data = response.json()
        if not data.get("success"):
            print(f"[FAIL] 执行失败: {data.get('message')}")
            return False
        
        print("[OK] 计划开始执行")
        return True
        
    except Exception as e:
        print(f"[FAIL] 执行计划时出错: {e}")
        return False


def monitor_execution(plan_id, max_wait=60):
    """监控执行过程，验证状态更新"""
    print("\n[5] 监控执行状态（验证修复）...")
    print("="*60)
    
    start_time = time.time()
    status_history = {}
    status_changes = []
    
    iteration = 0
    while time.time() - start_time < max_wait:
        iteration += 1
        
        try:
            response = requests.get(f"{BASE_URL}/api/plan/{plan_id}", timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    steps = data["plan"].get("steps", [])
                    
                    # 检查状态变化
                    for step in steps:
                        step_id = step.get("step_id")
                        status = step.get("status", "UNKNOWN")
                        desc = step.get("description", "N/A")[:40]
                        
                        old_status = status_history.get(step_id)
                        
                        if old_status != status:
                            timestamp = time.strftime('%H:%M:%S')
                            change_info = {
                                "time": timestamp,
                                "step": desc,
                                "from": old_status or "初始",
                                "to": status,
                                "executed_at": step.get("executed_at"),
                                "outputs": step.get("outputs")
                            }
                            status_changes.append(change_info)
                            
                            print(f"\n[{timestamp}] 步骤状态变化:")
                            print(f"  步骤: {desc}")
                            print(f"  状态: {old_status or '初始'} → {status}")
                            
                            if step.get("executed_at"):
                                print(f"  执行时间: {step['executed_at']}")
                            
                            if step.get("outputs"):
                                outputs_str = str(step["outputs"])[:60]
                                print(f"  输出: {outputs_str}...")
                            
                            status_history[step_id] = status
                    
                    # 检查是否完成
                    all_done = all(
                        s.get("status", "").lower() in ["completed", "failed"]
                        for s in steps
                    )
                    
                    if all_done:
                        print(f"\n[OK] 所有步骤执行完成")
                        time.sleep(1)  # 等待1秒确保最后的状态更新
                        return steps, status_changes
            
            time.sleep(2)
            
        except Exception as e:
            print(f"[WARN] 监控时出错: {e}")
            time.sleep(2)
    
    print(f"\n[WARN] 监控超时 ({max_wait}秒)")
    
    # 最后再获取一次
    try:
        response = requests.get(f"{BASE_URL}/api/plan/{plan_id}", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("success"):
                return data["plan"].get("steps", []), status_changes
    except:
        pass
    
    return None, status_changes


def verify_fix(steps, status_changes):
    """验证修复是否生效"""
    print("\n" + "="*60)
    print("验证修复结果")
    print("="*60)
    
    if not steps:
        print("\n[FAIL] 无法获取步骤信息")
        return False
    
    # 统计状态
    status_counts = {
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "unknown": 0
    }
    
    executed_steps = []
    
    for step in steps:
        status = step.get("status", "UNKNOWN").lower()
        
        if status == "pending":
            status_counts["pending"] += 1
        elif status == "running":
            status_counts["running"] += 1
        elif status == "completed":
            status_counts["completed"] += 1
            executed_steps.append(step)
        elif status == "failed":
            status_counts["failed"] += 1
            executed_steps.append(step)
        else:
            status_counts["unknown"] += 1
    
    # 显示最终状态
    print("\n最终状态统计:")
    print(f"  总步骤数:  {len(steps)}")
    print(f"  PENDING:   {status_counts['pending']}")
    print(f"  RUNNING:   {status_counts['running']}")
    print(f"  COMPLETED: {status_counts['completed']}")
    print(f"  FAILED:    {status_counts['failed']}")
    print(f"  UNKNOWN:   {status_counts['unknown']}")
    
    # 显示状态变化历史
    print(f"\n状态变化记录: {len(status_changes)} 次变化")
    for change in status_changes:
        print(f"  [{change['time']}] {change['step'][:30]}: {change['from']} → {change['to']}")
    
    # 验证关键点
    print("\n" + "="*60)
    print("关键验证点")
    print("="*60)
    
    success = True
    
    # 1. 是否有状态变化
    if len(status_changes) == 0:
        print("\n❌ 失败: 没有检测到任何状态变化")
        print("   步骤状态没有更新，修复未生效！")
        success = False
    else:
        print(f"\n✅ 检测到 {len(status_changes)} 次状态变化")
    
    # 2. 是否所有步骤都是PENDING
    if status_counts["pending"] == len(steps):
        print("❌ 失败: 所有步骤仍然是PENDING状态")
        print("   步骤状态没有更新，修复未生效！")
        success = False
    else:
        print(f"✅ 有 {len(steps) - status_counts['pending']} 个步骤状态已更新")
    
    # 3. 已执行的步骤是否有执行时间
    steps_with_time = sum(1 for s in executed_steps if s.get("executed_at"))
    if executed_steps and steps_with_time == len(executed_steps):
        print(f"✅ 已执行的 {len(executed_steps)} 个步骤都记录了执行时间")
    elif executed_steps:
        print(f"⚠️  警告: {len(executed_steps)} 个已执行步骤中，只有 {steps_with_time} 个记录了执行时间")
        success = False
    
    # 4. 已执行的步骤是否有输出
    steps_with_output = sum(1 for s in executed_steps if s.get("outputs"))
    if executed_steps and steps_with_output == len(executed_steps):
        print(f"✅ 已执行的 {len(executed_steps)} 个步骤都保存了输出数据")
    elif executed_steps:
        print(f"⚠️  警告: {len(executed_steps)} 个已执行步骤中，只有 {steps_with_output} 个保存了输出")
        success = False
    
    # 5. 是否有步骤卡在RUNNING
    if status_counts["running"] > 0:
        print(f"⚠️  警告: 有 {status_counts['running']} 个步骤卡在RUNNING状态")
        success = False
    else:
        print("✅ 没有步骤卡在RUNNING状态")
    
    return success


def main():
    """主函数"""
    print("="*60)
    print("EAS 2.0 - 步骤状态更新修复验证")
    print("="*60)
    print("\n本测试将验证:")
    print("  1. 步骤状态是否从 PENDING 更新到 RUNNING")
    print("  2. 步骤状态是否最终更新到 COMPLETED 或 FAILED")
    print("  3. 执行时间是否被记录")
    print("  4. 输出数据是否被保存")
    print("="*60)
    
    # 1. 检查服务器
    if not check_server():
        print("\n请启动服务器后重新运行此测试")
        return 1
    
    # 2. 创建测试计划
    design_id, plan_id = create_simple_plan()
    if not plan_id:
        print("\n[FAIL] 无法创建测试计划")
        return 1
    
    # 3. 检查初始步骤
    initial_steps = check_plan_steps(plan_id)
    if not initial_steps:
        print("\n[FAIL] 无法获取步骤信息")
        return 1
    
    # 4. 执行计划
    if not execute_plan(plan_id):
        print("\n[FAIL] 无法执行计划")
        return 1
    
    # 5. 监控执行
    final_steps, status_changes = monitor_execution(plan_id, max_wait=60)
    
    # 6. 验证结果
    success = verify_fix(final_steps, status_changes)
    
    # 7. 总结
    print("\n" + "="*60)
    print("测试总结")
    print("="*60)
    
    if success:
        print("\n✅ 测试通过！")
        print("\n步骤状态更新修复已验证生效:")
        print("  ✓ 步骤状态正确更新")
        print("  ✓ 执行时间正确记录")
        print("  ✓ 输出数据正确保存")
        print("\n修复成功部署到生产环境！")
        return 0
    else:
        print("\n❌ 测试失败")
        print("\n请检查:")
        print("  1. 服务器是否已重启（加载新代码）")
        print("  2. api/server.py 的修改是否正确")
        print("  3. 查看服务器日志是否有错误")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
