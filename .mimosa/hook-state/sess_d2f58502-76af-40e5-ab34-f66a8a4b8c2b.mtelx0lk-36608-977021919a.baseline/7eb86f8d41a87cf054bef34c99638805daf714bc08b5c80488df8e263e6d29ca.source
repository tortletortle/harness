#!/usr/bin/env python3
"""
启动 EAS 2.0 API 服务器（带自动重启）
"""

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


if __name__ == "__main__":
    # 设置控制台编码为 UTF-8
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    
    print("=" * 70)
    print("  EAS 2.0 - API 服务器（支持自动重启）")
    print("=" * 70)
    print()
    
    while True:
        print("[启动] 启动 API 服务...")
        result = run_server()
        
        if result.returncode == 0:
            # 正常退出（重启请求）
            print("[重启] 服务正在重启...")
            time.sleep(1)
            continue
        else:
            # 异常退出
            print(f"❌ 服务异常退出 (code={result.returncode})")
            print("3 秒后自动重启...")
            time.sleep(3)
