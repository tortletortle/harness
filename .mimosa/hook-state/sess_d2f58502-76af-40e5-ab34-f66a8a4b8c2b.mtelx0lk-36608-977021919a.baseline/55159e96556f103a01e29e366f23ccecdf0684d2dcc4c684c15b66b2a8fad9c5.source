"""
Script to restart the backend server
"""
import subprocess
import time
import sys
import os
import signal

def find_backend_process():
    """Find the backend server process"""
    if sys.platform == 'win32':
        result = subprocess.run(
            ['netstat', '-ano'], 
            capture_output=True, 
            text=True
        )
        for line in result.stdout.split('\n'):
            if ':8000' in line and 'LISTENING' in line:
                parts = line.split()
                pid = parts[-1]
                return int(pid)
    return None

def stop_backend():
    """Stop the backend server"""
    pid = find_backend_process()
    if pid:
        print(f"[INFO] Found backend process: PID {pid}")
        try:
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/F', '/PID', str(pid)], check=True)
            else:
                os.kill(pid, signal.SIGTERM)
            print("[OK] Backend stopped")
            time.sleep(2)
            return True
        except Exception as e:
            print(f"[ERROR] Failed to stop backend: {e}")
            return False
    else:
        print("[INFO] No backend process found on port 8000")
        return True

def start_backend():
    """Start the backend server"""
    print("[INFO] Starting backend server...")
    try:
        # Start in background
        if sys.platform == 'win32':
            subprocess.Popen(
                ['python', '-m', 'uvicorn', 'api.server:app', '--host', '127.0.0.1', '--port', '8000'],
                cwd='.',
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            subprocess.Popen(
                ['python', '-m', 'uvicorn', 'api.server:app', '--host', '127.0.0.1', '--port', '8000'],
                cwd='.'
            )
        
        # Wait for startup
        print("[INFO] Waiting for backend to start...")
        for i in range(10):
            time.sleep(1)
            try:
                import requests
                response = requests.get('http://127.0.0.1:8000/', timeout=2)
                if response.status_code == 200:
                    print("[OK] Backend started successfully")
                    return True
            except:
                pass
        
        print("[ERROR] Backend failed to start")
        return False
        
    except Exception as e:
        print(f"[ERROR] Failed to start backend: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Backend Server Restart")
    print("=" * 60)
    
    # Stop
    if not stop_backend():
        print("[ERROR] Failed to stop backend")
        sys.exit(1)
    
    # Start
    if not start_backend():
        print("[ERROR] Failed to start backend")
        sys.exit(1)
    
    print("=" * 60)
    print("[SUCCESS] Backend restarted successfully")
    print("=" * 60)
