import subprocess
import sys
import os
import time

# 启动服务器
print("Starting backend server...")
os.chdir(r"D:\spot\harness\python")

# 在 Windows 上使用 CREATE_NEW_PROCESS_GROUP 和 DETACHED_PROCESS
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", "8000"],
    stdout=open("backend_server.log", "w"),
    stderr=subprocess.STDOUT,
    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
)

print(f"Server started with PID: {proc.pid}")
print("Waiting 10 seconds for server to fully start...")
time.sleep(10)

# 验证服务器
import urllib.request
try:
    response = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5)
    print(f"[OK] Server is running! Status: {response.status}")
except Exception as e:
    print(f"[FAIL] Server check failed: {e}")
    print("Server may still be starting up...")

print(f"\nServer PID: {proc.pid}")
print("Server log: backend_server.log")
