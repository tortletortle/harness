# Rust 产品交付说明

## 产品边界

Rust 交付物由两个可执行程序组成：

- `eas-gui`：面向高强度独立开发者的桌面控制台，连接当前权威 Python/FastAPI 运行时。
- `eas-server`：本地 Rust 契约校验与工具注册服务，用于跨语言迁移、集成验证和后续运行时替换。

当前生产任务执行权威仍是 Python/FastAPI。Rust 服务不会伪装成第二套完整 Executor；只有通过兼容测试覆盖的能力才对外提供。这一边界避免双运行时产生状态分叉。

## 桌面端能力

- 设计生成、设计审批与反馈修订。
- 计划列表、步骤状态、执行、暂停、恢复、跳步和分支回滚。
- 工具审批队列、审计日志、实时日志和 WebSocket 状态同步。
- AI 助手与运行配置。
- Ctrl/Cmd+K 快捷操作中心、Ctrl/Cmd+1/2/3 工作流切换、F5 刷新和 Esc 分层退出。
- Token 变更验证、连接代际隔离、REST 轮询兜底和日志有界缓存。

## 安全默认值

- 服务默认绑定 `127.0.0.1`，显式设置 `EAS_RUST_BIND` 才能改变监听地址。
- API Token 来自 `EAS_API_TOKEN`，否则读取 `config/api_token`；空 Token 拒绝启动。
- 受保护端点支持 Bearer Token 和 `X-API-Token`，使用常数时间比较。
- CORS 仅允许本地开发控制台来源，不使用通配符。
- 服务路由名只允许 ASCII 字母、数字、连字符和下划线。

## 启动

先启动权威 Python 后端（默认 `http://127.0.0.1:8000`），然后：

```powershell
$env:EAS_API_TOKEN = Get-Content config/api_token -Raw
$env:EAS_API_BASE = "http://127.0.0.1:8000"
target/release/eas-gui.exe
```

可选 Rust 服务：

```powershell
$env:EAS_PYTHON_API_BASE = "http://127.0.0.1:8000"
$env:EAS_RUST_BIND = "127.0.0.1:8090"
target/release/eas-server.exe
```

## 构建和验收

- Windows：`powershell -ExecutionPolicy Bypass -File scripts/build-rust.ps1`
- Linux/macOS：`bash scripts/build-rust.sh`
- 发布：推送 `v*` Tag 或手动运行 `Rust desktop release` GitHub Actions。

验收门槛：rustfmt、workspace all-target check、workspace tests、Clippy correctness、release build、GUI 与 Python API 兼容测试全部通过。保留用于跨版本契约反序列化的暂未读取字段允许产生 `dead_code` 提示。若本机缺少系统链接器，应以 CI 的干净 runner 结果作为发布依据，不能把 `cargo check` 等同于可发布二进制。

## 2026-09-17 本地验收记录

- Windows GNU + Zig 0.13 完成七个 workspace 测试程序的真实链接和执行：**36 passed / 0 failed**。
- `cargo clippy --workspace --all-targets --no-deps -- -D warnings` 通过；rustfmt、all-target、YAML、diff check 与编辑器诊断通过。
- release 产物：`eas-server.exe` 2,102,272 bytes，SHA-256 `9EF15F0F90DDCA28497EBB2EE4EA5AA3F2BA9EF4BFCF5189FA089DD59D6C0F5D`；Workbench UX 重构版 `eas-gui.exe` 6,402,048 bytes，SHA-256 `E9E60E973F9621B6EA4CDAC143DCA6DAFF5FF5C1680896898577243BAA51C78C`。
- release 冒烟通过：公开健康端点、受保护端点 401/200、缺失 Token 明确失败，以及 Python 后端不可用时 GUI 安全启动。
- 真实 Windows 桌面截图复核覆盖连接状态、工作台壳层、设置、命令面板、项目列表、计划详情与错误卡片；最终布局采用固定 Activity Bar、上下文侧栏、中央工作区、独立工具箱、受限宽度 AI 辅助侧栏、底部输出区和状态栏，并重跑 GUI 31 项测试与严格 Clippy。
- Zig 的 libunwind/链接优化提示及 `sqlx-postgres 0.7.4` future-incompatibility 报告属于第三方工具链/依赖提示；严格 Clippy 对 workspace 源码无告警。

## 2026-09-17 GUI 视觉升级记录（egui 0.36）

- 框架升级：egui/eframe 0.28 → **0.36.2**（文字锐化、crisp 渲染、Popup 重写、`App::ui` 新契约、`Panel` 合并），并接入 egui-phosphor 0.14（图标字体）、egui-notify 0.23（右下角 Toast）、egui_commonmark 0.25（设计书 Markdown 渲染）、egui_extras 0.36。
- 视觉：`theme.rs` 重构为浅/暗双主题函数式调色板（Activity Bar 底部月亮/太阳按钮即时切换）；卡片带极浅投影；计划卡选中态主色描边 + 左侧 3px 竖条；空状态组件（大图标 + 引导文案）；Heading 19→17；`active.expansion` 按压反馈；窗口/弹层阴影分级。
- 图标：Activity Bar、顶栏搜索、连接状态、设置标题、步骤状态标记全部由 Unicode 符号换成 Phosphor 字形；运行中步骤使用 120ms 时间驱动加载帧模拟旋转。
- 字体管线：CJK 正文 + 等宽（Cascadia Mono/Consolas 优先，CJK 兜底）+ Phosphor 三层一次性挂载。
- 通知：`set_notice` 内容变化按语义分级弹 Toast（错误/成功/信息）；连接断开/恢复即时 Toast；底部通知条保留为可回看的持久位。
- 验收：rustfmt ✅；`cargo clippy --workspace --all-targets --no-deps -- -D warnings`（GNU target）✅；GUI 测试 **31 passed / 0 failed**（zig 链接真实执行）✅；编辑器诊断 0 ✅。
- release 产物（zig + cargo-zigbuild，Windows GNU）：`eas-gui.exe` 15,131,136 bytes，SHA-256 `35F7DA3BEAD1490FF3A525190CB90FD15723CD07C17AFE22AE20EEBCAC281865`；`eas-server.exe` 2,152,448 bytes，SHA-256 `F8A363122AC41F621B23E8347E4305D2B2E9465ED68F0D03F1F61E482F1FCB8E`。体积增长主要来自 egui 0.36 新渲染栈（vello_cpu/skrifa）与 commonmark/图标字体资源。
- 迁移要点（后续升级参考）：`Rounding`→`CornerRadius`（u8）、`Margin` i8、`FontData` 需 `Arc`、`SidePanel/TopBottomPanel`→`Panel`（`*_width/height`→`*_size`）、`SelectableLabel::new`→`Button::selectable`、`ctx.style_mut`→`all_styles_mut`、`ctx.screen_rect`→`content_rect`、`ui.close_menu`→`ui.close`、`App::update`→`App::ui`（面板挂根 `Ui`）。

## 2026-09-17 实用性增强记录（研究文档 §7 全量落地）

- 命令面板：条目带 Phosphor 图标；关键词含拼音首字母（xjrw/sjsy/jhzx/gjx/sx/zs/sz），中文用户 Ctrl+K 后可直接首字母命中；新增「切换暗色主题」「切换审计日志」两条命令。
- 审批队列：A/R 快捷键批准/拒绝队首审批（输入框聚焦时不抢键，无修饰键才触发），标题旁有键位提示。
- 连接状态：顶栏在线/离线标签可点击，弹出连接诊断（API Base、WS 状态、Token 配置、最后错误、REST 兜底刷新按钮）。
- 日志区：新增「暂停滚动」（冻结 stick_to_bottom 便于排查）与「复制全部」（写入剪贴板）按钮，等级过滤 chips 保留。
- 相对时间：计划卡时间显示「x 分钟/小时/天前」（chrono 解析 RFC3339 与无时区 ISO 两种格式，失败回退截短），hover 显完整时间戳。
- 持久化：eframe persistence 开启——窗口大小/位置自动记忆；暗色主题偏好经 `App::save` 存储、启动时恢复。
- 验收：rustfmt ✅；clippy 全 workspace `-D warnings` ✅；GUI 测试 31 passed / 0 failed（zig 链接真实执行）✅；编辑器诊断 0 ✅。
- release 产物：`eas-gui.exe` 15,745,536 bytes，SHA-256 `12F98EACC9BDF74BD68F09037000E263D3CCE8270A473AD96EDD75DCCB61D47B7`。

## 2026-09-17 交互增强第二轮（w1-w4）

- **审计日志表格化**：`egui::Grid` 替换为 `egui_extras::TableBuilder`（斑马纹 + 列宽可拖拽调整）。7 列：结果（exact 44）/ 时间（initial 128，相对时间 + 悬停原始时间戳）/ 计划、步骤、工具（initial 76-90）/ 指令（remainder）/ 拦截原因（initial 110），全列 clip + at_least，表头 24px、行高 22px，长文本悬停显示全文。
- **密度档位**：设置页新增「外观」卡片（暗色主题 + 紧凑密度复选框），命令面板新增「首选项: 切换紧凑密度」（拼音 md/jc）。compact 档缩小 item_spacing/button_padding/interact_size/字号/卡片内边距；`theme::set_compact` + `theme::install` 即时生效，eframe storage 键 `eas.compact_mode` 持久化（模式同 `eas.dark_mode`）。
- **运行中动效**：运行中计划卡顶部 2px 不确定进度光带（三角波往返、周期 2.4s，`theme::running_indicator`，内部 request_repaint 驱动）；计划详情进度条在 Running 状态启用 `ProgressBar::animate`。
- **键盘导航**：计划列表支持 ↑/↓ 在可见（过滤后）计划间移动选择、Enter 选中首项；typing 守卫（任何控件持焦点时不响应），两处计划面板同步生效。

### 验收

- `cargo fmt` 通过；`cargo clippy --workspace -- -D warnings` 0 告警；测试 **31 passed / 0 failed**；LSP 诊断 0。
- release 产物：`target/x86_64-pc-windows-gnu/release/eas-gui.exe` 15,795,712 bytes，SHA-256 `AECA67449E4D42847B4407F645B364B965E59EC2745826DA8336A7F05551F7E2`。
