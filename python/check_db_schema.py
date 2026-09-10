from database import Database


def _table_names(conn):
    """Return SQLite table names; later diagnostics use fixed SQL only."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = ? ORDER BY name",
        ("table",),
    ).fetchall()
    return [row[0] for row in rows]


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
tables = _table_names(conn)
for table in tables:
    print(f"  - {table}")
    if table == "plans":
        count = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    elif table == "steps":
        count = conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
    elif table == "designs":
        count = conn.execute("SELECT COUNT(*) FROM designs").fetchone()[0]
    elif table == "step_events":
        count = conn.execute("SELECT COUNT(*) FROM step_events").fetchone()[0]
    elif table == "execution_runs":
        count = conn.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0]
    elif table == "execution_attempts":
        count = conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0]
    elif table == "audit_logs":
        count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    else:
        print("    记录数: 未配置诊断查询")
        continue
    print(f"    记录数: {count}")

conn.close()
