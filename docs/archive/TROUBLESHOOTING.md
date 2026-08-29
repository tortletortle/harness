# EAS 2.0 测试与故障排除指南

## 快速诊断检查清单

当系统出现问题时，按以下顺序检查：

### 1. 基础设施检查 (30秒)

```bash
# 检查 Backend 服务
curl http://127.0.0.1:8000/api/status

# 应该返回:
# {"status":"running","llm_providers":[...],"tools_count":7,...}
```

如果失败 → 启动 Backend:
```bash
cd harness/python
python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
```

### 2. 快速烟雾测试 (10秒)

```bash
cd harness/python
python test_smoke.py
```

如果全部通过 → 系统基础设施正常  
如果任何失败 → 查看具体失败的测试

### 3. Executor 验证 (5秒)

```bash
python test_executor_fixes.py
```

如果通过 → Executor 修复有效  
如果失败 → Executor 有回退，检查代码

---

## 常见问题及解决方案

### 问题 1: "Invalid instruction" 错误

**症状**: 步骤执行失败，错误消息 "Invalid instruction"

**诊断**:
```bash
# 运行单元测试验证修复
python test_executor_fixes.py
```

**解决方案**:
1. 确认 `executor/__init__.py` 包含 `_infer_instruction()` 方法
2. 检查工具参数格式是否正确
3. 验证 tool 字段格式: `"tool_name"` 或 `"tool_name:instruction"`

**验证**:
```python
from executor import DefaultExecutor
executor = DefaultExecutor(None, {}, None)

# 测试推断
result = executor._infer_instruction("git_operator", {"action": "init"})
print(result)  # 应该输出: "git_init"
```

---

### 问题 2: API 返回 405 Method Not Allowed

**症状**: API 调用返回 405 状态码

**常见原因**: 使用了错误的端点路径

**正确的端点**:
```python
# ✅ 正确
POST /api/design              # 创建设计
GET  /api/design/{id}         # 获取设计
POST /api/design/{id}/approve # 批准设计（自动生成计划）
GET  /api/plan/{id}           # 获取计划
POST /api/plan/{id}/execute   # 执行计划

# ❌ 错误
POST /api/designs             # 不存在
POST /api/plans               # 不存在
```

**修复示例**:
```python
# 错误
response = requests.post("http://localhost:8000/api/designs", json={...})

# 正确
response = requests.post("http://localhost:8000/api/design", json={...})
```

---

### 问题 3: API 返回 404 Not Found

**症状**: 获取资源时返回 404

**可能原因**:
1. ID 不存在
2. 端点路径错误（plural vs singular）
3. 资源未正确保存到数据库

**诊断步骤**:
```python
# 1. 验证 ID 是否存在
import sqlite3
conn = sqlite3.connect('harness/data/eas.db')
cursor = conn.cursor()

# 检查设计
cursor.execute("SELECT design_id FROM designs WHERE design_id = ?", (design_id,))
print("Design exists:", cursor.fetchone())

# 检查计划
cursor.execute("SELECT plan_id FROM plans WHERE plan_id = ?", (plan_id,))
print("Plan exists:", cursor.fetchone())
```

---

### 问题 4: 计划包含 0 个步骤

**症状**: 计划创建成功但执行时没有步骤

**诊断**:
```python
# 检查计划的步骤
import sqlite3
conn = sqlite3.connect('harness/data/eas.db')
cursor = conn.cursor()

plan_id = "your-plan-id"
cursor.execute("SELECT COUNT(*) FROM steps WHERE plan_id = ?", (plan_id,))
step_count = cursor.fetchone()[0]
print(f"步骤数量: {step_count}")

# 列出步骤
cursor.execute("SELECT step_id, description, tool FROM steps WHERE plan_id = ?", (plan_id,))
for row in cursor.fetchall():
    print(f"  - {row[1]} (工具: {row[2]})")
```

**可能原因**:
1. Designer 的 `generate_plan()` 没有生成步骤
2. 步骤未持久化到数据库
3. Planner 返回空列表

**解决步骤**:

1. **添加调试日志** (designer.py):
```python
async def generate_plan(self, design: Design) -> ExecutionPlan:
    plan = await self.planner.generate_plan(design)
    
    # 添加这行
    print(f"🔍 生成的计划包含 {len(plan.steps)} 个步骤")
    
    return plan
```

2. **检查 Planner 输出**:
```python
# 在 planner 中添加日志
print(f"Planner 输入: {design.user_request}")
print(f"Planner 输出步骤数: {len(steps)}")
```

3. **验证数据库保存**:
```python
# 在 database.py 的 save_plan() 中
def save_plan(self, plan: ExecutionPlan):
    print(f"保存计划 {plan.plan_id}，包含 {len(plan.steps)} 个步骤")
    # ... 保存逻辑
```

---

### 问题 5: 测试超时

**症状**: E2E 测试等待 120 秒后超时

**常见原因**:
1. 计划没有步骤（见问题 4）
2. Executor 没有启动
3. AI 服务响应慢

**解决方案**:

调整超时时间:
```python
# 在 test_e2e_comprehensive.py 中
max_wait = 300  # 增加到 5 分钟
```

检查执行状态:
```bash
# 查看 backend 日志
tail -f harness/api.log
```

手动测试执行:
```bash
curl -X POST http://127.0.0.1:8000/api/plan/{plan_id}/execute
```

---

### 问题 6: Unicode 编码错误 (Windows)

**症状**: `UnicodeEncodeError: 'gbk' codec can't encode character`

**原因**: Windows 终端默认使用 GBK 编码，无法显示某些 Unicode 字符

**解决方案**:

1. **已修复的测试脚本**: 所有测试脚本已改用 ASCII 字符
   - ✓ 替换为 [OK]
   - ✗ 替换为 [FAIL]
   - → 替换为 [INFO]

2. **如果在新代码中遇到**:
```python
# 错误
print(f"✓ 测试通过")

# 正确
print(f"[OK] 测试通过")
```

3. **设置环境变量**:
```bash
# Windows CMD
set PYTHONIOENCODING=utf-8

# PowerShell
$env:PYTHONIOENCODING="utf-8"
```

---

### 问题 7: 数据库锁定

**症状**: `database is locked` 错误

**原因**: 多个进程同时访问 SQLite 数据库

**解决方案**:
```bash
# 停止所有 Python 进程
taskkill /F /IM python.exe

# 或者更优雅地
pkill -f "uvicorn"
pkill -f "test_"
```

---

### 问题 8: AI API 调用失败

**症状**: 设计或计划生成失败

**诊断**:
```bash
# 检查 AI 服务状态
curl http://127.0.0.1:8000/api/status

# 查看可用的提供商
# 应该显示: "llm_providers":["bbapi","hejuapi","newapi","stepfun"]
```

**可能原因**:
1. API 密钥未配置或过期
2. 网络连接问题
3. API 配额用尽

**解决方案**:

检查配置文件:
```bash
cat harness/config/models.yaml
```

验证 API 密钥:
```python
import yaml
with open('harness/config/models.yaml') as f:
    config = yaml.safe_load(f)
    
for provider, settings in config['providers'].items():
    api_key = settings.get('api_key', 'NOT SET')
    print(f"{provider}: {api_key[:10]}..." if api_key != 'NOT SET' else f"{provider}: NOT SET")
```

---

## 调试工具和技巧

### 1. 实时日志查看

```bash
# Backend 日志
tail -f harness/api.log

# 带过滤
tail -f harness/api.log | grep ERROR
```

### 2. 数据库检查

```python
# 快速脚本: db_check.py
import sqlite3

conn = sqlite3.connect('harness/data/eas.db')
cursor = conn.cursor()

# 统计
cursor.execute("SELECT COUNT(*) FROM designs")
print(f"设计数量: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM plans")
print(f"计划数量: {cursor.fetchone()[0]}")

cursor.execute("SELECT COUNT(*) FROM steps")
print(f"步骤数量: {cursor.fetchone()[0]}")

# 最近的条目
cursor.execute("SELECT design_id, user_request FROM designs ORDER BY created_at DESC LIMIT 3")
print("\n最近的设计:")
for row in cursor.fetchall():
    print(f"  {row[0]}: {row[1][:50]}...")
```

### 3. API 端点测试

创建 `test_api.sh`:
```bash
#!/bin/bash

BASE_URL="http://127.0.0.1:8000"

echo "测试健康检查..."
curl -s $BASE_URL/api/status | jq .

echo -e "\n创建设计..."
RESPONSE=$(curl -s -X POST $BASE_URL/api/design \
  -H "Content-Type: application/json" \
  -d '{"user_request":"创建测试文件"}')
  
DESIGN_ID=$(echo $RESPONSE | jq -r '.design.design_id')
echo "设计 ID: $DESIGN_ID"

echo -e "\n批准设计..."
curl -s -X POST $BASE_URL/api/design/$DESIGN_ID/approve \
  -H "Content-Type: application/json" \
  -d "{\"design_id\":\"$DESIGN_ID\",\"approved\":true}" | jq .
```

### 4. 性能分析

```python
import time
import requests

def measure_api_call(method, url, **kwargs):
    start = time.time()
    response = requests.request(method, url, **kwargs)
    duration = time.time() - start
    
    print(f"{method} {url}")
    print(f"  状态码: {response.status_code}")
    print(f"  耗时: {duration:.3f}s")
    return response, duration

# 使用
response, duration = measure_api_call(
    "POST", 
    "http://127.0.0.1:8000/api/design",
    json={"user_request": "测试"}
)
```

---

## 测试最佳实践

### 在修改代码后

1. **运行单元测试** (5秒)
```bash
python test_executor_fixes.py
```

2. **运行烟雾测试** (10秒)
```bash
python test_smoke.py
```

3. **如果以上通过，提交代码**

### 在发布前

1. **运行完整测试套件** (3分钟)
```bash
python test_master_runner.py
```

2. **检查测试报告**
```bash
cat test_report_*.txt | grep -E "\[FAIL\]|\[ERROR\]"
```

3. **所有测试通过后才能发布**

### 持续监控

1. **定期运行烟雾测试**
```bash
# 每小时运行一次
watch -n 3600 "cd harness/python && python test_smoke.py"
```

2. **监控 API 响应时间**
```bash
while true; do
  curl -w "\n响应时间: %{time_total}s\n" -s http://127.0.0.1:8000/api/status > /dev/null
  sleep 60
done
```

---

## 紧急恢复步骤

如果系统完全崩溃:

### 1. 停止所有服务
```bash
# Windows
taskkill /F /IM python.exe

# Linux/Mac
pkill -9 python
```

### 2. 清理数据库锁
```bash
rm -f harness/data/eas.db-journal
```

### 3. 备份当前数据
```bash
cp harness/data/eas.db harness/data/eas.db.backup.$(date +%Y%m%d_%H%M%S)
```

### 4. 重启服务
```bash
cd harness/python
python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
```

### 5. 验证系统
```bash
python test_smoke.py
```

---

## 获取帮助

### 检查日志
1. Backend 日志: `harness/api.log`
2. 测试报告: `harness/python/test_report_*.txt`
3. 数据库: `harness/data/eas.db`

### 常用命令参考
```bash
# 查看 Backend 进程
ps aux | grep uvicorn

# 查看端口占用
netstat -ano | findstr :8000    # Windows
lsof -i :8000                    # Linux/Mac

# 查看数据库大小
ls -lh harness/data/eas.db

# 查看最近的测试
ls -lt harness/python/test_report_*.txt | head -5
```

---

**最后更新**: 2026-08-24 23:20  
**维护者**: EAS 2.0 开发团队
