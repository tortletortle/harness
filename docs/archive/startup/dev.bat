@echo off
REM 启动开发环境 (Windows)

echo Starting EAS 2.0 Development Environment...

REM 创建数据目录
if not exist data mkdir data
if not exist logs mkdir logs

REM 启动Python API服务
echo Starting Python API service...
start "Python API" cmd /k "cd python && python -m uvicorn api.main:app --host 0.0.0.0 --port 9000 --reload"

REM 等待Python服务启动
timeout /t 3 /nobreak >nul

REM 启动Rust服务器
echo Starting Rust server...
start "Rust Server" cmd /k "cargo run --bin eas-server"

echo.
echo Services started:
echo   - Python API: http://localhost:9000
echo   - Rust Server: http://localhost:8090
echo.
echo Press any key to stop all services...
pause >nul

REM 停止所有服务
taskkill /FI "WindowTitle eq Python API*" /F
taskkill /FI "WindowTitle eq Rust Server*" /F
