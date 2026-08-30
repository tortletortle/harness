from database import Database

db = Database()
conn = db._get_connection()

# 查看 plans 表结构
print("=== plans 表结构 ===")
schema = conn.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name="plans"').fetchone()
if schema:
    print(schema[0])
else:
    print("plans 表不存在")

print("\n=== steps 表结构 ===")
schema = conn.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name="steps"').fetchone()
if schema:
    print(schema[0])
else:
    print("steps 表不存在")

# 查看所有表
print("\n=== 所有表 ===")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for table in tables:
    print(f"  - {table[0]}")
    
    # 获取表中的记录数
    count = conn.execute(f"SELECT COUNT(*) FROM {table[0]}").fetchone()[0]
    print(f"    记录数: {count}")

conn.close()
