"""验证 alembic baseline 与运行时 schema 一致性。

1. 新库 A：alembic upgrade head 创建
2. 新库 B：Database() 运行时创建
3. 逐对象对比 sqlite_master（规范化空白后必须完全一致）
"""
import os, re, sqlite3, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python"))

def normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql or "").strip().rstrip(";").lower()

def schema_of(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' AND name != 'alembic_version' "
        "ORDER BY type DESC, name"
    ).fetchall()
    conn.close()
    return {(t, n): normalize(s) for t, n, s in rows}

tmp = tempfile.mkdtemp(prefix="eas_mig_verify_")
db_a = os.path.join(tmp, "alembic.db")
db_b = os.path.join(tmp, "runtime.db")

# A: alembic 建库
env = dict(os.environ, EAS_DB_PATH=db_a)
r = subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"],
    cwd=ROOT, env=env, capture_output=True, text=True,
)
print("alembic upgrade rc:", r.returncode)
if r.returncode != 0:
    print(r.stderr[-1500:])
    sys.exit(1)

# B: 运行时建库
from database import Database
Database(db_b)

sa, sb = schema_of(db_a), schema_of(db_b)
missing = set(sb) - set(sa)
extra = set(sa) - set(sb)
diffs = [k for k in (set(sa) & set(sb)) if sa[k] != sb[k]]

print("objects alembic:", len(sa), "runtime:", len(sb))
if missing: print("MISSING in alembic:", sorted(missing))
if extra: print("EXTRA in alembic:", sorted(extra))
for k in diffs:
    print("DIFF:", k)
    print("  alembic:", sa[k][:200])
    print("  runtime:", sb[k][:200])
if not missing and not extra and not diffs:
    print("SCHEMA_IDENTICAL")

# C: 既有库 stamp（用运行时库模拟既有生产库）
env_b = dict(os.environ, EAS_DB_PATH=db_b)
r2 = subprocess.run(
    [sys.executable, "-m", "alembic", "stamp", "0001_baseline"],
    cwd=ROOT, env=env_b, capture_output=True, text=True,
)
print("stamp rc:", r2.returncode)
conn = sqlite3.connect(db_b)
ver = conn.execute("SELECT version_num FROM alembic_version").fetchone()
print("stamped version:", ver[0] if ver else None)
conn.close()

r3 = subprocess.run(
    [sys.executable, "-m", "alembic", "current"],
    cwd=ROOT, env=env_b, capture_output=True, text=True,
)
print("current:", (r3.stdout or r3.stderr).strip()[-80:])

