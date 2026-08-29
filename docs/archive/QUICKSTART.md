# 🚀 快速开始指南

## 第一步：配置API密钥

编辑项目根目录的 `.env` 文件，添加您的API密钥：

```bash
# Anthropic API (必需)
ANTHROPIC_API_KEY=sk-ant-xxxxx

# OpenAI API (可选)
OPENAI_API_KEY=sk-xxxxx
```

## 第二步：安装依赖

### Python依赖
```bash
cd python
pip install -r requirements.txt
cd ..
```

### Rust依赖
```bash
cargo build
```

## 第三步：验证配置

运行环境检查脚本：
```bash
python scripts/check_env.py
```

如果一切正常，应该看到 ✅ 标记。

## 第四步：启动服务

### 方式1：手动启动（推荐用于开发）

**终端1 - Python API服务:**
```bash
cd python
python -m uvicorn api.main:app --host 0.0.0.0 --port 9000 --reload
```

**终端2 - Rust服务器:**
```bash
cargo run --bin eas-server
```

### 方式2：使用启动脚本

**Linux/Mac:**
```bash
chmod +x scripts/dev.sh
./scripts/dev.sh
```

**Windows:**
```bash
scripts\dev.bat
```

## 第五步：测试服务

### Python API (端口9000)
```bash
curl http://localhost:9000/health
```

### Rust Server (端口8090)
```bash
curl http://localhost:8090/health
```

## 第六步：测试AI功能

创建一个测试脚本 `test_api.py`:

```python
import asyncio
import httpx

async def test_designer():
    async with httpx.AsyncClient() as client:
        # 测试Designer AI
        response = await client.post(
            "http://localhost:9000/api/v1/designer",
            json={
                "action": "generate_design",
                "data": {
                    "user_request": "创建一个简单的待办事项应用"
                }
            }
        )
        print(response.json())

asyncio.run(test_designer())
```

## 🔧 常见问题

### 1. ModuleNotFoundError
确保在 `python` 目录下运行 Python 服务，或者设置 PYTHONPATH：
```bash
export PYTHONPATH=$PWD/python:$PYTHONPATH
```

### 2. 端口被占用
修改 `config/server.yaml` 中的端口配置。

### 3. API密钥无效
检查 `.env` 文件中的密钥是否正确，确保没有多余的空格或引号。

### 4. Rust编译错误
```bash
rustup update
cargo clean
cargo build
```

## 📚 更多文档

- [完整开发指南](../docs/DEVELOPMENT.md)
- [API文档](../docs/API.md)
- [项目结构](../docs/STRUCTURE.md)
- [项目状态](../docs/STATUS.md)

## 🎯 下一步

1. 阅读 [架构设计文档](../NEW_ARCHITECTURE_DESIGN.md)
2. 测试API端点
3. 开始开发您的第一个功能
4. 创建自定义插件

祝您使用愉快！🎉
