@echo off
REM EAS 2.0 启动脚本

echo ========================================
echo EAS 2.0 - 启动服务
echo ========================================
echo.

REM 检查端口是否被占用
netstat -ano | findstr :9000 >nul
if %errorlevel% equ 0 (
    echo [警告] 端口 9000 已被占用
    echo.
)

netstat -ano | findstr :8090 >nul
if %errorlevel% equ 0 (
    echo [警告] 端口 8090 已被占用
    echo.
)

echo [1/2] 启动 Python API 服务 (端口 9000)...
start "EAS Python API" cmd /k "cd python && python -m uvicorn api.main:app --host 0.0.0.0 --port 9000"

echo [等待 5 秒...]
timeout /t 5 /nobreak >nul

echo.
echo [2/2] 启动 Rust 服务器 (端口 8090)...
start "EAS Rust Server" cmd /k "cargo run --bin eas-server"

echo.
echo ========================================
echo 服务启动中...
echo.
echo 请等待约 30 秒让服务完全启动
echo 然后运行: python scripts\test_services.py
echo.
echo 查看服务窗口获取启动日志
echo ========================================
