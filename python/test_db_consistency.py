#!/usr/bin/env python3
"""
测试 4：数据库状态持久化与读取一致性
验证 steps 表中的 status 字段是否正确保存/读取
验证是否还有残留的 "COMPLETED" 等错误状态值
"""

import sqlite3
from pathlib import Path
from database import Database
from models import StepStatus

def main():
    print("\n" + "="*70)
    print("测试 4：数据库状态持久化一致性检查")
    print("="*70)
    
    db = Database()
    
    # 查询所有步骤的状态
    print("\n[1] 检查 steps 表中的 status 字段值：\n")
    
    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT step_id, status FROM steps LIMIT 20")
            rows = cursor.fetchall()
            
            status_values = set()
            for step_id, status in rows:
                status_values.add(status)
                print(f"  step_id: {step_id[:8]}... | status: '{status}'")
            
        print(f"\n  数据库中实际的 status 值集合: {status_values}")
        
        # 检查是否有合法的枚举值
        valid_status_values = {s.value for s in StepStatus}
        print(f"  代码中定义的有效枚举值: {valid_status_values}")
        
        # 查找不合法的状态值
        invalid_status = status_values - valid_status_values
        if invalid_status:
            print(f"\n  [ALERT] 发现不合法的状态值: {invalid_status}")
            print(f"  这会导致 Pydantic 验证失败！")
        else:
            print(f"\n  [OK] 所有状态值都是合法的")
    
    except Exception as e:
        print(f"  [ERROR] 查询失败: {e}")
        return
    
    # 查询 plans 表的 status
    print("\n[2] 检查 plans 表中的 status 字段值：\n")
    
    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT plan_id, status FROM plans LIMIT 10")
            rows = cursor.fetchall()
            
            plan_status_values = set()
            for plan_id, status in rows:
                plan_status_values.add(status)
                print(f"  plan_id: {plan_id[:8]}... | status: '{status}'")
            
        print(f"\n  数据库中 plans 的 status 值集合: {plan_status_values}")
        
        # plans 应该用 PlanStatus
        from models import PlanStatus
        valid_plan_status = {s.value for s in PlanStatus}
        print(f"  代码中定义的有效 PlanStatus 值: {valid_plan_status}")
        
        invalid_plan_status = plan_status_values - valid_plan_status
        if invalid_plan_status:
            print(f"\n  [ALERT] 发现不合法的 plans status 值: {invalid_plan_status}")
        else:
            print(f"\n  [OK] plans 的所有状态值都是合法的")
    
    except Exception as e:
        print(f"  [ERROR] 查询失败: {e}")
        return
    
    # 查询残留的旧状态值
    print("\n[3] 扫描残留的旧状态值（COMPLETED, RUNNING 等）：\n")
    
    try:
        with db._get_connection() as conn:
            cursor = conn.cursor()
            
            # 查询 steps 表中的旧格式状态
            cursor.execute("SELECT COUNT(*) FROM steps WHERE status LIKE 'COMPLETED' OR status LIKE 'RUNNING' OR status LIKE 'FAILED'")
            old_format_count = cursor.fetchone()[0]
            
            if old_format_count > 0:
                print(f"  [ALERT] 发现 {old_format_count} 个步骤使用旧格式状态（大写）")
                cursor.execute("SELECT DISTINCT status FROM steps WHERE status LIKE 'COMPLETED' OR status LIKE 'RUNNING' OR status LIKE 'FAILED'")
                old_values = [row[0] for row in cursor.fetchall()]
                print(f"  旧格式状态值: {old_values}")
            else:
                print(f"  [OK] 没有发现旧格式状态值（COMPLETED, RUNNING 等）")
            
            # 查询 plans 表中的旧格式状态
            cursor.execute("SELECT COUNT(*) FROM plans WHERE status LIKE 'COMPLETED' OR status LIKE 'RUNNING' OR status LIKE 'FAILED'")
            old_plan_count = cursor.fetchone()[0]
            
            if old_plan_count > 0:
                print(f"  [ALERT] 发现 {old_plan_count} 个计划使用旧格式状态（大写）")
                cursor.execute("SELECT DISTINCT status FROM plans WHERE status LIKE 'COMPLETED' OR status LIKE 'RUNNING' OR status LIKE 'FAILED'")
                old_plan_values = [row[0] for row in cursor.fetchall()]
                print(f"  旧格式计划状态值: {old_plan_values}")
            else:
                print(f"  [OK] 没有发现 plans 表中的旧格式状态值")
    
    except Exception as e:
        print(f"  [ERROR] 扫描失败: {e}")
        return
    
    print("\n" + "="*70)
    print("数据库一致性检查完成")
    print("="*70)

if __name__ == "__main__":
    main()
