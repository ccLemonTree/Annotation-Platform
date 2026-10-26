@echo off
chcp 65001 > nul
echo ========================================
echo 数据管理平台后端 - 快速启动脚本
echo ========================================
echo.

REM 检查Python环境
python --version > nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到Python，请先安装Python 3.11+
    pause
    exit /b 1
)

REM 检查虚拟环境
if not exist "venv" (
    echo 📦 创建虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        echo ❌ 虚拟环境创建失败
        pause
        exit /b 1
    )
)

REM 激活虚拟环境
echo 🔧 激活虚拟环境...
call venv\Scripts\activate.bat

REM 安装依赖
echo 📥 安装依赖包...
pip install -r requirements.txt
if errorlevel 1 (
    echo ❌ 依赖安装失败
    pause
    exit /b 1
)

REM 检查.env文件
if not exist ".env" (
    echo ⚠️  未找到.env文件，从.env.example复制...
    copy .env.example .env
    echo 📝 请编辑.env文件配置数据库连接
    pause
)

REM 创建上传目录
if not exist "uploads" (
    echo 📁 创建uploads目录...
    mkdir uploads
)

echo.
echo ========================================
echo 🚀 启动开发服务器...
echo ========================================
echo.
echo 访问地址:
echo   - API文档: http://localhost:8000/docs
echo   - ReDoc: http://localhost:8000/redoc
echo   - 健康检查: http://localhost:8000/health
echo.
echo 按 Ctrl+C 停止服务器
echo ========================================
echo.

REM 启动服务器
uvicorn main:app --reload --host 0.0.0.0 --port 8000
