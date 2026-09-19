"""临时：逐个测试文件跑，检查仓库根记忆是否被写 + 导出隔离工作区里的记忆（用完即删）。"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

mem = Path("PROJECT_MEMORY.md")


def digest():
    raw = mem.read_bytes()
    return hashlib.sha256(raw).hexdigest()[:16], len(raw), raw.count("## [".encode())


def isolated_memories():
    root = Path(tempfile.gettempdir())
    return sorted(root.glob("pytest-of-*/pytest-*/eas_workspace_root*/PROJECT_MEMORY.md"))


base = digest()
print("baseline", base)
for target in sys.argv[1:]:
    before = digest()
    proc = subprocess.run(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", target],
                          capture_output=True, text=True)
    after = digest()
    tail = (proc.stdout or "").strip().splitlines()
    summary = next((ln for ln in reversed(tail) if "passed" in ln or "failed" in ln or "error" in ln), "")
    print(f"target={target} exit={proc.returncode} {summary[:90]}")
    print(f"  before={before} after={after} delta_entries={after[2] - before[2]} delta_bytes={after[1] - before[1]}")
print("final", digest(), "changed_from_baseline", digest() != base)

found = isolated_memories()
print("isolated_memory_files", len(found))
for path in found[-3:]:
    text = path.read_text(encoding="utf-8", errors="replace")
    print("---", path)
    print("  bytes", len(text.encode()), "entries", text.count("## ["))
    for line in text.splitlines()[:6]:
        print("   |", line[:120])
