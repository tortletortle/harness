"""临时：找出（并可选终止）残留的 pytest 进程（用完即删）。

用法：python scripts/_tmp_find_pytest.py            # 只列出
      python scripts/_tmp_find_pytest.py --kill      # 终止命令行含 pytest 的 python 进程
"""
from __future__ import annotations

import subprocess
import sys

PS = (
    "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
    "ForEach-Object { \"$($_.ProcessId)||$($_.CommandLine)\" }"
)
out = subprocess.run(["powershell", "-NoProfile", "-Command", PS],
                     capture_output=True, text=True)
rows = []
for line in (out.stdout or "").splitlines():
    if "||" not in line:
        continue
    pid, _, cmd = line.partition("||")
    if "pytest" in cmd or "_tmp_pollution_check" in cmd:
        rows.append((pid.strip(), cmd.strip()))

print("matched_pytest_processes", len(rows))
for pid, cmd in rows:
    print("  pid", pid, cmd[:150])

if "--kill" in sys.argv and rows:
    for pid, _ in rows:
        kill = subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                              capture_output=True, text=True)
        print("killed", pid, kill.returncode)
