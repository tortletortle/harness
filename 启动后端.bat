@echo off
chcp 65001 > nul
title EAS 2.0 - 仅启动后端 API

echo ========================================
echo   EAS 2.0 - 后端 API 服务器
echo ========================================
echo.

python scripts\start_api.py
