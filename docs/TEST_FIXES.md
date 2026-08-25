## 测试发现的问题及修复

### ✅ 已修复的问题

#### 1. 执行计划 JSON 序列化问题 ✅
**问题描述：**
- ExecutionPlan 包含 UUID、datetime、PlanStatus 枚举等复杂类型
- 使用标准 `json.dumps([seg.model_dump() for seg in plan.segments])` 无法序列化
- 错误：`Object of type UUID is not JSON serializable`

**修复方案：**
- 使用 Pydantic 的 `model_dump(mode="json")` 方法
- 这会自动将 UUID 转换为字符串，datetime 转换为 ISO 格式，枚举转换为值
- 文件：`python/database.py` - `save_plan()` 方法（第169-177行）

**修复代码：**
```python
segments_json = json.dumps(
    [seg.model_dump(mode="json") for seg in plan.segments],
    ensure_ascii=False,
)
```

---

#### 2. SQLite 连接未正确关闭 ✅
**问题描述：**
- 数据库操作后使用 `conn.close()` 手动关闭连接
- 异常情况下连接不会关闭，导致文件句柄泄漏
- Windows 下表现为无法删除临时数据库文件
- 错误：`PermissionError: [WinError 32] 另一个程序正在使用此文件`

**修复方案：**
- 使用 `closing()` 上下文管理器确保连接总是被关闭
- 无论成功或异常，连接都会自动释放
- 修复了所有数据库方法（8个方法）

**修复代码：**
```python
# 修复前
conn = self._get_connection()
# ... 操作 ...
conn.close()  # 如果异常，这行不会执行

# 修复后
with closing(self._get_connection()) as conn:
    # ... 操作 ...
    # 连接自动关闭，即使发生异常
```

**受影响的方法：**
1. `_initialize_tables()` - 表初始化
2. `save_design()` - 保存设计书
3. `get_design()` - 读取设计书
4. `save_plan()` - 保存执行计划
5. `get_plan()` - 读取执行计划
6. `list_designs()` - 列出设计书
7. `list_plans()` - 列出执行计划
8. `log()` - 记录日志

---

### 📊 修复验证

**测试结果：**
```
Ran 10 tests in 0.518s

OK
```

**所有测试通过：**
- ✅ test_design_round_trip_and_update
- ✅ test_list_queries
- ✅ test_plan_steps_logs_and_round_trip
- ✅ test_executor_end_to_end_without_llm
- ✅ test_plugin_load_reload_and_unload
- ✅ test_all_tools_are_registered
- ✅ test_command_runner_and_git
- ✅ test_file_creator_and_text_editor
- ✅ test_package_validation_and_unknown_tool
- ✅ test_web_fetcher_with_local_file

---

### 🎯 影响评估

**严重性：中等**
- 这两个问题都会导致功能失败
- 执行计划无法保存到数据库（数据丢失）
- Windows 下数据库文件无法清理（资源泄漏）

**影响范围：**
- 所有使用数据库持久化的功能
- 特别是执行计划的保存和读取

**修复前行为：**
- 执行计划保存失败，返回 False
- 临时数据库无法删除，累积文件句柄

**修复后行为：**
- 执行计划正常保存和读取
- 连接正确释放，无资源泄漏

---

### 📝 经验教训

1. **序列化复杂对象**
   - 使用 Pydantic 的 `model_dump(mode="json")` 而不是 `model_dump()`
   - 确保所有字段都能转换为 JSON 兼容类型

2. **资源管理**
   - 始终使用上下文管理器（`with`）管理资源
   - 不要手动调用 `close()`，除非在 `finally` 块中

3. **测试驱动**
   - 完整的测试能发现真实的问题
   - Windows 和 Linux 的行为可能不同，需要跨平台测试

---

### ✨ 其他改进

同时在修复过程中进行的改进：
- 添加了 `ensure_ascii=False` 以正确处理中文
- 统一了错误处理模式
- 确保了所有方法的异常安全性

---

**修复人员：AI Assistant**  
**修复日期：2026-08-21**  
**相关测试：scripts/test_coverage.py**
