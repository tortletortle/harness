# 工具库

这个目录存放所有可用的工具。

## 工具规范

每个工具必须：

1. **提供工具Schema** - 描述工具的能力、输入输出格式
2. **实现标准接口** - `execute()` 和 `validate()` 方法
3. **自包含** - 包含所有依赖或声明依赖
4. **可测试** - 提供测试用例

## 工具结构

```
tools/
├── example_tool.py       # 示例工具
├── image_generator/      # 复杂工具（目录形式）
│   ├── __init__.py
│   ├── main.py
│   ├── config.yaml
│   └── README.md
└── ...
```

## 注册工具

通过ToolManager注册：

```python
from tools.example_tool import ExampleTool, TOOL_SCHEMA

# 注册工具
await tool_manager.register_tool(TOOL_SCHEMA)
```

## 创建新工具

参考 `example_tool.py` 的实现。
