# EAS 2.0 高强度测试总结报告（中文）

**测试日期**: 2026-08-25  
**测试方式**: 仅测试，不修改任何源代码  
**测试状态**: ✓ 完成

---

## 📋 执行的测试脚本

| 脚本名称 | 大小 | 功能描述 | 测试结果 |
|---------|------|--------|--------|
| test_instruction_mismatch.py | 3.2 KB | 验证推断指令与工具声明的一致性 | 6/6 不匹配 ❌ |
| test_tool_support.py | 3.0 KB | 验证工具对推断指令的支持 | 1/5 失败 ❌ |
| test_e2e_execution.py | 3.4 KB | 端到端执行流程验证 | 1/3 失败 ❌ |
| test_db_consistency.py | 5.0 KB | 数据库状态值合法性检查 | 4 个脏数据 ❌ |

---

## 🐛 发现的核心问题

### 【Bug #1】web_fetcher 推断指令 'fetch' 不存在 ⚠️ 严重

- **位置**: `executor/__init__.py` L180-181
- **问题**: `_infer_instruction('web_fetcher', {...})` 返回 `'fetch'`
- **影响**: `web_fetcher.execute('fetch', {...})` 返回 **"Invalid instruction: fetch"**
- **后果**: 所有未明确指定 instruction 的 web_fetcher 步骤都会**执行失败**
- **验证证据**:
  - ✗ test_instruction_mismatch: 推断 'fetch' 不在声明中
  - ✗ test_tool_support: tool.execute('fetch') 返回 Invalid instruction
  - ✗ test_e2e_execution: web_fetcher 步骤执行失败

### 【Bug #2】text_editor 推断指令 'write' 不在声明中 ⚠️ 中等

- **位置**: `executor/__init__.py` L168-169
- **问题**: 推断 `'write'`，但声明指令为 `['read_file', 'append_to_file', 'replace_in_file', 'insert_at_line']`
- **现状**: 工具的 execute() 中有兼容处理，所以能工作
- **风险**: 架构不一致，容易引发维护问题
- **验证证据**:
  - ✗ test_instruction_mismatch: 推断 'write' 不在声明中
  - ✓ test_tool_support: tool.execute('write') 成功（工具兼容处理）
  - ✓ test_e2e_execution: 步骤成功（工具兼容处理掩盖）

### 【Bug #3】command_runner 推断指令 'run' 不在声明中 ⚠️ 中等

- **位置**: `executor/__init__.py` L175-177
- **问题**: 推断 `'run'`，但声明指令为 `['run_command']`
- **现状**: 工具的 execute() 中有兼容处理，所以能工作
- **风险**: 架构不一致，容易引发维护问题

### 【Bug #4】数据库残留旧状态值 ⚠️ 高

- **位置**: `harness/data/eas.db` - steps 表
- **问题**: 存在 `'COMPLETED'`(2个) 和 `'FAILED'`(2个) 的旧格式状态值
- **预期**: 应为 `'Success'` 和 `'Failed'`（首字母大写）
- **影响**: 这 4 个步骤通过 `get_plan()` API 读取时会 **Pydantic 验证失败**，导致 404
- **验证证据**:
  - ✗ test_db_consistency: 扫描发现 4 个不合法状态值

---

## 📊 详细测试数据

### 测试 1: 指令推断矩阵

```
总测试数: 6
不匹配数: 6 (100%)

具体结果:
  text_editor + {path, content}     => 推断 'write'    ✗ 不在声明
  text_editor + {path, old_text}    => 推断 'write'    ✗ 不在声明
  text_editor + {path}              => 推断 'write'    ✗ 不在声明
  command_runner + {command}        => 推断 'run'      ✗ 不在声明
  web_fetcher + {url}               => 推断 'fetch'    ✗ 不在声明
  web_fetcher + {url, path}         => 推断 'fetch'    ✗ 不在声明
```

### 测试 2: 推断指令工具支持

```
总测试数: 5
成功: 4  |  失败: 1

成功案例:
  [OK] text_editor:write          | 文件写入成功
  [OK] command_runner:run          | 命令执行完成
  [OK] command_runner:run_command  | 命令执行完成
  [OK] web_fetcher:fetch_content   | 内容获取成功

失败案例:
  [INVALID] web_fetcher:fetch      | 未知指令: fetch
```

### 测试 3: 端到端执行

```
总步骤数: 3
成功: 2  |  失败: 1

步骤 1 (text_editor 推断 'write'): ✓ SUCCESS
  - 操作: 创建文本文件
  - 输入: {path: /tmp/test_1.txt, content: hello}
  - 输出: 文件写入成功

步骤 2 (command_runner 推断 'run'): ✓ SUCCESS
  - 操作: 执行命令
  - 输入: {command: echo test > /tmp/test_2.txt}
  - 输出: 命令执行完成

步骤 3 (web_fetcher 推断 'fetch'): ✗ FAILED
  - 操作: 获取网页内容
  - 输入: {url: http://example.com}
  - 错误: 未知指令: fetch
```

### 测试 4: 数据库状态一致性

```
steps 表状态值集合: ['Success', 'FAILED', 'COMPLETED', 'Pending']
合法的状态值集合: ['Running', 'Pending', 'Success', 'Skipped', 'Failed']

不合法的状态值: ['COMPLETED', 'FAILED']

具体数据:
  - COMPLETED 状态: 2 个步骤（应为 'Success'）
  - FAILED 状态: 2 个步骤（应为 'Failed'）
  - 总计不合法: 4 个
```

---

## 🔍 根本原因分析

1. **推断逻辑与工具实现脱节**
   - `_infer_instruction()` 没有从工具的 `get_metadata()` 中验证推断结果
   - web_fetcher 的 'fetch' 是完全杜撰的指令，不存在于工具代码
   - text_editor 和 command_runner 的推断指令虽然被工具兼容处理，但这掩盖了问题

2. **测试覆盖不足**
   - 现有的 `test_instruction_inference.py` 只验证自己假设的指令名
   - 没有从注册表动态获取合法指令名做校验
   - 导致 11/11 通过是假阳性

3. **数据库迁移不完整**
   - 旧格式状态值未被清理或迁移
   - 导致历史数据无法正确读取

---

## ✅ 修复建议

### P1 (立即修复 - 影响生产)

**修复 web_fetcher 推断指令**

在 `executor/__init__.py` L180-181 修改:
```python
# 改为:
elif tool_id == "web_fetcher":
    if "path" in inputs or "output" in inputs or "save_to" in inputs:
        return "download_file"
    else:
        return "fetch_content"
```

这样可以立即解决 web_fetcher 执行失败问题。

### P2 (短期修复 - 架构一致)

**修复 text_editor 推断指令**

```python
elif tool_id == "text_editor":
    if "search" in inputs and "replace" in inputs:
        return "replace_in_file"
    elif "content" in inputs or "code" in inputs:
        return "append_to_file"
    elif "line_number" in inputs:
        return "insert_at_line"
    else:  # 默认为读
        return "read_file"
```

**修复 command_runner 推断指令**

```python
elif tool_id == "command_runner":
    return "run_command"  # 直接返回标准指令名
```

### P3 (数据清理)

运行数据库迁移脚本：

```sql
UPDATE steps SET status = 'Success' WHERE status = 'COMPLETED';
UPDATE steps SET status = 'Failed' WHERE status = 'FAILED';
```

### P4 (测试改进)

重写 `test_instruction_inference.py`，从注册表动态获取合法指令名做验证。

---

## 📈 测试统计

| 项目 | 总数 | 通过 | 失败 | 通过率 |
|-----|------|------|------|--------|
| 推断指令匹配 | 6 | 0 | 6 | 0% |
| 工具支持验证 | 5 | 4 | 1 | 80% |
| 端到端执行 | 3 | 2 | 1 | 67% |
| 数据库一致性 | 1 | 0 | 1 | 0% |

---

## 🎯 结论

**严重程度: 高 (HIGH)**

web_fetcher 的推断指令 bug 是一个**致命问题**，已通过多个测试维度全面验证：
- ✗ 声明中不包含推断指令
- ✗ 工具明确拒绝该指令
- ✗ 端到端执行实际失败

text_editor 和 command_runner 的推断指令虽然能工作，但由于架构不一致，存在长期风险。

数据库中的旧状态值导致历史数据无法正确访问，影响系统数据完整性。

**建议立即修复 P1 级 bug，短期内完成 P2 和 P3 的修复。**

---

## 📝 测试文件列表

所有测试脚本均保存在 `D:\spot\harness\python\` 目录下：

```
D:\spot\harness\python\
├── test_instruction_mismatch.py      (3.2 KB) - 推断指令与声明一致性验证
├── test_tool_support.py              (3.0 KB) - 工具对推断指令的支持验证
├── test_e2e_execution.py             (3.4 KB) - 端到端执行流程验证
├── test_db_consistency.py            (5.0 KB) - 数据库状态一致性检查
├── test_results_summary.txt          (7.9 KB) - 详细测试报告
└── TEST_SUMMARY_CN.md                         - 本文件
```

可以随时重新运行这些测试脚本来验证修复效果。

---

**报告生成时间**: 2026-08-25  
**测试方式**: 仅测试，不修改代码  
**报告状态**: ✓ 完成
