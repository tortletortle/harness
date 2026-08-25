@echo off
REM 初始化项目 (Windows)

echo Initializing EAS 2.0 Project...

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found. Please install Python 3.10+
    exit /b 1
)

REM 检查Cargo
cargo --version >nul 2>&1
if errorlevel 1 (
    echo Cargo not found. Please install Rust
    exit /b 1
)

echo Environment OK

REM 创建虚拟环境
echo Creating Python virtual environment...
cd python
python -m venv venv
call venv\Scripts\activate.bat

REM 安装Python依赖
echo Installing Python dependencies...
pip install -r requirements.txt

cd ..

REM 构建Rust项目
echo Building Rust project...
cargo build

REM 创建数据目录
echo Creating data directories...
if not exist data mkdir data
if not exist logs mkdir logs

REM 复制环境变量示例
if not exist .env (
    echo Creating .env file...
    copy .env.example .env
    echo Please edit .env and add your API keys
)

echo.
echo Project initialized successfully!
echo.
echo Next steps:
echo 1. Edit .env and add your API keys
echo 2. Run 'scripts\dev.bat' to start development servers
echo 3. Read docs\DEVELOPMENT.md for more information

pause
