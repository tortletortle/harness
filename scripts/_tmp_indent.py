"""临时：打印 background.py 目标行的精确缩进（用完即删）。"""
from pathlib import Path

lines = Path("python/api/runtime/background.py").read_text(encoding="utf-8").splitlines()
for i in range(432, 450):
    line = lines[i]
    print(i + 1, "indent=", len(line) - len(line.lstrip(" ")), repr(line.strip()[:90]))
