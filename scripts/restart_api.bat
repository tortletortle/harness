@echo off
chcp 65001 > nul
title 重启后端服务

echo ========================================
echo   停止并重启后端服务
echo ========================================
echo.

echo 正在查找端口 8000 上的进程...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo 找到进程 PID: %%a
    echo 正在停止进程...
    taskkill /F /PID %%a
    timeout /t 2 /nobreak > nul
)

echo.
echo 等待端口释放...
timeout /t 3 /nobreak > nul

echo.
echo 启动新的后端服务...
echo ========================================
echo.

python scripts\start_api.py

pause
