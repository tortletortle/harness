"""打印 Database 初始化出的完整 schema（用于 alembic baseline 核对）。"""
import sys, os, tempfile, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from database import Database

p = os.path.join(tempfile.gettempdir(), "eas_schema_probe.db")
if os.path.exists(p):
    os.remove(p)
Database(p)
conn = sqlite3.connect(p)
rows = conn.execute(
    "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type DESC, name"
).fetchall()
for t, n, s in rows:
    print(f"-- == {t}: {n}")
    if s:
        print(s.strip() + ";")
conn.close()
os.remove(p)

