from database import Database
import json

db = Database()
conn = db._get_connection()

# 获取最新的几个计划
plans = conn.execute("""
    SELECT id, design_id, created_at 
    FROM plans 
    ORDER BY created_at DESC 
    LIMIT 5
""").fetchall()

print(f"Found {len(plans)} plans:")
for plan in plans:
    plan_id = plan['id']
    print(f"\nPlan ID: {plan_id}")
    print(f"  Created: {plan['created_at']}")
    
    # 检查这个计划有多少步骤
    steps = conn.execute("""
        SELECT COUNT(*) as count 
        FROM steps 
        WHERE plan_id = ?
    """, (plan_id,)).fetchone()
    
    print(f"  Steps in DB: {steps['count']}")
    
conn.close()
