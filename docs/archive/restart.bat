@echo off
echo [INFO] Killing backend processes...
taskkill /F /FI "WINDOWTITLE eq uvicorn*" 2>nul
taskkill /F /FI "MEMUSAGE gt 100000" /IM python.exe 2>nul

echo [INFO] Waiting 3 seconds...
timeout /T 3 /NOBREAK >nul

echo [INFO] Starting backend...
start /B python -m uvicorn api.server:app --host 127.0.0.1 --port 8000

echo [INFO] Waiting 8 seconds for startup...
timeout /T 8 /NOBREAK >nul

echo [INFO] Testing backend...
curl -s http://127.0.0.1:8000/

echo.
echo [OK] Backend restart complete
