# 工具库

这个目录存放所有可用的工具。

> **注意**：当前系统实际内置的 7 个工具实现在 `python/tools/`（由
> `python/tools/__init__.py` 的 ToolRegistry 注册），本目录只保留工具规范与示例。
> 新增工具请遵循同样的 `execute()` / `validate()` 接口约定。

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
