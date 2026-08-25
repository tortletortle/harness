@echo off
chcp 65001 >nul
REM EAS 2.0 - 完整启动（包含API服务）

echo ========================================
echo   EAS 2.0 - 完整启动
echo ========================================
echo.

REM 检查.env文件
if not exist .env (
    echo ❌ 错误: .env 文件不存在
    echo.
    echo 请先创建 .env 文件并添加API密钥:
    echo   ANTHROPIC_API_KEY=你的密钥
    echo.
    pause
    exit /b 1
)

echo ✅ 环境文件已找到
echo.

REM 检查端口占用
echo 🔍 检查端口占用...
netstat -ano | findstr :9000 >nul
if %errorlevel% equ 0 (
    echo ⚠️  端口 9000 已被占用
    echo.
)

netstat -ano | findstr :8090 >nul
if %errorlevel% equ 0 (
    echo ⚠️  端口 8090 已被占用
    echo.
)

echo.
echo ========================================
echo   选择启动模式:
echo ========================================
echo.
echo   [1] 仅启动CLI工具（推荐，最简单）
echo   [2] 启动Python API服务
echo   [3] 启动完整服务（Python + Rust）
echo   [4] 退出
echo.
set /p choice="请选择 (1-4): "

if "%choice%"=="1" goto CLI_ONLY
if "%choice%"=="2" goto PYTHON_API
if "%choice%"=="3" goto FULL_SERVICE
if "%choice%"=="4" goto END

echo 无效选择，默认启动CLI
goto CLI_ONLY

:CLI_ONLY
echo.
echo ========================================
echo   启动 CLI 工具
echo ========================================
echo.
python scripts/cli.py
goto END

:PYTHON_API
echo.
echo ========================================
echo   启动 Python API 服务
echo ========================================
echo.
echo 💡 启动后可以访问:
echo    - API: http://localhost:9000
echo    - 文档: http://localhost:9000/docs
echo.
echo 按 Ctrl+C 停止服务
echo.
cd python
python -m uvicorn api.main:app --host 0.0.0.0 --port 9000 --reload
cd ..
goto END

:FULL_SERVICE
echo.
echo ========================================
echo   启动完整服务
echo ========================================
echo.
echo [1/2] 启动 Python API 服务 (端口 9000)...
start "EAS Python API" cmd /k "cd python && python -m uvicorn api.main:app --host 0.0.0.0 --port 9000"

echo 等待 Python 服务启动...
timeout /t 5 /nobreak >nul

echo.
echo [2/2] 启动 Rust 服务器 (端口 8090)...
start "EAS Rust Server" cmd /k "cargo run --bin eas-server"

echo.
echo ========================================
echo   服务已启动！
echo ========================================
echo.
echo 查看服务窗口获取日志
echo.
echo Python API: http://localhost:9000
echo Rust Server: http://localhost:8090
echo.
echo 按任意键测试服务...
pause >nul

echo.
echo 🧪 测试服务连接...
python scripts/test_services.py

echo.
echo 按任意键关闭所有服务...
pause >nul

echo.
echo 🛑 关闭服务...
taskkill /FI "WindowTitle eq EAS Python API*" /F >nul 2>&1
taskkill /FI "WindowTitle eq EAS Rust Server*" /F >nul 2>&1
echo ✅ 服务已关闭
goto END

:END
echo.
pause
