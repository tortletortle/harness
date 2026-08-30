"""
Properly restart the backend server
"""
import subprocess
import time
import sys
import signal

# Kill all python processes
print("[INFO] Killing all Python processes...")
subprocess.run(['taskkill', '/F', '/IM', 'python.exe'], capture_output=True)
time.sleep(3)

# Start backend
print("[INFO] Starting backend server...")
proc = subprocess.Popen(
    [sys.executable, '-m', 'uvicorn', 'api.server:app', '--host', '127.0.0.1', '--port', '8000'],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0
)

# Wait for startup
print("[INFO] Waiting for backend to start (10 seconds)...")
time.sleep(10)

# Test
import requests
try:
    response = requests.get('http://127.0.0.1:8000/', timeout=5)
    if response.status_code == 200:
        print("[OK] Backend started successfully")
        print(response.json())
    else:
        print(f"[ERROR] Backend returned status {response.status_code}")
        sys.exit(1)
except Exception as e:
    print(f"[ERROR] Failed to connect to backend: {e}")
    sys.exit(1)
