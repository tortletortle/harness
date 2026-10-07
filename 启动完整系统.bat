@echo off
chcp 65001 > nul
title EAS 2.0 - 完整系统启动

echo ========================================
echo   EAS 2.0 - 完整系统启动
echo ========================================
echo.

REM 检查 Python
where python > nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
)

REM 检查 Node.js
where node > nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 未找到 Node.js，请先安装 Node.js
    pause
    exit /b 1
)

echo ✅ 环境检查通过
echo.

REM 安装 Python 依赖（如果需要）
echo 📦 检查 Python 依赖...
python -c "import fastapi" 2>nul
if %errorlevel% neq 0 (
    echo 安装 FastAPI...
    pip install fastapi uvicorn websockets -q
)

python -c "import yaml" 2>nul
if %errorlevel% neq 0 (
    echo 安装 PyYAML...
    pip install pyyaml -q
)

echo ✅ Python 依赖已就绪
echo.

REM 启动后端 API 服务器（后台）
echo 🚀 启动后端 API 服务器 (http://localhost:8000)...
start "EAS 2.0 API Server" cmd /c "python scripts\start_api.py"

REM 等待后端启动
timeout /t 3 /nobreak > nul

REM 启动前端开发服务器（前台）
echo 🚀 启动前端界面 (http://localhost:3000)...
echo.
echo ========================================
echo   访问地址:
echo   前端: http://localhost:3000
echo   后端: http://localhost:8000
echo ========================================
echo.

cd frontend
npm run dev
