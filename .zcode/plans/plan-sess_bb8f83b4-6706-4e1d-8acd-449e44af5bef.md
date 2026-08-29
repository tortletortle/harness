## Rust egui 桌面控制台 — 先设计后实现

### A. 设计稿更新（docs/AGENT_HARNESS_DESIGN.md，遵守 §20 版本变更规则）
- 新增 **"20. 桌面控制台（Rust egui）设计"** 章节（原 §20 版本变更规则顺延为 §21），内容包括：
  - **定位**：Web 界面的桌面原生补充，同一 Python FastAPI 后端（:8000，token 认证）——明确排除 rust/server(:8090)（近乎无消费者的脚手架，文档记录该决策）
  - **架构**：eframe 0.28 UI 线程 + tokio 后台线程（REST 轮询 + WS 订阅）→ mpsc channel 桥接 → `ctx.request_repaint`；复用设计稿已验证的"纯推送 + 30s 轮询兜底"模式（§16.7）
  - **面板布局**：顶栏（服务状态/token 配置）、左栏（计划列表）、中央（计划详情/步骤/控制按钮）、底栏（实时日志流）
  - **数据同步**：plan_update WS 推送（后端已有 version 围栏）+ 30s REST 兜底，与 Web 端同构
  - **DTO 策略**：GUI 本地类型模块对齐 Python API 实际形状（eas-common 枚举缺 Approved/Retry/RolledBack，已过期——文档记录"fork 不复用"决策及理由）
  - **分期**：阶段1 骨架+计划列表+详情+实时状态+日志流+执行控制（execute/pause/resume/skip）；阶段2 审批卡片（对接已有 ApprovalManager）+审计+回滚；阶段3 设计书生成/审阅/拒绝反馈
  - **依赖决策**：新增 tokio-tungstenite（WS 客户端，reqwest 0.11 无 WS）；egui_extras 暂不引入（egui::Grid 够用）
- §11 当前 EAS 映射表补 `rust/gui` 行；§10 UI/审批行标注 egui 端
- 版本号 → 0.8-draft（顺带修正文首过期的"0.1-draft"），变更记录追加 0.8 条目

### B. 阶段 1+2 实现（本次交付）
1. `rust/gui/src/` 模块化：`types.rs`（GUI 本地 DTO + 解析测试用真实 API JSON 夹具）、`api.rs`（reqwest 客户端：status/plans/plan/execute/pause/resume/skip + 认证头）、`ws.rs`（tokio-tungstenite 日志流 + plan_update）、`state.rs`（AppState + channel 桥接 + 状态 reducer）、`app.rs`（四面板布局 + 控制按钮）、`main.rs`（tokio 运行时 + eframe 启动）
2. 控制操作：开始/暂停/恢复/跳过（409 detail 错误在 UI 显示）；回滚与审批卡片留阶段 2 后续/阶段 3
3. `cargo test`：DTO 解析（含 Rust 枚举补齐 Approved/Retry/RolledBack 的反序列化）、状态 reducer（plan_update 应用/版本围栏/30s 兜底触发）
4. 根 Cargo.toml 增加 tokio-tungstenite workspace 依赖
5. 全量验证：cargo test --workspace + pytest + vitest 不回归；提交推送（CI 三 job 自动验证 Rust job）

### C. 验证
- `cargo run --bin eas-gui` 连真实后端手动走查一遍（计划列表/详情/日志流/执行控制）
- 浏览器端不回归

### 明确不做
- rust/server(:8090) 不扩展不删除（保持现状，文档记录）
- 设计书生成/审阅 UI（阶段 3）、审批卡片与回滚（阶段 2 尾）本次不实现
- 不改 Python 后端任何代码