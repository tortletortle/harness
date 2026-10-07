# 可执行测试任务：订单数据清洗、对账与审计报告

## 测试目标
让 Helm / EAS 使用真实模型完成 **需求 → 设计书 → 审批生成计划 → 真实工具执行 → 独立验收 → 交付报告**。不仅检查 Completed，还检查数据、金额、原始文件完整性及重复执行是否稳定。

## 可直接提交给系统的任务正文

在当前独立工作区完成一个只使用 Python 标准库的订单清洗对账程序。

### 输入与约束
- 输入文件已提供：`data/orders.csv`；不要改写它。
- CSV 为 UTF-8，列为 `order_id,customer,amount,status`。
- 不安装依赖、不联网、不读取凭据、不访问工作区之外的文件。
- 只允许使用 `file_creator` 创建 `reconcile.py`、`test_reconcile.py`；再使用 `test_runner` 执行服务端预置模板。不要使用 command_runner、codegen agent 或其他工具。
- `test_runner` 模板 `reconcile` 执行 `python reconcile.py --input data/orders.csv --output out/orders`；模板 `pytest` 执行本工作区的 `test_reconcile.py`。步骤 inputs 使用 `suite`，不必提供 command、cwd、args。
- 计划建议 4 个步骤：创建程序 → 创建测试 → 执行 reconcile → 执行 pytest。使用显式依赖；不要直接用 file_creator 伪造 CSV/JSON 结果。
- CLI 必须支持任意 `--input` 和 `--output` 路径；不能硬编码样例答案。

### 清洗规则
1. 所有输入字段去除首尾空白。记录原 CSV 行号，标题行为 1。
2. `order_id` 不能为空；否则拒绝，原因 `missing_order_id`。
3. 按输入顺序保留同一非空订单号的第一次出现。后续相同订单号记为重复，不参与其他验证。即使第一次出现被拒绝，后续同号仍记为重复。
4. 客户不能为空；否则 `missing_customer`。
5. 金额使用 Decimal，不使用浮点累计。只接受十进制数：可有负号，整数部分至少一位，小数部分最多两位。不接受科学计数法、NaN、Infinity。格式错误为 `invalid_amount`；合法数值小于零为 `negative_amount`；零金额有效。
6. 状态只接受 `paid`、`refunded`、`pending`；否则 `invalid_status`。
7. 多个字段同时有错时，按上述顺序记录第一个原因。
8. 所有输出金额为两位小数字符串。有效订单按 order_id 排序；错误和重复记录按原始行号排序；客户汇总按 customer 排序。

### 必须产出
`reconcile.py`、`test_reconcile.py`，以及输出目录下六个文件：
- `cleaned.csv`：`order_id,customer,amount,status`，仅有效订单。
- `rejected.csv`：`row_number,order_id,reason`，不包含重复记录。
- `duplicates.csv`：`row_number,order_id`。
- `customers.csv`：`customer,paid_total,refunded_total,net_total,pending_total`，包含有效订单涉及的所有客户。
- `summary.json`：键必须包含 `input_rows,duplicate_rows,rejected_rows,valid_rows,paid_orders,refunded_orders,pending_orders,paid_total,refunded_total,net_total,pending_total`。
- `report.md`：用中文说明有效、拒绝、重复数量，收款、退款、待付款和净额，以及金额平衡检查结果。待付款不计入净额。

金额公式：`net_total = paid_total - refunded_total`。
数量公式：`input_rows = duplicate_rows + rejected_rows + valid_rows`。

### 样例的公开验收值
输入 12 行，重复 1 行，拒绝 4 行，有效 7 行；paid 4 单、refunded 2 单、pending 1 单。
收款 **260.50**，退款 **35.50**，净额 **225.00**，待付款 **40.00**。
不要依靠这些常量生成结果；验收还会替换输入。

`test_reconcile.py` 至少有 4 个真正包含断言的测试，覆盖金额、重复、非法输入及重复执行稳定性。测试不能依赖修改原始输入。

## 固定输入

```csv
order_id,customer,amount,status
A001,Alice,120.50,paid
A002,Bob,80.00,paid
A003,Alice,25.50,refunded
A002,Bob,80.00,paid
A004,Carol,invalid,paid
A005,,15.00,paid
A006,Bob,40.00,pending
A007,Carol,60.00,paid
A008,Alice,10.00,refunded
A009,Dan,-5.00,paid
A010,Dan,0.00,paid
A011,Eve,30.00,cancelled
```

## 独立验收门槛（由测试执行器核验）

| 编号 | 验收 |
|---|---|
| A1 | 真实生成设计书和执行计划；执行前检查工具、路径和生成代码。 |
| A2 | 当前逻辑步骤成功、Plan 和最新 Run 均 Completed，执行权释放；历史失败 Run 必须保留，不能仅凭状态判整体通过。 |
| A3 | 六个文件实际存在，公开样例的逐行结果、错误原因及全部统计值匹配。 |
| A4 | 原始输入 SHA256 不变。 |
| A5 | 再运行一次程序，六个输出文件逐字节 SHA256 不变。 |
| A6 | 对未交给模型的另一组 CSV 做黑盒验收，含 Unicode、0.1/0.2 精度、NaN/Infinity、重复及缺失字段，防止硬编码。 |
| A7 | 缺失输入时非零退出；不得报告成功或破坏上一次有效输出。 |
| A8 | 模型编写的测试至少收集 4 项并通过；独立验收结果不依赖这些自测。 |
| A9 | 最终交付报告实际保存，内容哈希有效，当前步骤和自测结果须与真实执行一致；记录模型请求、错误和终态，脱敏保存证据。 |

任何一项失败都要单独记录。区分“模型任务实现错误”和“编排系统缺陷”；发现后先复现，再修复和复验，不手动伪造输出或把首次失败覆盖为成功。

## 执行预算与边界
- 一次真实设计和计划生成；必要时最多再做一次有记录的修订。
- 计划最多 6 步，执行观察期限 10 分钟，报告额外等待不超过 3 分钟。
- 工具执行在独立 Git 工作区和数据库中；模型请求走已授权的现有路由。
- 独立工作区不是操作系统级沙箱，因此执行前必须审查生成代码，不执行越权计划。
- 不主动制造供应商限流、不更改密钥、不重启生产服务。

## 如何复用

本次执行器在独立进程中注册 `reconcile` 和 `pytest` 两个白名单模板，不修改生产配置。项目内复跑时使用一个全新的批次名，避免混合缓存、数据库和证据：

```powershell
$env:EAS_ORDER_CASE = 'case-new'
python scripts/repair-20260923/order_e2e.py generate
# 阅读该批次 proposed-steps.json，人工审查路径、工具及 Python 代码后明确签收：
python scripts/repair-20260923/order_e2e.py approve-reviewed
python scripts/repair-20260923/order_e2e.py execute
python scripts/repair-20260923/order_e2e_oracle.py
```

`generate` 和 `execute` 阶段会调用真实模型，可能产生费用；`approve-reviewed` 只记录人工审查签收，不调用模型。独立验收脚本不调用模型，但会执行本次生成并已审查的程序。

如只需检查已经生成的六个输出文件，可使用附带的无网络、无凭据独立检查器：

```bash
python verify_outputs.py path/to/out/orders
```

检查器验的是文件内容，不代表 Plan/Run、模型调用或报告生命周期也已通过。完整端到端结果见同包的测试报告。
