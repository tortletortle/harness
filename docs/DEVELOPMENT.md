# EAS 2.0 开发指南

## 环境设置

### 1. 安装依赖

**Rust:**
```bash
# 安装Rust (如果尚未安装)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# 构建项目
cargo build
```

**Python:**
```bash
cd python
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入真实API密钥:

```bash
cp .env.example .env
# 编辑 .env 文件
```

### 3. 初始化数据库

```bash
mkdir -p data
```

数据库会在首次运行时自动创建。

## 开发流程

### 启动开发环境

**Linux/Mac:**
```bash
chmod +x scripts/dev.sh
./scripts/dev.sh
```

**Windows:**
```bash
scripts\dev.bat
```

### 单独启动服务

**Python API:**
```bash
cd python
python -m uvicorn api.main:app --host 0.0.0.0 --port 9000 --reload
```

**Rust Server:**
```bash
cargo run --bin eas-server
```

**GUI (未来):**
```bash
cargo run --bin eas-gui
```

## 项目结构

```
harness/
├── rust/              # Rust代码
│   ├── common/       # 通用类型和Trait
│   ├── dispatcher/   # 调度器
│   ├── logger/       # 日志系统
│   ├── tool_manager/ # 工具管理
│   ├── server/       # HTTP服务器
│   ├── db/           # 数据库层
│   └── gui/          # GUI (egui)
├── python/           # Python代码
│   ├── api/          # FastAPI服务
│   ├── designer/     # Designer AI
│   ├── toolmanager/  # ToolManager AI
│   ├── knowledge/    # Knowledge AI
│   ├── executor/     # Executor
│   ├── llm_client/   # LLM客户端
│   ├── models/       # 数据模型
│   └── utils/        # 工具函数
└── config/           # 配置文件
```

## 插件化架构

### 核心接口

**Rust:**
- `AIService` - AI服务接口
- `Dispatcher` - 调度器接口
- `Executor` - 执行器接口
- `ToolManager` - 工具管理器接口
- `Logger` - 日志接口
- `Database` - 数据库接口
- `Plugin` - 插件接口

**Python:**
- `AIService` - AI服务基类
- `Designer` - 设计师接口
- `ToolManagerAI` - 工具管理AI接口
- `KnowledgeAI` - 知识助手接口
- `ExecutorAI` - 执行器接口
- `Plugin` - 插件接口

### 创建新的AI服务

1. **实现接口**

```python
from utils.interfaces import AIService

class MyService(AIService):
    @property
    def name(self) -> str:
        return "my-service"
    
    async def process(self, request):
        # 实现逻辑
        pass
```

2. **注册服务**

在 `python/api/main.py` 中注册:

```python
app_state["my_service"] = MyService(llm_client, config)
```

3. **添加API端点**

```python
@app.post("/api/v1/my-service")
async def call_my_service(request: ServiceRequest):
    service = app_state.get("my_service")
    result = await service.process(request.data)
    return {"success": True, "result": result}
```

## 测试

**Rust测试:**
```bash
cargo test
```

**Python测试:**
```bash
cd python
pytest
```

**集成测试:**
```bash
cd tests/integration
pytest
```

## 调试

**启用详细日志:**

修改 `config/server.yaml`:
```yaml
logging:
  level: debug
```

**查看日志:**
```bash
tail -f logs/server.log
```

## 常见问题

### 1. 端口被占用

修改 `config/server.yaml` 中的端口配置。

### 2. Python依赖冲突

使用虚拟环境:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
pip install -r requirements.txt
```

### 3. Rust编译错误

确保使用最新的Rust版本:
```bash
rustup update
```

## 贡献指南

1. Fork项目
2. 创建特性分支
3. 提交更改
4. 推送到分支
5. 创建Pull Request

## License

MIT
