"""
启动服务器的辅助脚本
"""
import subprocess
import sys
import os

def start_server():
    """在新进程中启动服务器"""
    # 使用 CREATE_NEW_PROCESS_GROUP 标志创建独立进程
    # 这样即使父进程退出，服务器也会继续运行
    
    if sys.platform == 'win32':
        # Windows
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        
        process = subprocess.Popen(
            [sys.executable, "launch_server.py"],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL
        )
        
        print(f"服务器已启动 (PID: {process.pid})")
        print("正在等待服务器启动...")
        
        # 等待服务器启动
        import time
        import requests
        
        for i in range(15):
            time.sleep(2)
            try:
                response = requests.get("http://127.0.0.1:8000/", timeout=1)
                if response.status_code == 200:
                    print("✓ 服务器启动成功！")
                    return True
            except:
                print(".", end="", flush=True)
        
        print("\n✗ 服务器启动超时")
        return False
    else:
        # Linux/Mac
        process = subprocess.Popen(
            [sys.executable, "launch_server.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True
        )
        
        print(f"服务器已启动 (PID: {process.pid})")
        return True

if __name__ == "__main__":
    success = start_server()
    sys.exit(0 if success else 1)
