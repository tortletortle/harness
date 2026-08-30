#!/usr/bin/env python3
"""
启动 EAS 2.0 API 服务器（带自动重启）
"""

import socket
import sys
import subprocess
import time
from pathlib import Path

# 添加python目录到路径
python_dir = str(Path(__file__).parent.parent / "python")


def run_server():
    """运行服务器子进程"""
    return subprocess.run(
        [sys.executable, "-c", f"""
import sys
sys.path.insert(0, r"{python_dir}")
from api.server import start_server
start_server(host="127.0.0.1", port=8000)
"""],
        cwd=str(Path(__file__).parent.parent),
    )


def _port_in_use(port: int = 8000) -> bool:
    """端口是否已被监听（被另一个后端占用时不启动，避免双实例互相杀端口的战争）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    # 设置控制台编码为 UTF-8
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    
    print("=" * 70)
    print("  EAS 2.0 - API 服务器（支持自动重启）")
    print("=" * 70)
    print()
    
    # 端口预检：已有后端在监听时直接退出，不做重启战争（两个 start_api 同时跑会互相杀端口）
    if _port_in_use(8000):
        print("[退出] 端口 8000 已被占用——已有后端在运行。")
        print("       如需替换，请先停止现有后端（或 taskkill 其 PID）后再启动本脚本。")
        sys.exit(2)
    
    consecutive_fast_crashes = 0
    while True:
        print("[启动] 启动 API 服务...")
        t_start = time.time()
        result = run_server()
        uptime = time.time() - t_start
        
        if result.returncode == 0:
            # 正常退出（重启请求）
            consecutive_fast_crashes = 0
            print("[重启] 服务正在重启...")
            time.sleep(1)
            continue
        
        # 异常退出：快速连续崩溃熔断（防止崩溃循环干扰同机其他会话）
        if uptime < 30:
            consecutive_fast_crashes += 1
        else:
            consecutive_fast_crashes = 0
        if consecutive_fast_crashes >= 3:
            print(f"❌ 连续 {consecutive_fast_crashes} 次快速崩溃（每次存活 <30s），停止自动重启。")
            print("   请查看 logs/eas.log 中的启动异常后再手动启动。")
            sys.exit(3)
        
        print(f"❌ 服务异常退出 (code={result.returncode}，存活 {uptime:.0f}s)")
        print("3 秒后自动重启...")
        time.sleep(3)
