# 工具库

> **规范入口**：新增/修改工具请读 **[docs/TOOL_INTERFACE_DESIGN.md](../docs/TOOL_INTERFACE_DESIGN.md)**
> （主设计稿 §6 的工具层细化：实现契约、注册、授权边界、测试口径与落地清单）。
>
> **注意**：系统内置工具的实际实现在 `python/tools/`（由 `python/tools/__init__.py`
> 的 ToolRegistry 注册），本目录只剩历史遗留文件：
>
> - `example_tool.py` 是旧 EAS 规范示例（ToolManager / TOOL_SCHEMA / validate()
>   约定），**与现行代码不符，不要参照**；
> - 对应的旧 schema 是 `schemas/tool_schema.json`，现行工具体系不消费它。
>
> 两份遗留文件的清理在 docs/TOOL_INTERFACE_DESIGN.md §8 演进项中跟踪。

## 现行约定速览（详见规范文档）

1. 工具类提供 `get_metadata()`（tool_id / name / description / category / version /
   capabilities.instructions）与 `execute(instruction, inputs)` 两个静态方法；
2. 输入路径键名必须复用授权层认识的名字（path / file_path / cwd / output /
   save_to / url 等），否则 Grant 资源校验不可见；
3. 返回值必含 `success`；策略拒绝加 `blocked: True`；大输出尾部截断；
4. 子进程一律 argv 列表 + 服务端白名单模板，无 shell；
5. 注册一行进 `ToolRegistry._register_default_tools`；有副作用需同步登记
   `python/capabilities.py` 的副作用集合。
