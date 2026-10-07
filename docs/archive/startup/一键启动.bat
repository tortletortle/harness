@echo off
chcp 65001 >nul
REM EAS 2.0 - 一键启动（最简单版本）

echo ========================================
echo   EAS 2.0 - 一键启动
echo ========================================
echo.

REM 检查config/models.yaml文件
if not exist config\models.yaml (
    echo ❌ 错误: config/models.yaml 文件不存在
    echo.
    pause
    exit /b 1
)

echo ✅ 配置文件已找到
echo.

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python 未安装或不在PATH中
    pause
    exit /b 1
)

echo ✅ Python 已安装
echo.

REM 检查依赖
echo 🔍 检查Python依赖...
python -c "import anthropic" >nul 2>&1
if errorlevel 1 (
    echo ⚠️  依赖未安装，正在安装...
    cd python
    pip install -r requirements.txt -q
    cd ..
    echo ✅ 依赖安装完成
) else (
    echo ✅ 依赖已安装
)
echo.

echo ========================================
echo   启动 CLI 工具
echo ========================================
echo.
echo 💡 提示: 输入项目需求，AI会生成设计书和计划
echo 💡 输入 'quit' 退出
echo.
echo ========================================
echo.

REM 启动CLI
python scripts/cli.py

echo.
echo ========================================
echo   程序已退出
echo ========================================
pause
