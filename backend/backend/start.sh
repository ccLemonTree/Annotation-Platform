#!/bin/bash

echo "========================================"
echo "数据管理平台后端 - 快速启动脚本"
echo "========================================"
echo ""

# 检查Python环境
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到Python3，请先安装Python 3.11+"
    exit 1
fi

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "📦 创建虚拟环境..."
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo "❌ 虚拟环境创建失败"
        exit 1
    fi
fi

# 激活虚拟环境
echo "🔧 激活虚拟环境..."
source venv/bin/activate

# 安装依赖
echo "📥 安装依赖包..."
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "❌ 依赖安装失败"
    exit 1
fi

# 检查.env文件
if [ ! -f ".env" ]; then
    echo "⚠️  未找到.env文件，从.env.example复制..."
    cp .env.example .env
    echo "📝 请编辑.env文件配置数据库连接"
    read -p "按Enter继续..."
fi

# 创建上传目录
if [ ! -d "uploads" ]; then
    echo "📁 创建uploads目录..."
    mkdir -p uploads
fi

echo ""
echo "========================================"
echo "🚀 启动开发服务器..."
echo "========================================"
echo ""
echo "访问地址:"
echo "  - API文档: http://localhost:8000/docs"
echo "  - ReDoc: http://localhost:8000/redoc"
echo "  - 健康检查: http://localhost:8000/health"
echo ""
echo "按 Ctrl+C 停止服务器"
echo "========================================"
echo ""

# 启动服务器
uvicorn main:app --reload --host 0.0.0.0 --port 8000
