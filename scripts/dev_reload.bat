@echo off
chcp 65001 > nul
title EAS 2.0 - 开发模式（自动重启）

echo ========================================
echo   EAS 2.0 - 开发模式
echo   文件变化时自动重启后端
echo ========================================
echo.

REM 检查是否安装了 watchdog
python -c "import watchdog" 2>nul
if errorlevel 1 (
    echo [警告] 未安装 watchdog 库
    echo 正在安装 watchdog...
    pip install watchdog
    echo.
)

python scripts\dev_server.py

pause
