@echo off
chcp 65001 > nul
title EAS 2.0 - 前端开发服务器

echo ========================================
echo   EAS 2.0 - 前端界面
echo ========================================
echo.

cd frontend
npm run dev
