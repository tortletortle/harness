# EAS 2.0 API文档

## Python API 端点

Base URL: `http://localhost:9000`

### 1. 健康检查

```http
GET /health
```

**响应:**
```json
{
  "status": "ok",
  "services": {
    "designer": "ok",
    "toolmanager": "ok",
    "knowledge": "ok",
    "executor": "ok"
  }
}
```

### 2. Designer AI

```http
POST /api/v1/designer
```

**请求体:**
```json
{
  "action": "generate_design",
  "data": {
    "user_request": "创建一个Web应用..."
  }
}
```

**支持的action:**
- `generate_design` - 生成设计书
- `generate_plan` - 生成执行计划
- `assess_severity` - 评估问题严重程度
- `generate_summary` - 生成总结报告

### 3. ToolManager AI

```http
POST /api/v1/toolmanager
```

**请求体:**
```json
{
  "action": "assess_tool",
  "data": {
    "tool_id": "image-generator",
    "tool_schema": { ... }
  }
}
```

**支持的action:**
- `assess_tool` - 评估工具
- `confirm_tool_issue` - 确认工具问题
- `suggest_alternatives` - 建议替代工具

### 4. Knowledge AI

```http
POST /api/v1/knowledge
```

**请求体:**
```json
{
  "action": "query",
  "data": {
    "question": "如何修复这个错误？",
    "context": { ... }
  }
}
```

**支持的action:**
- `query` - 回答问题
- `analyze_error` - 分析错误
- `suggest_fix` - 建议修复方案

### 5. Executor

```http
POST /api/v1/executor
```

**请求体:**
```json
{
  "action": "execute_step",
  "data": {
    "step": { ... }
  }
}
```

**支持的action:**
- `execute_step` - 执行步骤
- `check_result` - 检查结果

---

## Rust API 端点

Base URL: `http://localhost:8090`

### 1. 健康检查

```http
GET /health
```

### 2. 验证设计书

```http
POST /api/v1/design/validate
```

**请求体:**
```json
{
  "design": { ... }
}
```

### 3. 验证执行计划

```http
POST /api/v1/plan/validate
```

**请求体:**
```json
{
  "plan": { ... }
}
```

### 4. 工具管理

```http
GET /api/v1/tools
```

列出所有工具

```http
POST /api/v1/tools/register
```

注册新工具

---

## 插件开发

### Rust插件

实现 `Plugin` trait:

```rust
use async_trait::async_trait;
use eas_common::{Plugin, EasResult};

pub struct MyPlugin;

#[async_trait]
impl Plugin for MyPlugin {
    fn name(&self) -> &str {
        "my-plugin"
    }
    
    fn version(&self) -> &str {
        "1.0.0"
    }
    
    // ... 实现其他方法
}
```

### Python插件

继承 `Plugin` 类:

```python
from utils.interfaces import Plugin

class MyPlugin(Plugin):
    @property
    def name(self) -> str:
        return "my-plugin"
    
    @property
    def version(self) -> str:
        return "1.0.0"
    
    # ... 实现其他方法
```
