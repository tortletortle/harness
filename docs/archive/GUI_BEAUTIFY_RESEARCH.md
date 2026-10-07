# eas-gui 界面美化与实用性研究（2026-09-17）

> **实施状态（2026-09-17）**：P1–P4 已全部落地——egui/eframe 升级到 0.36.2，
> phosphor 图标、暗色双主题（Activity Bar 月亮/太阳切换）、卡片阴影、选中竖条、
> 空状态组件、egui-notify Toast、egui_commonmark 设计书渲染、等宽字体管线、
> 运行中步骤旋转帧均已合入。rustfmt / clippy -D warnings（全 workspace）/
> GUI 31 项测试全部通过；链接沿用 zig + cargo-zigbuild（本机无 MSVC）。
> 剩余可选项见 §7 实用性清单（拼音搜索、审批快捷键、表格化审计）。
>
> **P5 增量（2026-09-18）**：§7 清单进一步落地——命令面板拼音首字母、审批 A/R
> 快捷键、审计 TableBuilder 表格此前已合入；本次新增日志长行/多行点击展开完整
> payload（LogLine 增加单调 seq 标识展开状态，环形裁剪后不串行）与设计书列表
> ↑/↓/Enter 键盘导航（与项目列表同一 typing 守卫语义）。rustfmt / clippy
> -D warnings / workspace 测试 37 passed（GUI 32，含新增 log_seq 回归）全部通过。
>
> **P6 增量（2026-09-19）**：新增可选布局模式（Focus / Workbench / Timeline），与
> GuiMode 及执行权限解耦；顶栏布局条、设置页外观区、Ctrl+K 均可切换，每种布局
> 独立记住项目栏/输出/助手开合并持久化（`eas.layout.v1`），旧偏好缺省回退经典
> 工作台。Timeline 只串联后端已有步骤状态，不编造时间与历史。rustfmt 通过；
> 本机无 MSVC/Windows SDK，改用 GNU 工具链 + cargo-zigbuild（zig 0.16）验证：
> eas-gui 编译链接通过，GUI 37/37 测试通过（含 5 项布局回归）；workspace 级
> clippy 与 msvc 链接待 CI 覆盖。

## 0. 现状结论

当前 `eas-gui` 基于 **egui/eframe 0.28**，已完成 Workbench UX 重构（Activity Bar + 上下文侧栏 + 中央工作区 + 工具箱 + AI 侧栏 + 输出区 + 状态栏），`theme.rs` 集中了浅色令牌（白卡片 / #F6F7F9 页面底 / #2563EB 蓝主色 / 药丸徽章）。基础已经相当好；剩余差距主要在五个维度：**文字清晰度、图标语言、层次感（阴影/深浅对比）、动效反馈、信息密度控制**。

## 1. 最高性价比：升级 egui 到 0.32+（建议 0.33/0.34）

0.28 → 0.32+ 的收益直接命中"美观"痛点，属于零设计成本的画质升级：

- **0.29**：文本垂直居中修复、线条/描边像素对齐更锐利、浅色模式文本选择更好看；支持暗/亮双主题跟随系统（`Theme` 双 Style）。
- **0.30**：`Modal` 模态层（回滚确认、审批弹窗可用它阻断背景输入，替代手写遮罩）。
- **0.31**：所有线条/矩形 crisp 对齐；`Rounding` 更名 `CornerRadius`。
- **0.32**：**文字明显更锐利（尤其浅色模式）**、Popup/菜单/Tooltip 全面重写（分层正确、可嵌套）、Atom 布局（按钮可自然混排图标+文字）、SVG 高质量渲染。

迁移成本评估（基于现有代码扫描）：
- `Rounding::same(f32)` → `CornerRadius::same(u8)`（theme.rs 约 15 处，app.rs 若干处）；`Margin` 变 i8。
- egui 0.28 无 `widgets.disabled`，0.29+ 有——theme.rs 里的注释项可以真正实现禁用态。
- `FontData::from_owned` 在新版本要 `Arc` 包装；WS/轮询架构不受影响。
- 预计一次 apply_patch + 半天修编译告警即可，31 项 GUI 测试是安全网。

## 2. 字体与图标：观感提升第二大来源

### 2.1 中文字体
现状按路径加载微软雅黑/黑体/宋体。建议：
- 候选列表首位加 **Segoe UI Variable / 微软雅黑 UI（msyhui）**，或直接 `include_bytes!` 内嵌 **Noto Sans SC / Source Han Sans Subset**，保证三平台观感一致、发布件不依赖系统字体（体积 +3~8MB，可用子集化压到 ~1.5MB）。
- 数字/日志用等宽：内嵌 **JetBrains Mono** 并只挂到 `Monospace` family，日志区和 SHA/ID 立即变专业。
- 字号阶梯微调：Heading 19 → 17（配合 strong），新增 `TextStyle::Name("Title")` 22px 用于页面主标题，层次更分明。

### 2.2 图标字体（替换 Unicode 符号）
现状 Activity Bar 用 "▣ ◇ ≡ ⚙" 等 Unicode 字符，风格不统一且随字体漂移。方案：
- **egui-phosphor**（推荐，最流行；注意版本对应：egui 0.28 → egui-phosphor 0.6.x；升级 egui 后用对应新版）。`add_to_fonts` 挂为 Proportional fallback 后，图标可与文字直接混排。
- Activity Bar：工作区 `SQUARES_FOUR`、工具箱 `TOOLBOX`、AI `SPARKLE`、审计 `LIST_MAGNIFYING_GLASS`、设置 `GEAR`。
- 状态图标：✓/✗/◐ 换成 `CHECK_CIRCLE / X_CIRCLE / CIRCLE_NOTCH`，配合旋转动画（见 §4）。

## 3. 视觉层次：阴影、深浅、密度

egui 默认无卡片阴影，是"扁而平"观感的主因。在 `theme.rs` 增加两级 Frame：

```rust
// 静态卡片：极浅投影 + 弱描边（Stripe/Linear 风格）
pub fn card_frame() -> Frame {
    Frame::default()
        .fill(SURFACE)
        .stroke(Stroke::new(1.0, BORDER))
        .rounding(Rounding::same(10.0))
        .inner_margin(Margin::same(14.0))
        .shadow(Shadow { offset: vec2(0.0, 1.0), blur: 3.0,
                         spread: 0.0, color: Color32::from_black_alpha(10) })
}
// 浮层（命令面板/审批弹窗）：更深投影拉开层级
pub fn overlay_frame() -> Frame { /* blur 24, alpha 28, rounding 12 */ }
```

配套原则：
- **页面底与卡片对比再拉开一档**：PAGE_BG 可降到 #F3F4F6，卡片保持纯白，边界感更强。
- **分区标题体系**：12px 大写字距（egui 无 letter-spacing，可用 `SMALL + TEXT_FAINT + strong`）做 section label，如 "计划 / PLANS"。
- **斑马纹 + 悬停行**：列表行 hover 用 HOVER_BG 全行高亮（`ui.interact` + `rect_filled`），选中行左侧 3px ACCENT 竖条（VS Code 风格），比当前仅背景色更醒目。
- **空状态插画化**：无计划/无设计书时居中显示 phosphor 大图标（48px, TEXT_FAINT）+ 一句引导 + 主按钮，而不是空白。
- **密度档位**：`Spacing` 提供 comfortable/compact 两档（item_spacing 10/6，行高 28/22），设置里切换——高强度用户会感谢 compact。

## 4. 动效与即时反馈（egui 内建能力，零依赖）

- `ctx.animate_value_with_time` / `animate_bool`：侧栏开合宽度、审批卡片进入淡入（120–150ms，缓出）。
- 运行中步骤 `CIRCLE_NOTCH` 图标用 `ui.ctx().request_repaint()` + 时间取模旋转，替代静态 ◐。
- 按钮按压反馈：0.32 起 `Visuals.widgets.active.expansion = 1.0` 即有轻微放大感。
- 进度：计划执行中在卡片顶部加 2px 不确定进度条（动画渐变条），状态一目了然。
- Toast：错误/成功提示用 **egui-notify**（右下角滑入，自动消退），替代行内堆叠的 error_inline——行内保留给表单校验。

## 5. 生态 crate 按需引入（都与 egui 版本强绑定，升级后选新版）

| crate | 用途 | 建议 |
|---|---|---|
| egui-phosphor | 统一图标语言 | **强烈建议** |
| egui-notify | Toast 通知 | 建议 |
| egui_table / egui_extras TableBuilder | 审计日志表格（列宽可调、斑马纹、虚拟滚动） | 建议，审计面板量大时收益高 |
| egui_virtual_list | 日志区大量条目虚拟滚动 | 日志 >5k 行时引入 |
| egui_commonmark | 设计书 Markdown 渲染（现在多半是纯文本） | **审阅体验质变**，强烈建议 |
| egui_flex | 复杂自适应工具栏布局 | 可选 |
| egui_dock / egui_tiles | 可拖拽 Dock 布局 | 暂不建议——现固定 Workbench 更符合"控制台"心智，引入拖拽反增复杂度 |

## 6. 暗色主题（双主题跟随系统）

升级到 0.29+ 后用 `ctx.set_style_of(Theme::Dark, ...)` 注册第二套令牌即可跟随系统。暗色板建议（与现蓝主色同族）：
PAGE_BG #111418 / SURFACE #1A1F26 / BORDER #2A313B / TEXT #E5E9F0 / ACCENT 亮化为 #3B82F6，SOFT 系用低透明度主色叠加而非固定浅色。`theme.rs` 改为 `struct Palette` + 两个 const 实例，所有 `pub const` 换成 `pub fn palette(dark: bool)`，视觉令牌集中不变。

## 7. 实用性（不只好看）快速清单

1. **命令面板增强**：Ctrl+K 已有——给结果加图标 + 分组（导航/操作/最近），模糊匹配加拼音首字母（`pinyin` crate，中文用户命中率大增）。
2. **键盘可达性**：列表 ↑/↓ 选择 + Enter 进入；审批队列 A/R 快捷批准拒绝（有确认）。
3. **连接状态可操作**：离线红点可点击 → 弹出诊断卡（API base、最后错误、重连按钮），而非只读标签。
4. **日志区**：等级过滤 chips（error/warn/info）+ 暂停滚动按钮 + 复制全部；错误行点击展开完整 payload。
5. **相对时间**：列表时间显示"3 分钟前"，hover 显完整时间戳。
6. **窗口记忆**：eframe `persistence` feature 记住窗口大小/位置/主题/密度选择。

## 8. 建议实施顺序（每步可独立验收）

| 阶段 | 内容 | 工作量 | 风险 |
|---|---|---|---|
| P1 | 卡片阴影 + 选中竖条 + 空状态 + 密度微调（纯 theme.rs/app.rs，0.28 内完成） | 0.5 天 | 低 |
| P2 | 内嵌字体（Noto Sans SC 子集 + JetBrains Mono）+ egui-phosphor 0.6 图标替换 | 0.5 天 | 低 |
| P3 | egui 升级 0.33/0.34 + 迁移（CornerRadius/Margin/FontData/Modal） | 1 天 | 中（有 31 项测试兜底） |
| P4 | 暗色双主题 + egui-notify + egui_commonmark 设计书渲染 | 1 天 | 低 |
| P5 | 动效（侧栏动画/旋转 spinner/进度条）+ 命令面板增强 + 表格化审计 | 1–2 天 | 低 |

验收沿用现门槛：rustfmt / clippy -D warnings / workspace tests / GUI 兼容测试 / Windows 真机截图复核。

## 9. 参考

- egui 0.29–0.32 changelog（文本锐化、双主题、Modal、Popup 重写、Atom）
- egui 官方 3rd-party crates 清单（egui_dock、egui_table、egui_commonmark、egui-notify 等）
- hello_egui 合集（egui_flex / egui_inbox / egui_virtual_list / egui_material_icons）
- egui-phosphor 版本与 egui 版本对应关系（0.28→0.6.x）
- egui #3284 CSS-like styling 路线（未来 per-widget style/class 系统，长期关注）
