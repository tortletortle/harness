@echo off
chcp 65001 >nul
REM 验证EAS 2.0配置

echo ========================================
echo   EAS 2.0 - 配置验证
echo ========================================
echo.

set ERROR_COUNT=0

REM 检查config/models.yaml文件
echo [1/5] 检查配置文件...
if exist config\models.yaml (
    echo ✅ config/models.yaml 文件存在
) else (
    echo ❌ config/models.yaml 文件不存在
    set /a ERROR_COUNT+=1
)
echo.

REM 检查Python
echo [2/5] 检查Python环境...
python --version >nul 2>&1
if %errorlevel% equ 0 (
    python --version
    echo ✅ Python 已安装
) else (
    echo ❌ Python 未安装或不在PATH中
    set /a ERROR_COUNT+=1
)
echo.

REM 检查关键目录
echo [3/5] 检查项目结构...
set DIR_OK=1
if not exist python\ set DIR_OK=0
if not exist rust\ set DIR_OK=0
if not exist config\ set DIR_OK=0
if not exist scripts\ set DIR_OK=0

if %DIR_OK% equ 1 (
    echo ✅ 项目目录结构完整
) else (
    echo ❌ 项目目录不完整
    set /a ERROR_COUNT+=1
)
echo.

REM 检查关键文件
echo [4/5] 检查关键文件...
set FILE_OK=1
if not exist scripts\cli.py set FILE_OK=0
if not exist python\requirements.txt set FILE_OK=0
if not exist config\models.yaml set FILE_OK=0

if %FILE_OK% equ 1 (
    echo ✅ 关键文件完整
) else (
    echo ❌ 部分关键文件缺失
    set /a ERROR_COUNT+=1
)
echo.

REM 检查Python依赖（简单检查）
echo [5/5] 检查Python依赖...
python -c "import anthropic, fastapi, pydantic, yaml" >nul 2>&1
if %errorlevel% equ 0 (
    echo ✅ Python依赖已安装
) else (
    echo ⚠️  Python依赖未安装或不完整
    echo    运行: cd python ^&^& pip install -r requirements.txt
    set /a ERROR_COUNT+=1
)
echo.

REM 总结
echo ========================================
if %ERROR_COUNT% equ 0 (
    echo ✅ 验证通过！系统已准备就绪
    echo.
    echo 下一步:
    echo   1. 确保 .env 文件中已配置 ANTHROPIC_API_KEY
    echo   2. 双击 一键启动.bat 开始使用
    echo.
) else (
    echo ⚠️  发现 %ERROR_COUNT% 个问题，请修复后再使用
    echo.
    echo 常见解决方案:
    echo   - 安装Python: https://www.python.org/downloads/
    echo   - 安装依赖: cd python ^&^& pip install -r requirements.txt
    echo   - 配置密钥: 编辑 .env 文件，添加 ANTHROPIC_API_KEY
    echo.
)
echo ========================================
echo.
pause
