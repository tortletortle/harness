#!/usr/bin/env bash
# 初始化项目

set -e

echo "🚀 Initializing EAS 2.0 Project"

# 检查环境
echo "📋 Checking environment..."

if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.10+"
    exit 1
fi

if ! command -v cargo &> /dev/null; then
    echo "❌ Cargo not found. Please install Rust"
    exit 1
fi

echo "✅ Environment OK"

# 创建虚拟环境
echo "🐍 Creating Python virtual environment..."
cd python
python3 -m venv venv
source venv/bin/activate || . venv/Scripts/activate

# 安装Python依赖
echo "📦 Installing Python dependencies..."
pip install -r requirements.txt

cd ..

# 构建Rust项目
echo "🦀 Building Rust project..."
cargo build

# 创建数据目录
echo "📁 Creating data directories..."
mkdir -p data logs

# 复制环境变量示例
if [ ! -f .env ]; then
    echo "📝 Creating .env file..."
    cp .env.example .env
    echo "⚠️  Please edit .env and add your API keys"
fi

echo ""
echo "✅ Project initialized successfully!"
echo ""
echo "Next steps:"
echo "1. Edit .env and add your API keys"
echo "2. Run './scripts/dev.sh' to start development servers"
echo "3. Read docs/DEVELOPMENT.md for more information"
