"""临时：检查上次被中断的测试是否留下监听进程（用完即删）。"""
from __future__ import annotations

import socket
import subprocess

for port in (8000, 8014, 3000):
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", port))
        print(f"port_{port}=LISTENING")
    except OSError:
        print(f"port_{port}=free")
    finally:
        s.close()

proc = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe"],
                      capture_output=True, text=True)
lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
print("python_processes", max(0, len(lines) - 3))
for ln in lines[:6]:
    print("  ", ln[:120])
