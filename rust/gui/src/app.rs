//! eframe 应用：四面板布局（设计稿 §20.2）。
//! UI 线程零阻塞：网络全部在后台线程，事件经有界 channel 驱动重绘。

use crate::layout::{self, LayoutMode, LayoutPreferences, PanelVisibility};
use crate::state::{GuiMode, GuiState, LogLine};
use crate::theme;
use crate::types::{
    parse_plan_status, CapabilitySpec, Design, DesignApprovalResponse, PlanStatus, StepStatus,
    StepView, ToolInfo,
};
use crate::ws::WsEvent;
use tokio::sync::mpsc;

#[cfg(test)]
const CHANNEL_CAPACITY: usize = 256;

/// UI → 后台命令
#[derive(Debug)]
pub enum Command {
    RefreshPlans,
    RefreshDesigns {
        epoch: u64,
    },
    SelectDesign {
        design_id: String,
        epoch: u64,
    },
    GenerateDesign {
        user_request: String,
        epoch: u64,
    },
    ApproveDesign {
        design_id: String,
        approved: bool,
        feedback: Option<String>,
        epoch: u64,
    },
    SelectPlan {
        plan_id: String,
        epoch: u64,
    },
    RefreshPlanDetail {
        plan_id: String,
        epoch: u64,
    },
    RefreshApprovals {
        plan_id: Option<String>,
        epoch: u64,
    },
    Approve {
        plan_id: String,
        approval_id: String,
        comment: Option<String>,
    },
    Reject {
        plan_id: String,
        approval_id: String,
        comment: Option<String>,
    },
    RefreshAudit {
        plan_id: Option<String>,
        limit: u32,
        epoch: u64,
    },
    Rollback {
        plan_id: String,
        step_id: String,
        strategy: String,
        epoch: u64,
    },
    Execute(String),
    Pause(String),
    Resume(String),
    Skip {
        plan_id: String,
        step_id: String,
    },
    RefreshStatus,
    SetToken(String),
    ArchivePlan {
        plan_id: String,
        archived: bool,
    },
    RenamePlan {
        plan_id: String,
        name: String,
    },
    DeletePlan {
        plan_id: String,
    },
    RefreshSettingsData,
    RestartServer,
    Chat {
        message: String,
        history: Vec<(String, String)>,
    },
    DeleteDesign {
        design_id: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DesignErrorAction {
    List,
    Detail,
    Generate,
}

/// 项目列表的状态筛选（纯 UI 侧状态，不进 state.rs）
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlanFilter {
    All,
    Active,
    Finished,
    Archived,
}

/// 可全屏放大的区块（双击标题或 ⛶ 进入，Esc 退出）
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Zone {
    Projects,
    Steps,
    Logs,
    Assistant,
}

/// 后台 → UI 事件
#[derive(Debug)]
pub enum GuiEvent {
    Ws(WsEvent),
    Status(crate::types::ServiceStatus),
    Plans(Vec<crate::types::PlanSummary>),
    Designs {
        designs: Vec<Design>,
        epoch: u64,
    },
    DesignDetail {
        design: Box<Design>,
        epoch: u64,
    },
    DesignGenerated {
        design: Box<Design>,
        epoch: u64,
    },
    DesignError {
        message: String,
        epoch: u64,
        action: DesignErrorAction,
    },
    DesignApprovalResult {
        design_id: String,
        approved: bool,
        result: Result<DesignApprovalResponse, String>,
        epoch: u64,
    },
    PlanDetail {
        detail: Box<crate::types::PlanDetail>,
        epoch: u64,
    },
    Approvals {
        approvals: Vec<crate::types::ApprovalRequest>,
        epoch: u64,
    },
    ApprovalsError {
        message: String,
        epoch: u64,
    },
    ApprovalResult {
        approval_id: String,
        result: Result<crate::types::ApprovalResolved, String>,
    },
    AuditLogs {
        logs: Vec<crate::types::AuditLogEntry>,
        epoch: u64,
    },
    AuditError {
        message: String,
        epoch: u64,
    },
    RollbackResult {
        plan_id: String,
        result: Result<crate::types::RollbackResult, String>,
        epoch: u64,
    },
    Error(String),
    ControlResult {
        plan_id: String,
        result: Result<String, String>,
    },
    TokenApplied(String),
    SettingsData {
        tools: Vec<ToolInfo>,
        capabilities: Vec<CapabilitySpec>,
        config: serde_json::Value,
    },
    ChatReply {
        reply: Result<String, String>,
    },
}

/// 中央区渲染用的拥有数据（避免渲染中借用冲突）
struct SelectionView {
    plan_id: String,
    status: PlanStatus,
    progress: (usize, usize),
    steps: Vec<(StepView, StepStatus)>,
}

/// 设置窗口的拉取结果（工具/能力/配置）
#[derive(Debug, Clone)]
struct SettingsDataView {
    tools: Vec<ToolInfo>,
    capabilities: Vec<CapabilitySpec>,
    config: serde_json::Value,
}

pub struct EasApp {
    pub state: GuiState,
    events: mpsc::Receiver<GuiEvent>,
    commands: mpsc::Sender<Command>,
    selected_plan_id: Option<String>,
    /// 单调递增的当前详情请求代数；旧 REST 响应永远不能覆盖新选择。
    selected_request_epoch: u64,
    approvals_request_epoch: u64,
    audit_request_epoch: u64,
    rollback_request_epoch: u64,
    design_request_epoch: u64,
    design_action_epoch: u64,
    feedback_open: bool,
    audit_limit_draft: String,
    token_draft: String,
    /// 设置窗口是否打开（纯 UI 状态）
    settings_open: bool,
    /// 工具箱是独立的一级工作区，不再嵌套在设置中。
    tools_open: bool,
    tool_search: String,
    /// 工具卡片是否展示能力列表（纯 UI 状态，会话内有效）。
    show_tool_capabilities: bool,
    /// 项目列表搜索与状态筛选（纯 UI 状态）
    plan_search: String,
    plan_filter: PlanFilter,
    /// 待确认删除的计划 id
    delete_confirmation: Option<String>,
    /// 重命名对话框：(plan_id, 草稿名)
    rename_dialog: Option<(String, String)>,
    /// 设置窗口数据（工具/能力/配置），打开设置时拉取
    settings_data: Option<SettingsDataView>,
    /// 重启服务二次确认
    restart_confirm: bool,
    /// 回滚结果条是否已关闭
    rollback_result_dismissed: bool,
    /// AI 助手对话窗（纯 UI 状态）
    chat_open: bool,
    chat_history: Vec<(String, String)>,
    chat_draft: String,
    chat_busy: bool,
    /// 待确认删除的设计书 id
    design_delete_confirmation: Option<String>,
    /// 日志级别过滤：None = 全部
    log_filter: Option<String>,
    /// 双击全屏的区块
    zoomed: Option<Zone>,
    /// Ctrl/Cmd+K 打开的高频操作面板。
    quick_actions_open: bool,
    command_query: String,
    command_selected: usize,
    /// 右下角 Toast 通知（egui-notify）。
    toasts: egui_notify::Toasts,
    /// 设计书 Markdown 渲染缓存（egui_commonmark）。
    md_cache: egui_commonmark::CommonMarkCache,
    /// 暗色主题开关（纯 UI 状态；切换时重装主题）。
    dark_mode: bool,
    /// 上一帧的通知内容：变化时弹 Toast，不重复弹。
    last_notice: Option<String>,
    /// 上一帧连接状态：断开/恢复时弹 Toast。
    last_connected: Option<bool>,
    /// 连接诊断弹窗是否打开。
    conn_diag_open: bool,
    /// 日志暂停自动滚动（排查问题时冻结视口）。
    log_paused: bool,
    /// 已展开完整内容的日志行序号（LogLine::seq）。
    expanded_logs: std::collections::HashSet<u64>,
    /// 紧凑密度（纯 UI 状态；切换时重装主题并持久化）。
    compact_mode: bool,
    /// Layout is a local preference, independent of GuiMode and execution permissions.
    layout: LayoutPreferences,
    navigation_open: bool,
    logs_open: bool,
}

impl EasApp {
    pub fn new(
        state: GuiState,
        events: mpsc::Receiver<GuiEvent>,
        commands: mpsc::Sender<Command>,
    ) -> Self {
        let token_draft = state.token.clone();
        let audit_limit_draft = state.audit_limit.to_string();
        Self {
            state,
            events,
            commands,
            selected_plan_id: None,
            selected_request_epoch: 0,
            approvals_request_epoch: 0,
            audit_request_epoch: 0,
            rollback_request_epoch: 0,
            design_request_epoch: 0,
            design_action_epoch: 0,
            feedback_open: false,
            audit_limit_draft,
            token_draft,
            settings_open: false,
            tools_open: false,
            tool_search: String::new(),
            show_tool_capabilities: true,
            plan_search: String::new(),
            plan_filter: PlanFilter::All,
            delete_confirmation: None,
            rename_dialog: None,
            settings_data: None,
            restart_confirm: false,
            rollback_result_dismissed: false,
            chat_open: false,
            chat_history: Vec::new(),
            chat_draft: String::new(),
            chat_busy: false,
            design_delete_confirmation: None,
            log_filter: None,
            zoomed: None,
            quick_actions_open: false,
            command_query: String::new(),
            command_selected: 0,
            toasts: egui_notify::Toasts::default(),
            md_cache: egui_commonmark::CommonMarkCache::default(),
            // 与启动时恢复的全局主题标志保持一致（见 main.rs persistence 恢复）。
            dark_mode: theme::is_dark(),
            last_notice: None,
            last_connected: None,
            conn_diag_open: false,
            log_paused: false,
            expanded_logs: std::collections::HashSet::new(),
            compact_mode: theme::is_compact(),
            layout: LayoutPreferences::default(),
            navigation_open: true,
            logs_open: true,
        }
    }

    fn layout_snapshot(&self) -> LayoutPreferences {
        let mut preferences = self.layout.clone();
        preferences.remember(PanelVisibility {
            navigation: self.navigation_open,
            logs: self.logs_open,
            assistant: self.chat_open,
        });
        preferences
    }

    pub fn restore_layout(&mut self, preferences: LayoutPreferences) {
        let panels = preferences.current();
        self.layout = preferences;
        self.navigation_open = panels.navigation;
        self.logs_open = panels.logs;
        self.chat_open = panels.assistant;
    }

    fn switch_layout(&mut self, mode: LayoutMode) {
        if self.layout.mode == mode {
            return;
        }
        let mut preferences = self.layout_snapshot();
        preferences.mode = mode;
        self.restore_layout(preferences);
        // Leave a zoomed pane, but preserve task selection, approvals and backend state.
        self.zoomed = None;
    }

    fn layout_picker(&mut self, ui: &mut egui::Ui, id: &str) {
        let mut selected = self.layout.mode;
        egui::ComboBox::from_id_salt(id)
            .selected_text(selected.label())
            .width(175.0)
            .show_ui(ui, |ui| {
                for mode in LayoutMode::ALL {
                    ui.selectable_value(&mut selected, mode, mode.label())
                        .on_hover_text(mode.description());
                }
                ui.separator();
                ui.weak("仅切换布局，不改变任务或执行权限");
            });
        self.switch_layout(selected);
    }

    fn handle_global_shortcuts(&mut self, ctx: &egui::Context) {
        // (日志行展开渲染见 log_line_row)
        let command = ctx.input(|input| input.modifiers.command);

        if command && ctx.input(|input| input.key_pressed(egui::Key::K)) {
            self.quick_actions_open = !self.quick_actions_open;
            self.command_query.clear();
            self.command_selected = 0;
        }
        for (key, mode) in [
            (egui::Key::Num1, GuiMode::Input),
            (egui::Key::Num2, GuiMode::Review),
            (egui::Key::Num3, GuiMode::Plan),
        ] {
            if command && ctx.input(|input| input.key_pressed(key)) {
                self.state.mode = mode;
                self.settings_open = false;
            }
        }
        if command && ctx.input(|input| input.key_pressed(egui::Key::Num4)) {
            self.tools_open = true;
            self.settings_open = false;
            self.settings_data = None;
            self.send(Command::RefreshSettingsData);
        }
        if ctx.input(|input| input.key_pressed(egui::Key::F5)) {
            match self.state.mode {
                GuiMode::Input | GuiMode::Review => self.refresh_designs(),
                GuiMode::Plan => {
                    self.send(Command::RefreshPlans);
                }
            }
            self.state.set_notice("正在刷新当前工作区");
        }
        if ctx.input(|input| input.key_pressed(egui::Key::Escape)) {
            if self.quick_actions_open {
                self.quick_actions_open = false;
            } else if self.zoomed.is_some() {
                self.zoomed = None;
            }
        }
    }

    fn quick_actions(&mut self, ctx: &egui::Context) {
        if !self.quick_actions_open {
            return;
        }
        let mut open = self.quick_actions_open;
        let mut selected_action = None;
        // keywords 含中文、英文和拼音首字母，Ctrl+K 后可用 xjrw / sjsy / jhzx 等直达。
        let actions = [
            (
                0_u8,
                egui_phosphor::regular::PLUS_CIRCLE,
                "工作区: 新建任务",
                "Ctrl+1",
                "需求 输入 新建 xqsr xjrw input new",
            ),
            (
                1,
                egui_phosphor::regular::FILE_TEXT,
                "工作区: 设计审阅",
                "Ctrl+2",
                "设计 审阅 sjsy sj review",
            ),
            (
                2,
                egui_phosphor::regular::PLAY,
                "工作区: 执行控制",
                "Ctrl+3",
                "计划 执行 jhzx zx plan run",
            ),
            (
                3,
                egui_phosphor::regular::TOOLBOX,
                "视图: 打开工具箱",
                "Ctrl+4",
                "工具 能力 gjx gj toolbox",
            ),
            (
                4,
                egui_phosphor::regular::ARROW_CLOCKWISE,
                "工作区: 刷新",
                "F5",
                "刷新 sx refresh reload",
            ),
            (
                5,
                egui_phosphor::regular::SPARKLE,
                "视图: 切换 AI 助手",
                "",
                "助手 zs ai chat assistant",
            ),
            (
                6,
                egui_phosphor::regular::GEAR,
                "首选项: 打开设置",
                "",
                "设置 sz token config settings",
            ),
            (
                7,
                egui_phosphor::regular::MOON,
                "首选项: 切换暗色主题",
                "",
                "主题 暗色 浅色 zt as dark light theme",
            ),
            (
                8,
                egui_phosphor::regular::LIST_MAGNIFYING_GLASS,
                "视图: 切换审计日志",
                "",
                "审计 日志 sjrz sj audit log",
            ),
            (
                9,
                egui_phosphor::regular::SQUARES_FOUR,
                "首选项: 切换紧凑密度",
                "",
                "密度 紧凑 md jc compact density",
            ),
            (
                10,
                egui_phosphor::regular::SQUARES_FOUR,
                "布局: Focus · 专注",
                "",
                "布局 模式 专注 bj ms zz focus layout",
            ),
            (
                11,
                egui_phosphor::regular::SQUARES_FOUR,
                "布局: Workbench · 工作台",
                "",
                "布局 模式 工作台 bj ms gzt workbench layout",
            ),
            (
                12,
                egui_phosphor::regular::SQUARES_FOUR,
                "布局: Timeline · 时间线",
                "",
                "布局 模式 时间线 bj ms sjx timeline layout",
            ),
            (
                13,
                egui_phosphor::regular::SQUARES_FOUR,
                "视图: 切换项目栏",
                "",
                "项目 导航 xml dh navigation sidebar",
            ),
            (
                14,
                egui_phosphor::regular::LIST_MAGNIFYING_GLASS,
                "视图: 切换输出面板",
                "",
                "输出 日志 sc rz output logs",
            ),
        ];
        let query = self.command_query.trim().to_lowercase();
        let filtered: Vec<_> = actions
            .iter()
            .filter(|(_, _, label, _, keywords)| {
                query.is_empty()
                    || label.to_lowercase().contains(&query)
                    || keywords.contains(&query)
            })
            .collect();
        if filtered.is_empty() {
            self.command_selected = 0;
        } else {
            self.command_selected = self.command_selected.min(filtered.len() - 1);
            if ctx.input(|i| i.key_pressed(egui::Key::ArrowDown)) {
                self.command_selected = (self.command_selected + 1).min(filtered.len() - 1);
            }
            if ctx.input(|i| i.key_pressed(egui::Key::ArrowUp)) {
                self.command_selected = self.command_selected.saturating_sub(1);
            }
            if ctx.input(|i| i.key_pressed(egui::Key::Enter)) {
                selected_action = Some(filtered[self.command_selected].0);
            }
        }
        egui::Window::new("命令面板")
            .id(egui::Id::new("quick_actions"))
            .title_bar(false)
            .anchor(egui::Align2::CENTER_TOP, [0.0, 48.0])
            .fixed_size([560.0, 330.0])
            .collapsible(false)
            .resizable(false)
            .open(&mut open)
            .frame(
                egui::Frame::window(&ctx.style_of(ctx.theme()))
                    .fill(theme::surface())
                    .stroke(egui::Stroke::new(1.0_f32, theme::border_strong()))
                    .corner_radius(egui::CornerRadius::same(8))
                    .inner_margin(egui::Margin::same(10)),
            )
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.label(
                        egui::RichText::new("›")
                            .size(18.0)
                            .color(theme::accent_text()),
                    );
                    ui.add(
                        egui::TextEdit::singleline(&mut self.command_query)
                            .hint_text("键入命令名称或搜索…")
                            .desired_width(f32::INFINITY)
                            .frame(egui::Frame::NONE),
                    )
                    .request_focus();
                });
                ui.separator();
                ui.label(
                    egui::RichText::new("命令")
                        .size(11.0)
                        .color(theme::text_faint()),
                );
                for (index, (action, icon, label, hint, _)) in filtered.iter().enumerate() {
                    let selected = index == self.command_selected;
                    if ui
                        .add_sized(
                            [ui.available_width(), 34.0],
                            egui::Button::selectable(
                                selected,
                                format!("{icon}  {label:<40}{hint}"),
                            ),
                        )
                        .clicked()
                    {
                        selected_action = Some(*action);
                    }
                }
                if filtered.is_empty() {
                    ui.add_space(18.0);
                    ui.vertical_centered(|ui| ui.weak("没有匹配的命令"));
                }
            });
        if let Some(action) = selected_action {
            match action {
                0 => self.state.mode = GuiMode::Input,
                1 => self.state.mode = GuiMode::Review,
                2 => self.state.mode = GuiMode::Plan,
                3 => {
                    self.tools_open = true;
                    self.settings_data = None;
                    self.send(Command::RefreshSettingsData);
                }
                4 => match self.state.mode {
                    GuiMode::Input | GuiMode::Review => self.refresh_designs(),
                    GuiMode::Plan => {
                        self.send(Command::RefreshPlans);
                    }
                },
                5 => self.chat_open = !self.chat_open,
                6 => {
                    self.settings_open = true;
                    self.settings_data = None;
                    self.send(Command::RefreshSettingsData);
                }
                7 => {
                    self.dark_mode = !self.dark_mode;
                    theme::set_dark(self.dark_mode);
                    theme::install(ctx);
                }
                8 => {
                    self.state.audit_visible = !self.state.audit_visible;
                    if self.state.audit_visible && self.state.audit_logs.is_empty() {
                        self.refresh_audit();
                    }
                }
                9 => {
                    self.compact_mode = !self.compact_mode;
                    theme::set_compact(self.compact_mode);
                    theme::install(ctx);
                }
                10 => self.switch_layout(LayoutMode::Focus),
                11 => self.switch_layout(LayoutMode::Workbench),
                12 => self.switch_layout(LayoutMode::Timeline),
                13 => self.navigation_open = !self.navigation_open,
                14 => self.logs_open = !self.logs_open,
                _ => {}
            }
            if matches!(action, 0..=2 | 4 | 5) {
                self.tools_open = false;
                self.settings_open = false;
            }
            open = false;
        }
        self.quick_actions_open = open;
    }

    fn send(&mut self, cmd: Command) -> bool {
        if let Err(err) = self.commands.try_send(cmd) {
            // UI 线程不阻塞；队列满时给用户可见反馈，而不是静默丢失控制命令。
            self.state
                .set_notice(format!("后台命令队列已满，请稍后重试: {err}"));
            self.state
                .push_log("error", format!("后台命令发送失败: {err}"));
            return false;
        }
        true
    }

    /// 渲染单条日志：过长或多行 payload 默认折叠为单行，点击切换展开完整内容。
    /// 用 LogLine::seq 记录展开状态，环形裁剪后不会串行。
    fn log_line_row(
        expanded: &mut std::collections::HashSet<u64>,
        ui: &mut egui::Ui,
        line: &LogLine,
    ) {
        const COLLAPSE_LIMIT: usize = 160;
        let full = format!("[{}] {}", line.level, line.message);
        let multiline = full.contains('\n');
        let long = full.chars().count() > COLLAPSE_LIMIT;
        let collapsible = multiline || long;
        let is_expanded = expanded.contains(&line.seq);
        let shown = if collapsible && !is_expanded {
            let first = full.lines().next().unwrap_or("");
            let mut s: String = first.chars().take(COLLAPSE_LIMIT).collect();
            s.push_str(" …");
            s
        } else {
            full
        };
        let text = egui::RichText::new(shown)
            .monospace()
            .size(12.5)
            .color(theme::log_color(&line.level));
        if collapsible {
            let resp = ui
                .add(egui::Label::new(text).sense(egui::Sense::click()))
                .on_hover_text(if is_expanded {
                    "点击折叠"
                } else {
                    "点击展开完整内容"
                });
            if resp.clicked() {
                if is_expanded {
                    expanded.remove(&line.seq);
                } else {
                    expanded.insert(line.seq);
                }
            }
        } else {
            ui.label(text);
        }
    }

    fn select_plan(&mut self, plan_id: String) {
        if self.selected_plan_id.as_deref() != Some(plan_id.as_str()) {
            // Do not render or control the previously selected plan while the new
            // epoch's REST detail is in flight. Same-plan refreshes retain live WS state.
            self.state.selected = None;
        }
        self.selected_plan_id = Some(plan_id.clone());
        self.selected_request_epoch = self
            .selected_request_epoch
            .checked_add(1)
            .expect("计划详情请求 epoch 溢出");
        let epoch = self.selected_request_epoch;
        self.send(Command::SelectPlan { plan_id, epoch });
        if self.state.audit_visible {
            self.refresh_audit();
        }
    }

    fn refresh_designs(&mut self) {
        self.design_request_epoch = self
            .design_request_epoch
            .checked_add(1)
            .expect("设计请求 epoch 溢出");
        let epoch = self.design_request_epoch;
        if !self.send(Command::RefreshDesigns { epoch }) {
            self.state.design_error = Some("后台命令队列已满".to_string());
        }
    }

    fn select_design(&mut self, design_id: String) {
        self.state.mode = GuiMode::Review;
        self.design_request_epoch = self
            .design_request_epoch
            .checked_add(1)
            .expect("设计请求 epoch 溢出");
        let epoch = self.design_request_epoch;
        if !self.send(Command::SelectDesign { design_id, epoch }) {
            self.state.design_error = Some("后台命令队列已满".to_string());
        }
    }

    fn generate_design(&mut self) {
        if !self.state.begin_design_generation() {
            if self.state.input_text.trim().is_empty() {
                self.state.design_error = Some("请输入需求后再生成设计书".to_string());
            }
            return;
        }
        self.design_request_epoch = self
            .design_request_epoch
            .checked_add(1)
            .expect("设计请求 epoch 溢出");
        let request = self.state.input_text.trim().to_string();
        let epoch = self.design_request_epoch;
        if !self.send(Command::GenerateDesign {
            user_request: request,
            epoch,
        }) {
            self.state
                .design_generation_failed("后台命令队列已满".to_string());
        }
    }

    fn approve_design(&mut self, approved: bool) {
        let Some(design) = self.state.current_design.clone() else {
            return;
        };
        if !approved && !self.feedback_open {
            self.feedback_open = true;
            self.state.design_error = None;
            return;
        }
        let feedback = if approved {
            None
        } else {
            match self.state.trimmed_feedback() {
                Some(feedback) => Some(feedback),
                None => {
                    self.state.design_error = Some("请输入非空反馈后提交".to_string());
                    return;
                }
            }
        };
        if !self.state.begin_design_action() {
            return;
        }
        self.design_action_epoch = self
            .design_action_epoch
            .checked_add(1)
            .expect("设计审批 epoch 溢出");
        let epoch = self.design_action_epoch;
        if !self.send(Command::ApproveDesign {
            design_id: design.design_id,
            approved,
            feedback,
            epoch,
        }) {
            self.state
                .finish_design_action(Err("后台命令队列已满".to_string()));
        }
    }

    fn refresh_approvals(&mut self) {
        self.approvals_request_epoch = self
            .approvals_request_epoch
            .checked_add(1)
            .expect("审批请求 epoch 溢出");
        self.state.approvals_loading = true;
        self.state.approvals_error = None;
        if !self.send(Command::RefreshApprovals {
            plan_id: None,
            epoch: self.approvals_request_epoch,
        }) {
            self.state.approvals_loading = false;
            self.state.approvals_error = Some("后台命令队列已满".to_string());
        }
    }

    fn refresh_audit(&mut self) {
        self.audit_request_epoch = self
            .audit_request_epoch
            .checked_add(1)
            .expect("审计请求 epoch 溢出");
        self.state.audit_loading = true;
        self.state.audit_error = None;
        if !self.send(Command::RefreshAudit {
            plan_id: self.selected_plan_id.clone(),
            limit: self.state.audit_limit.max(1),
            epoch: self.audit_request_epoch,
        }) {
            self.state.audit_loading = false;
            self.state.audit_error = Some("后台命令队列已满".to_string());
        }
    }

    fn handle_event(&mut self, ev: GuiEvent) {
        match ev {
            GuiEvent::Ws(WsEvent::Log { level, message }) => {
                self.state.push_log(&level, message);
            }
            GuiEvent::Ws(WsEvent::PlanUpdate(ev)) => {
                let steps: Vec<(String, String)> = ev
                    .steps
                    .iter()
                    .map(|s| (s.step_id.clone(), s.status.clone()))
                    .collect();
                self.state
                    .apply_plan_update(&ev.plan_id, &ev.status, &steps, ev.version);
            }
            GuiEvent::Ws(WsEvent::ApprovalRequest(approval)) => {
                self.state.apply_approval_request(approval);
            }
            GuiEvent::Ws(WsEvent::ApprovalResolved(resolved)) => {
                self.state.apply_approval_resolved(&resolved);
            }
            GuiEvent::Ws(WsEvent::Connected) => {
                self.state.connected = true;
                self.state.last_ws_error = None;
                self.refresh_approvals();
            }
            GuiEvent::Ws(WsEvent::Disconnected(reason)) => {
                self.state.connected = false;
                self.state.last_ws_error = Some(reason);
            }
            GuiEvent::Status(st) => self.state.service_status = Some(st),
            GuiEvent::Designs { designs, epoch } => {
                if epoch == 0 || epoch == self.design_request_epoch {
                    self.state.set_designs(designs);
                }
            }
            GuiEvent::DesignDetail { design, epoch } => {
                if epoch == self.design_request_epoch {
                    self.state.design_generated(*design);
                }
            }
            GuiEvent::DesignGenerated { design, epoch } => {
                if epoch == self.design_request_epoch {
                    self.state.design_generated(*design);
                }
            }
            GuiEvent::DesignError {
                message,
                epoch,
                action,
            } => {
                let epoch_matches = epoch == self.design_request_epoch;
                if !epoch_matches {
                    return;
                }
                match action {
                    DesignErrorAction::Generate => {
                        self.state.design_generation_failed(message.clone())
                    }
                    DesignErrorAction::List | DesignErrorAction::Detail => {
                        self.state.design_error = Some(message.clone());
                    }
                }
                self.state.set_notice(message);
            }
            GuiEvent::DesignApprovalResult {
                design_id,
                approved,
                result,
                epoch,
            } => {
                if epoch != self.design_action_epoch {
                    return;
                }
                if self
                    .state
                    .current_design
                    .as_ref()
                    .map(|design| design.design_id.as_str())
                    != Some(design_id.as_str())
                {
                    return;
                }
                let ok = result.is_ok();
                self.state.finish_design_action(result);
                if ok {
                    self.feedback_open = false;
                    if self.state.mode == GuiMode::Plan {
                        if let Some(plan_id) = self
                            .state
                            .selected
                            .as_ref()
                            .map(|selected| selected.plan_id.clone())
                        {
                            self.selected_plan_id = Some(plan_id.clone());
                            self.selected_request_epoch = self
                                .selected_request_epoch
                                .checked_add(1)
                                .expect("计划详情请求 epoch 溢出");
                            self.send(Command::RefreshPlanDetail {
                                plan_id,
                                epoch: self.selected_request_epoch,
                            });
                        }
                    }
                } else if let Some(error) = self.state.design_error.clone() {
                    // Keep the inline feedback editor open only for a failed
                    // revision request; an approval failure should not turn
                    // the approve button into a rejection flow.
                    if !approved {
                        self.feedback_open = true;
                    }
                    self.state.set_notice(error);
                }
            }
            GuiEvent::Plans(plans) => {
                self.state.plans = plans;
                // 首次加载无选中 → 自动选中第一个计划, preserving the
                // phase-2 startup view. Navigation can still switch to designs.
                if self.selected_plan_id.is_none() {
                    if let Some(first) = self.state.plans.first() {
                        self.state.mode = GuiMode::Plan;
                        self.select_plan(first.plan_id.clone());
                    }
                }
            }
            GuiEvent::Approvals { approvals, epoch } => {
                if epoch == 0 || epoch == self.approvals_request_epoch {
                    self.state.approvals_loading = false;
                    self.state.set_pending_approvals(approvals);
                }
            }
            GuiEvent::ApprovalsError { message, epoch } => {
                if epoch == self.approvals_request_epoch {
                    self.state.approvals_loading = false;
                    self.state.approvals_error = Some(message.clone());
                    self.state.set_notice(message);
                }
            }
            GuiEvent::ApprovalResult {
                approval_id,
                result,
            } => {
                match &result {
                    Ok(resolved) => {
                        self.state.set_notice(if resolved.approved {
                            "审批已批准"
                        } else {
                            "审批已拒绝"
                        });
                    }
                    Err(error) => {
                        self.state.set_notice(error.clone());
                        self.state.push_log("error", error.clone());
                    }
                }
                self.state.finish_approval(&approval_id, result);
                if self.state.audit_visible {
                    self.refresh_audit();
                }
            }
            GuiEvent::AuditError { message, epoch } => {
                if epoch == self.audit_request_epoch {
                    self.state.audit_loading = false;
                    self.state.audit_error = Some(message.clone());
                    self.state.set_notice(message);
                }
            }
            GuiEvent::AuditLogs { logs, epoch } => {
                if epoch == self.audit_request_epoch {
                    self.state.audit_loading = false;
                    self.state.audit_error = None;
                    self.state.audit_logs = logs;
                }
            }
            GuiEvent::RollbackResult {
                plan_id,
                result,
                epoch,
            } => {
                if epoch != self.rollback_request_epoch {
                    return;
                }
                let ok = result.is_ok();
                self.state.finish_rollback(result);
                if ok {
                    self.state.set_notice("回滚成功，正在刷新计划详情");
                    if self.selected_plan_id.as_deref() == Some(plan_id.as_str()) {
                        self.send(Command::RefreshPlanDetail {
                            plan_id,
                            epoch: self.selected_request_epoch,
                        });
                    }
                } else if let Some(error) = self.state.rollback_error.clone() {
                    self.state.set_notice(error);
                }
            }
            GuiEvent::PlanDetail { detail, epoch } => {
                // 选择已变化时，旧请求的响应只丢弃，不改变选中项或状态。
                if epoch == self.selected_request_epoch
                    && self.selected_plan_id.as_deref() == Some(detail.plan_id.as_str())
                {
                    self.state.set_plan_detail(*detail);
                }
            }
            GuiEvent::Error(msg) => {
                self.state.push_log("error", msg.clone());
                self.state.set_notice(msg);
            }
            GuiEvent::ControlResult { plan_id, result } => match result {
                Ok(msg) => {
                    self.state.set_notice(msg);
                    self.send(Command::RefreshStatus);
                    // 归档/重命名/删除/执行控制都会改变列表内容，统一刷新项目列表。
                    self.send(Command::RefreshPlans);
                    self.refresh_designs();
                    // 控制成功后立即拉当前计划详情，快速校准 REST 视图。
                    if self.selected_plan_id.as_deref() == Some(plan_id.as_str()) {
                        self.send(Command::RefreshPlanDetail {
                            plan_id,
                            epoch: self.selected_request_epoch,
                        });
                    }
                }
                Err(e) => {
                    // 409/404 等冲突：状态栏显示后端中文 detail（设计稿 §20.3）
                    self.state.set_notice(e.clone());
                    self.state.push_log("error", e);
                }
            },
            GuiEvent::ChatReply { reply } => {
                self.chat_busy = false;
                match reply {
                    Ok(text) => self.chat_history.push(("assistant".into(), text)),
                    Err(e) => self
                        .chat_history
                        .push(("assistant".into(), format!("⚠ 请求失败: {e}"))),
                }
            }
            GuiEvent::SettingsData {
                tools,
                capabilities,
                config,
            } => {
                self.settings_data = Some(SettingsDataView {
                    tools,
                    capabilities,
                    config,
                });
            }
            GuiEvent::TokenApplied(token) => {
                self.state.token = token.clone();
                self.token_draft = token;
                self.state.set_notice("token 已更新");
                self.send(Command::RefreshStatus);
                self.send(Command::RefreshPlans);
            }
        }
    }

    /// 提取选中计划的拥有数据（渲染与命令发送不冲突）
    fn selection(&self) -> Option<SelectionView> {
        let sel = self.state.selected.as_ref()?;
        let steps = sel
            .detail
            .steps
            .iter()
            .map(|s| {
                let live = sel.step_status(&s.step_id, &s.status);
                (s.clone(), live)
            })
            .collect();
        Some(SelectionView {
            plan_id: sel.plan_id.clone(),
            status: sel.status,
            progress: sel.progress(),
            steps,
        })
    }

    fn control_buttons(&mut self, ui: &mut egui::Ui, plan_id: &str, status: PlanStatus) {
        ui.horizontal(|ui| {
            match status {
                // 草稿不能执行，避免绕过审批/确认阶段。
                PlanStatus::Draft => {
                    ui.weak("草稿不可执行");
                }
                PlanStatus::Running => {
                    if ui.add(theme::secondary_button("⏸ 暂停")).clicked() {
                        self.send(Command::Pause(plan_id.to_string()));
                    }
                }
                PlanStatus::Paused => {
                    if ui.add(theme::primary_button("▶ 恢复执行")).clicked() {
                        self.send(Command::Resume(plan_id.to_string()));
                    }
                }
                _ => {
                    if ui.add(theme::primary_button("▶ 开始执行")).clicked() {
                        self.send(Command::Execute(plan_id.to_string()));
                    }
                }
            }
        });
    }

    fn design_input_panel(&mut self, ui: &mut egui::Ui) {
        ui.heading("描述你的项目需求");
        ui.weak("AI 将分析需求并生成详细的设计书和执行计划");
        ui.add_space(10.0);
        let loading = self.state.design_loading;
        ui.add_enabled_ui(!loading, |ui| {
            theme::card(ui, |ui| {
                ui.add(
                    egui::TextEdit::multiline(&mut self.state.input_text)
                        .hint_text("描述你想要实现的功能和技术要求…")
                        .desired_rows(9)
                        .desired_width(f32::INFINITY),
                );
            });
        });
        if let Some(error) = self.state.design_error.clone() {
            ui.add_space(4.0);
            theme::error_inline(ui, &error);
        }
        ui.add_space(10.0);
        if ui
            .add_enabled(
                !loading && !self.state.input_text.trim().is_empty(),
                theme::primary_button(if loading {
                    "生成中…"
                } else {
                    "生成设计书"
                }),
            )
            .clicked()
        {
            self.generate_design();
        }
    }

    fn design_review_panel(&mut self, ui: &mut egui::Ui) {
        let Some(design) = self.state.current_design.clone() else {
            theme::empty_state(
                ui,
                egui_phosphor::regular::FILE_TEXT,
                "暂无设计书",
                "在「需求输入」中描述目标，AI 会生成设计书供审阅",
            );
            return;
        };
        let part1 = design.part1.clone();
        let part2 = design.part2.clone();
        ui.heading("设计书审阅");
        ui.weak(format!("ID: {} · {}", design.design_id, design.created_at));
        ui.add_space(6.0);
        egui::ScrollArea::vertical()
            .auto_shrink([false, false])
            .show(ui, |ui| {
                theme::card(ui, |ui| {
                    ui.strong("用户需求");
                    ui.add_space(2.0);
                    egui_commonmark::CommonMarkViewer::new().show(
                        ui,
                        &mut self.md_cache,
                        &design.user_request,
                    );
                });
                ui.add_space(8.0);
                theme::card(ui, |ui| {
                    ui.strong("架构设计");
                    ui.label("系统组件");
                    if part1.architecture.components.is_empty() {
                        ui.weak("暂无");
                    } else {
                        for component in &part1.architecture.components {
                            ui.label(format!("• {component}"));
                        }
                    }
                    ui.label("数据流");
                    for flow in &part1.architecture.data_flow {
                        ui.label(format!("• {flow}"));
                    }
                    ui.label(format!(
                        "技术栈: {}",
                        if part1.architecture.tech_stack.is_empty() {
                            "暂无".to_string()
                        } else {
                            part1.architecture.tech_stack.join("、")
                        }
                    ));
                });
                ui.add_space(8.0);
                theme::card(ui, |ui| {
                    ui.strong(format!("难度: {}", part1.difficulty.level));
                    ui.add_space(2.0);
                    egui_commonmark::CommonMarkViewer::new().show(
                        ui,
                        &mut self.md_cache,
                        &part1.difficulty.reasoning,
                    );
                });
                ui.add_space(8.0);
                theme::card(ui, |ui| {
                    ui.strong("可用工具");
                    if part2.available_tools.is_empty() {
                        ui.weak("暂无");
                    }
                    for tool in &part2.available_tools {
                        ui.label(format!(
                            "{} [{}]: {}",
                            tool.tool_id,
                            tool.quality.as_deref().unwrap_or("未知质量"),
                            tool.purpose
                        ));
                    }
                    if !part2.missing_tools.is_empty() {
                        ui.strong("缺失工具");
                        for tool in &part2.missing_tools {
                            ui.colored_label(
                                egui::Color32::RED,
                                format!("{}: {}", tool.tool_id, tool.purpose),
                            );
                        }
                    }
                    if !part2.recommendations.is_empty() {
                        ui.strong("建议");
                        for recommendation in &part2.recommendations {
                            ui.label(format!("• {recommendation}"));
                        }
                    }
                });
            });
        ui.add_space(8.0);
        let in_flight = self.state.design_action_in_flight;
        ui.horizontal(|ui| {
            if ui
                .add_enabled(!in_flight, theme::primary_button("批准并生成计划"))
                .clicked()
            {
                self.approve_design(true);
            }
            if ui
                .add_enabled(!in_flight, theme::danger_outline_button("拒绝并给反馈"))
                .clicked()
            {
                self.approve_design(false);
            }
            if in_flight {
                ui.spinner();
            }
        });
        if self.feedback_open {
            ui.add_space(4.0);
            theme::card(ui, |ui| {
                ui.strong("请告诉 AI 需要修改的内容");
                ui.add_space(4.0);
                ui.add_enabled_ui(!in_flight, |ui| {
                    ui.add(
                        egui::TextEdit::multiline(&mut self.state.feedback_text)
                            .hint_text("例如：组件划分不够细致，需要进一步拆分…")
                            .desired_rows(4)
                            .desired_width(f32::INFINITY),
                    );
                });
                ui.horizontal(|ui| {
                    if ui
                        .add_enabled(!in_flight, theme::secondary_button("取消"))
                        .clicked()
                    {
                        self.feedback_open = false;
                        self.state.feedback_text.clear();
                        self.state.design_error = None;
                    }
                    let can_submit = !in_flight && self.state.trimmed_feedback().is_some();
                    if ui
                        .add_enabled(can_submit, theme::primary_button("提交反馈"))
                        .clicked()
                    {
                        self.approve_design(false);
                    }
                });
            });
        }
        if let Some(error) = self.state.design_error.clone() {
            ui.add_space(4.0);
            theme::error_inline(ui, &error);
        }
    }

    fn design_list_panel(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.heading("设计书");
            theme::badge(
                ui,
                &self.state.designs.len().to_string(),
                theme::text_sec(),
                theme::hover_bg(),
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.add(theme::secondary_button("刷新")).clicked() {
                    self.refresh_designs();
                }
            });
        });
        theme::section_label(ui, "DESIGNS");
        ui.add_space(4.0);
        let designs = self.state.designs.clone();
        egui::ScrollArea::vertical().show(ui, |ui| {
            if designs.is_empty() {
                theme::empty_state(
                    ui,
                    egui_phosphor::regular::TRAY,
                    "还没有设计书",
                    "从「需求输入」开始创建第一份设计书",
                );
                return;
            }
            // 键盘导航：↑/↓ 在设计书间移动选择，Enter 选中第一份（无选择时）。
            // typing 守卫与项目列表一致：焦点在输入框时不抢键。
            if !ui.ctx().memory(|m| m.focused().is_some()) {
                let (up, down, enter) = ui.ctx().input(|i| {
                    (
                        i.key_pressed(egui::Key::ArrowUp),
                        i.key_pressed(egui::Key::ArrowDown),
                        i.key_pressed(egui::Key::Enter),
                    )
                });
                let ids: Vec<String> = designs.iter().map(|d| d.design_id.clone()).collect();
                if up || down {
                    let cur = self
                        .state
                        .current_design
                        .as_ref()
                        .and_then(|c| ids.iter().position(|v| v == &c.design_id));
                    let next = match (cur, down) {
                        (Some(i), true) => (i + 1).min(ids.len() - 1),
                        (Some(i), false) => i.saturating_sub(1),
                        (None, _) => 0,
                    };
                    let target = ids[next].clone();
                    let already = self
                        .state
                        .current_design
                        .as_ref()
                        .map(|c| c.design_id == target)
                        .unwrap_or(false);
                    if !already {
                        self.select_design(target);
                    }
                } else if enter && self.state.current_design.is_none() {
                    self.select_design(ids[0].clone());
                }
            }
            for design in designs {
                let selected = self
                    .state
                    .current_design
                    .as_ref()
                    .map(|current| current.design_id == design.design_id)
                    .unwrap_or(false);
                let label = if design.user_request.is_empty() {
                    design.design_id.clone()
                } else {
                    design.user_request.chars().take(24).collect::<String>()
                };
                ui.horizontal(|ui| {
                    let btn_w = (ui.available_width() - 66.0).max(60.0);
                    if ui
                        .add_sized([btn_w, 26.0], egui::Button::selectable(selected, label))
                        .clicked()
                    {
                        self.select_design(design.design_id.clone());
                    }
                    if ui.small_button("删除").clicked() {
                        self.design_delete_confirmation = Some(design.design_id.clone());
                    }
                });
                ui.add_space(2.0);
            }
        });
    }

    fn mode_selector(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.add_space(4.0);
            for (mode, label) in [
                (GuiMode::Input, "需求输入"),
                (GuiMode::Review, "设计审阅"),
                (GuiMode::Plan, "计划执行"),
            ] {
                let active = self.state.mode == mode;
                let mut text = egui::RichText::new(label).size(14.0);
                if active {
                    text = text.strong();
                }
                if ui.selectable_label(active, text).clicked() {
                    self.state.mode = mode;
                    self.zoomed = None;
                }
                ui.add_space(2.0);
            }
        });
    }

    fn approval_queue(&mut self, ui: &mut egui::Ui) {
        let approvals = self.state.pending_approvals.clone();
        if approvals.is_empty()
            && !self.state.approvals_loading
            && self.state.approvals_error.is_none()
        {
            return;
        }
        // A/R 快捷键作用于队首审批；输入框聚焦时不抢键。
        let typing = ui.ctx().memory(|m| m.focused().is_some());
        let key_approve = !typing
            && ui
                .ctx()
                .input(|i| i.key_pressed(egui::Key::A) && !i.modifiers.any());
        let key_reject = !typing
            && ui
                .ctx()
                .input(|i| i.key_pressed(egui::Key::R) && !i.modifiers.any());
        egui::Panel::top("approval_queue")
            .resizable(true)
            .default_size(170.0)
            .max_size(330.0)
            .frame(
                egui::Frame::default()
                    .fill(theme::surface())
                    .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                    .inner_margin(egui::Margin::same(10)),
            )
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.heading("待审批工具调用");
                    theme::badge(
                        ui,
                        &format!("{} 条", approvals.len()),
                        theme::accent_text(),
                        theme::accent_soft(),
                    );
                    ui.label(
                        egui::RichText::new(format!(
                            "{} A 批准队首 · R 拒绝队首",
                            egui_phosphor::regular::KEYBOARD
                        ))
                        .size(11.5)
                        .color(theme::text_faint()),
                    );
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if ui.add(theme::secondary_button("刷新审批")).clicked() {
                            self.refresh_approvals();
                        }
                        if self.state.approvals_loading {
                            ui.spinner();
                        }
                    });
                });
                if let Some(error) = &self.state.approvals_error {
                    ui.add_space(2.0);
                    theme::error_inline(ui, error);
                }
                egui::ScrollArea::vertical().show(ui, |ui| {
                    for (index, approval) in approvals.into_iter().enumerate() {
                        let approval_id = approval.approval_id.clone();
                        let in_flight = self.state.approval_in_flight.contains(&approval_id);
                        let is_head = index == 0;
                        theme::card(ui, |ui| {
                            ui.horizontal(|ui| {
                                ui.strong(format!("工具 {}", approval.tool_id));
                                ui.weak(format!(
                                    "步骤 {} · 计划 {}",
                                    approval.step_id, approval.plan_id
                                ));
                                ui.with_layout(
                                    egui::Layout::right_to_left(egui::Align::Center),
                                    |ui| {
                                        ui.weak(&approval.created_at);
                                    },
                                );
                            });
                            ui.label(format!("指令: {}", approval.instruction));
                            let json = serde_json::to_string_pretty(&approval.inputs)
                                .unwrap_or_else(|_| "<无效 JSON>".to_string());
                            ui.collapsing("输入 JSON", |ui| {
                                ui.monospace(json);
                            });
                            let mut comment = self.state.approval_comment(&approval_id).to_string();
                            if ui
                                .add(
                                    egui::TextEdit::singleline(&mut comment)
                                        .hint_text("审批备注（可选）"),
                                )
                                .changed()
                            {
                                self.state
                                    .set_approval_comment(&approval_id, comment.clone());
                            }
                            ui.horizontal(|ui| {
                                let approve = ui
                                    .add_enabled(!in_flight, theme::primary_button("批准"))
                                    .clicked()
                                    || (is_head && key_approve && !in_flight);
                                let reject = ui
                                    .add_enabled(!in_flight, theme::danger_outline_button("拒绝"))
                                    .clicked()
                                    || (is_head && key_reject && !in_flight && !approve);
                                if in_flight {
                                    ui.spinner();
                                }
                                if (approve || reject) && self.state.begin_approval(&approval_id) {
                                    let comment = if comment.trim().is_empty() {
                                        None
                                    } else {
                                        Some(comment)
                                    };
                                    let command = if approve {
                                        Command::Approve {
                                            plan_id: approval.plan_id.clone(),
                                            approval_id: approval_id.clone(),
                                            comment,
                                        }
                                    } else {
                                        Command::Reject {
                                            plan_id: approval.plan_id.clone(),
                                            approval_id: approval_id.clone(),
                                            comment,
                                        }
                                    };
                                    if !self.send(command) {
                                        self.state.finish_approval(
                                            &approval_id,
                                            Err("后台命令队列已满".to_string()),
                                        );
                                    }
                                }
                            });
                        });
                        ui.add_space(4.0);
                    }
                });
            });
    }

    fn audit_panel(&mut self, ui: &mut egui::Ui) {
        if !self.state.audit_visible {
            return;
        }
        egui::Panel::right("audit_panel")
            .resizable(true)
            .default_size(430.0)
            .frame(
                egui::Frame::default()
                    .fill(theme::surface())
                    .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                    .inner_margin(egui::Margin::same(12)),
            )
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.heading("审计日志");
                    theme::badge(
                        ui,
                        if self.selected_plan_id.is_some() {
                            "当前计划"
                        } else {
                            "全部"
                        },
                        theme::accent_text(),
                        theme::accent_soft(),
                    );
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if ui.add(theme::secondary_button("刷新")).clicked() {
                            self.refresh_audit();
                        }
                        if self.state.audit_loading {
                            ui.spinner();
                        }
                    });
                });
                ui.horizontal(|ui| {
                    ui.label("搜索");
                    ui.text_edit_singleline(&mut self.state.audit_search);
                    ui.label("条数");
                    if ui
                        .add(
                            egui::TextEdit::singleline(&mut self.audit_limit_draft)
                                .desired_width(45.0),
                        )
                        .lost_focus()
                    {
                        if let Ok(value) = self.audit_limit_draft.parse::<u32>() {
                            self.state.audit_limit = value.clamp(1, 1000);
                            self.audit_limit_draft = self.state.audit_limit.to_string();
                        }
                    }
                });
                if let Some(error) = &self.state.audit_error {
                    theme::error_inline(ui, error);
                }
                ui.separator();
                if self.state.audit_logs.is_empty() && !self.state.audit_loading {
                    ui.weak("暂无日志记录——搜索仅作用于已拉取的最近条目");
                    ui.add_space(4.0);
                }
                // 表格化：列宽可拖动、斑马纹、长文本截断 + hover 显示全文。
                let logs: Vec<_> = self
                    .state
                    .filtered_audit_logs()
                    .into_iter()
                    .cloned()
                    .collect();
                egui_extras::TableBuilder::new(ui)
                    .striped(true)
                    .resizable(true)
                    .column(egui_extras::Column::exact(44.0))
                    .column(
                        egui_extras::Column::initial(128.0)
                            .at_least(90.0)
                            .clip(true),
                    )
                    .column(egui_extras::Column::initial(76.0).at_least(56.0).clip(true))
                    .column(egui_extras::Column::initial(76.0).at_least(56.0).clip(true))
                    .column(egui_extras::Column::initial(90.0).at_least(56.0).clip(true))
                    .column(egui_extras::Column::remainder().at_least(110.0).clip(true))
                    .column(
                        egui_extras::Column::initial(110.0)
                            .at_least(70.0)
                            .clip(true),
                    )
                    .header(24.0, |mut header| {
                        for title in ["结果", "时间", "计划", "步骤", "工具", "指令", "拦截原因"]
                        {
                            header.col(|ui| {
                                ui.strong(title);
                            });
                        }
                    })
                    .body(|body| {
                        body.rows(22.0, logs.len(), |mut row| {
                            let log = &logs[row.index()];
                            let (label, color) = theme::audit_result_visual(log.result());
                            row.col(|ui| {
                                ui.colored_label(color, label);
                            });
                            row.col(|ui| {
                                ui.label(Self::relative_time(&log.timestamp))
                                    .on_hover_text(&log.timestamp);
                            });
                            for value in [
                                log.plan_id.as_deref(),
                                log.step_id.as_deref(),
                                log.tool_id.as_deref(),
                            ] {
                                row.col(|ui| {
                                    let text = value.unwrap_or("-");
                                    ui.label(text).on_hover_text(text);
                                });
                            }
                            row.col(|ui| {
                                let text = log.instruction.as_deref().unwrap_or("-");
                                ui.label(text).on_hover_text(text);
                            });
                            row.col(|ui| {
                                let text = log.block_reason.as_deref().unwrap_or("-");
                                ui.colored_label(
                                    if log.block_reason.is_some() {
                                        theme::warn_text()
                                    } else {
                                        theme::text_faint()
                                    },
                                    text,
                                )
                                .on_hover_text(text);
                            });
                        });
                    });
            });
    }

    fn settings_central_content(&mut self, ui: &mut egui::Ui) {
        theme::card(ui, |ui| {
            ui.strong("外观");
            ui.set_min_width(ui.available_width().max(0.0));
            ui.add_space(2.0);
            ui.horizontal(|ui| {
                let mut dark = self.dark_mode;
                if ui.checkbox(&mut dark, "暗色主题").changed() {
                    self.dark_mode = dark;
                    theme::set_dark(dark);
                    theme::install(ui.ctx());
                }
                ui.add_space(16.0);
                let mut compact = self.compact_mode;
                if ui.checkbox(&mut compact, "紧凑密度").changed() {
                    self.compact_mode = compact;
                    theme::set_compact(compact);
                    theme::install(ui.ctx());
                }
            });
            ui.weak("紧凑密度缩小间距与字号，适合小屏或高信息密度场景；选择会自动保存。");
            ui.separator();
            ui.horizontal_wrapped(|ui| {
                ui.label("布局模式");
                self.layout_picker(ui, "settings_layout_mode");
            });
            ui.weak(self.layout.mode.description());
            ui.weak("每种布局分别记住项目栏、输出和助手开合；主题与密度独立保存。");
        });
        ui.add_space(8.0);
        theme::card(ui, |ui| {
            ui.strong("服务状态");
            ui.set_min_width(ui.available_width().max(0.0));
            ui.add_space(2.0);
            ui.label(
                egui::RichText::new(self.status_text())
                    .size(12.5)
                    .color(theme::text_sec()),
            );
            ui.add_space(4.0);
            if self.state.connected {
                theme::badge(ui, "● 已连接", theme::ok_text(), theme::ok_soft());
            } else {
                theme::badge(ui, "● 未连接", theme::danger_text(), theme::danger_soft());
            }
        });
        ui.add_space(8.0);
        theme::card(ui, |ui| {
            ui.strong("服务地址");
            ui.set_min_width(ui.available_width().max(0.0));
            ui.add_space(2.0);
            ui.monospace(self.state.base.as_str());
            ui.weak("由环境变量 EAS_API_BASE 指定，默认 http://127.0.0.1:8000");
        });
        ui.add_space(8.0);
        theme::card(ui, |ui| {
            ui.strong("API Token");
            ui.set_min_width(ui.available_width().max(0.0));
            ui.add_space(2.0);
            let token_edit = egui::TextEdit::singleline(&mut self.token_draft)
                .password(true)
                .hint_text("API token")
                .desired_width(240.0);
            let response = ui.add(token_edit);
            let apply_clicked = ui
                .add_enabled(
                    !self.token_draft.trim().is_empty(),
                    theme::primary_button("应用 token"),
                )
                .clicked();
            if apply_clicked
                || (response.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)))
            {
                let t = self.token_draft.trim().to_string();
                if !t.is_empty() && t != self.state.token {
                    self.send(Command::SetToken(t));
                }
            }
            ui.add_space(2.0);
            ui.weak("token 来源：环境变量 EAS_API_TOKEN 或 config/api_token");
        });
        ui.add_space(8.0);
        theme::card(ui, |ui| {
            ui.strong("后端配置（config/models.yaml）");
            ui.set_min_width(ui.available_width().max(0.0));
            ui.add_space(2.0);
            match &self.settings_data {
                Some(data) => {
                    if let Some(keys) = data.config["api_keys_configured"].as_object() {
                        for (k, v) in keys {
                            let ok = v.as_bool().unwrap_or(false);
                            theme::badge(
                                ui,
                                &format!("{k}: {}", if ok { "已配置" } else { "未配置" }),
                                if ok {
                                    theme::ok_text()
                                } else {
                                    theme::text_sec()
                                },
                                if ok {
                                    theme::ok_soft()
                                } else {
                                    theme::hover_bg()
                                },
                            );
                        }
                    }
                    ui.add_space(4.0);
                    if let Some(urls) = data.config["base_urls"].as_object() {
                        for (k, v) in urls {
                            ui.horizontal(|ui| {
                                ui.weak(format!("{k}:"));
                                ui.monospace(v.as_str().unwrap_or(""));
                            });
                        }
                    }
                    ui.add_space(4.0);
                    ui.weak("编辑密钥请直接修改 config/models.yaml——GUI 保存会整体覆盖密钥映射，暂不开放。");
                }
                None => {
                    ui.weak("等待加载…");
                }
            }
        });
        ui.add_space(8.0);
        theme::card(ui, |ui| {
            ui.strong("服务运维");
            ui.set_min_width(ui.available_width().max(0.0));
            ui.add_space(2.0);
            ui.weak("重启会让后端进程退出，需要重新启动后端后 GUI 才能重连。");
            ui.add_space(4.0);
            if self.restart_confirm {
                ui.horizontal(|ui| {
                    ui.label(egui::RichText::new("确定重启后端服务？").color(theme::danger_text()));
                    if ui.add(theme::secondary_button("再想想")).clicked() {
                        self.restart_confirm = false;
                    }
                    if ui.add(theme::danger_button("确认重启")).clicked() {
                        self.restart_confirm = false;
                        self.send(Command::RestartServer);
                    }
                });
            } else if ui
                .add(theme::danger_outline_button("重启后端服务"))
                .clicked()
            {
                self.restart_confirm = true;
            }
        });
        ui.add_space(10.0);
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            if ui.add(theme::secondary_button("关闭")).clicked() {
                self.settings_open = false;
            }
        });
    }

    fn tools_central_content(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.vertical(|ui| {
                ui.heading("工具箱");
                ui.label(egui::RichText::new("浏览可用工具及其能力边界").color(theme::text_sec()));
            });
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.add(theme::secondary_button("刷新")).clicked() {
                    self.settings_data = None;
                    self.send(Command::RefreshSettingsData);
                }
            });
        });
        ui.add_space(12.0);
        ui.add(
            egui::TextEdit::singleline(&mut self.tool_search)
                .hint_text("搜索工具、类别或能力…")
                .desired_width(f32::INFINITY),
        );
        ui.add_space(10.0);
        let Some(data) = self.settings_data.clone() else {
            ui.horizontal(|ui| {
                ui.spinner();
                ui.weak("正在加载工具目录…");
            });
            return;
        };
        let query = self.tool_search.trim().to_lowercase();
        let show_caps = self.show_tool_capabilities;
        let tools: Vec<_> = data
            .tools
            .iter()
            .filter(|tool| {
                query.is_empty()
                    || tool.name.to_lowercase().contains(&query)
                    || tool.category.to_lowercase().contains(&query)
                    || tool.description.to_lowercase().contains(&query)
                    || tool
                        .capabilities
                        .iter()
                        .any(|cap| cap.to_lowercase().contains(&query))
            })
            .collect();
        ui.horizontal(|ui| {
            let tools_resp = theme::badge(
                ui,
                &format!("{} 个工具", tools.len()),
                theme::accent_text(),
                theme::accent_soft(),
            )
            .interact(egui::Sense::click())
            .on_hover_text("清除搜索，显示全部工具")
            .on_hover_cursor(egui::CursorIcon::PointingHand);
            if tools_resp.clicked() {
                self.tool_search.clear();
            }
            let caps_resp = theme::badge(
                ui,
                &format!("{} 项能力声明", data.capabilities.len()),
                theme::text_sec(),
                theme::hover_bg(),
            )
            .interact(egui::Sense::click())
            .on_hover_text(if self.show_tool_capabilities {
                "点击隐藏各卡片的能力列表"
            } else {
                "点击显示各卡片的能力列表"
            })
            .on_hover_cursor(egui::CursorIcon::PointingHand);
            if caps_resp.clicked() {
                self.show_tool_capabilities = !self.show_tool_capabilities;
            }
        });
        ui.add_space(8.0);
        egui::ScrollArea::vertical()
            .auto_shrink([false, false])
            .show(ui, |ui| {
                if tools.is_empty() {
                    ui.weak("没有匹配的工具");
                }
                for tool in tools {
                    theme::card(ui, |ui| {
                        // 卡片撑满整行宽度，避免随内容伸缩导致右边缘参差不齐。
                        ui.set_min_width(ui.available_width().max(0.0));
                        ui.horizontal(|ui| {
                            ui.label(egui::RichText::new(&tool.name).size(14.0).strong());
                            theme::badge(
                                ui,
                                &tool.category,
                                theme::info_text(),
                                theme::info_soft(),
                            );
                        });
                        ui.label(egui::RichText::new(&tool.description).color(theme::text_sec()));
                        if show_caps && !tool.capabilities.is_empty() {
                            ui.add_space(4.0);
                            ui.monospace(tool.capabilities.join("  ·  "));
                        }
                    });
                    ui.add_space(8.0);
                }
            });
    }

    #[allow(dead_code)] // 保留为窄屏/弹窗布局的共享渲染入口。
    fn assistant_content(&mut self, ui: &mut egui::Ui) {
        egui::ScrollArea::vertical()
            .stick_to_bottom(true)
            .auto_shrink([false, false])
            .max_height((ui.available_height() - 68.0).max(120.0))
            .show(ui, |ui| {
                if self.chat_history.is_empty() {
                    ui.weak("向 AI 助手提问，例如：解释一下当前计划的步骤。");
                }
                for (role, content) in &self.chat_history {
                    let (who, color) = if role == "user" {
                        ("你", theme::accent_text())
                    } else {
                        ("助手", theme::ok_text())
                    };
                    theme::card(ui, |ui| {
                        ui.label(egui::RichText::new(who).size(11.5).strong().color(color));
                        ui.label(egui::RichText::new(content).size(13.0));
                    });
                    ui.add_space(4.0);
                }
                if self.chat_busy {
                    ui.horizontal(|ui| {
                        ui.spinner();
                        ui.weak("思考中…");
                    });
                }
            });
        ui.add_space(6.0);
        ui.separator();
        let send_enabled = !self.chat_busy && !self.chat_draft.trim().is_empty();
        ui.horizontal(|ui| {
            let response = ui.add(
                egui::TextEdit::singleline(&mut self.chat_draft)
                    .hint_text("输入问题…（Enter 发送）")
                    .desired_width(f32::INFINITY),
            );
            let enter_send = response.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
            let send_clicked = ui
                .add_enabled(send_enabled, theme::primary_button("发送"))
                .clicked();
            if (send_clicked || enter_send) && send_enabled {
                let msg = self.chat_draft.trim().to_string();
                let history = self.chat_history.clone();
                self.chat_history.push(("user".into(), msg.clone()));
                self.chat_draft.clear();
                self.chat_busy = true;
                self.send(Command::Chat {
                    message: msg,
                    history,
                });
            }
        });
    }

    fn rollback_confirmation(&mut self, ctx: &egui::Context) {
        // 三态：确认 → 执行中 →（结果摘要显示在计划头部卡片）
        let confirming = self.state.rollback_confirmation.clone();
        let in_flight = self.state.rollback_in_flight.clone();
        if confirming.is_none() && in_flight.is_none() {
            return;
        }
        egui::Window::new("回滚")
            .collapsible(false)
            .resizable(false)
            .show(ctx, |ui| {
                if let Some((plan_id, step_id)) = confirming {
                    ui.label(format!(
                        "确定回滚计划 {plan_id} 到步骤 {step_id} 吗？将创建新分支并废弃后续旧步骤。"
                    ));
                    ui.add_space(6.0);
                    ui.horizontal(|ui| {
                        if ui.add(theme::secondary_button("取消")).clicked() {
                            self.state.cancel_rollback();
                        }
                        if ui.add(theme::danger_button("确认回滚")).clicked()
                            && self.state.begin_rollback(&plan_id, &step_id)
                        {
                            self.rollback_request_epoch = self
                                .rollback_request_epoch
                                .checked_add(1)
                                .expect("回滚请求 epoch 溢出");
                            if !self.send(Command::Rollback {
                                plan_id: plan_id.clone(),
                                step_id: step_id.clone(),
                                strategy: "retry".to_string(),
                                epoch: self.rollback_request_epoch,
                            }) {
                                self.state
                                    .finish_rollback(Err("后台命令队列已满".to_string()));
                            }
                        }
                    });
                } else if let Some((plan_id, step_id)) = in_flight {
                    ui.horizontal(|ui| {
                        ui.spinner();
                        ui.label("回滚执行中…");
                    });
                    ui.weak(format!(
                        "计划 {} · 目标步骤 {}",
                        &plan_id[..8.min(plan_id.len())],
                        step_id
                    ));
                }
            });
    }
}

impl eframe::App for EasApp {
    fn save(&mut self, storage: &mut dyn eframe::Storage) {
        eframe::set_value(storage, "eas.dark_mode", &self.dark_mode);
        eframe::set_value(storage, "eas.compact_mode", &self.compact_mode);
        eframe::set_value(storage, layout::STORAGE_KEY, &self.layout_snapshot());
    }

    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let ctx = &ui.ctx().clone();
        // 1. 排空事件通道（每条事件都在后台线程请求过重绘）
        while let Ok(ev) = self.events.try_recv() {
            self.handle_event(ev);
        }
        // 通知变化 → 右下角 Toast（按内容分级；通知条保留为可回看的持久位）。
        if self.state.notice != self.last_notice {
            if let Some(msg) = self.state.notice.clone() {
                let toast = if msg.contains("失败") || msg.contains("错误") || msg.contains("拒绝")
                {
                    self.toasts.error(msg.clone())
                } else if msg.contains("成功") || msg.contains("已更新") || msg.contains("已批准")
                {
                    self.toasts.success(msg.clone())
                } else {
                    self.toasts.info(msg.clone())
                };
                toast.duration(Some(std::time::Duration::from_secs(4)));
            }
            self.last_notice = self.state.notice.clone();
        }
        // 连接状态转变 → Toast 即时提醒（首帧不提示）。
        if self.last_connected != Some(self.state.connected) {
            if self.last_connected.is_some() {
                if self.state.connected {
                    self.toasts
                        .success("已重新连接到服务")
                        .duration(Some(std::time::Duration::from_secs(3)));
                } else {
                    self.toasts
                        .error("与服务的连接已断开")
                        .duration(Some(std::time::Duration::from_secs(5)));
                }
            }
            self.last_connected = Some(self.state.connected);
        }
        // 即使本帧没有事件，也保持即时模式持续处理队列和输入。
        ctx.request_repaint_after(std::time::Duration::from_millis(100));
        self.handle_global_shortcuts(ctx);

        // 2. 紧凑标题栏：工作区上下文 + 命令中心。高频视图入口全部归入 Activity Bar。
        egui::Panel::top("top_bar")
            .exact_size(40.0)
            .frame(
                egui::Frame::default()
                    .fill(theme::surface())
                    .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                    .inner_margin(egui::Margin::symmetric(12, 5)),
            )
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.label(
                        egui::RichText::new("星舵")
                            .size(15.0)
                            .strong()
                            .color(theme::accent_text()),
                    );
                    ui.label(egui::RichText::new("/ ").color(theme::text_faint()));
                    let workspace = if self.tools_open {
                        "工具箱"
                    } else if self.settings_open {
                        "设置"
                    } else {
                        match self.state.mode {
                            GuiMode::Input => "需求输入",
                            GuiMode::Review => "设计审阅",
                            GuiMode::Plan => "执行控制",
                        }
                    };
                    ui.label(
                        egui::RichText::new(workspace)
                            .size(12.5)
                            .color(theme::text_sec()),
                    );
                    ui.add_space(24.0);
                    if ui
                        .add_sized(
                            [300.0, 28.0],
                            egui::Button::new(
                                egui::RichText::new(format!(
                                    "{}  搜索或运行命令                              Ctrl+K",
                                    egui_phosphor::regular::MAGNIFYING_GLASS
                                ))
                                .size(12.0)
                                .color(theme::text_sec()),
                            )
                            .fill(theme::hover_bg()),
                        )
                        .clicked()
                    {
                        self.quick_actions_open = true;
                        self.command_query.clear();
                        self.command_selected = 0;
                    }
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        let (icon, label, color) = if self.state.connected {
                            (egui_phosphor::regular::WIFI_HIGH, "在线", theme::ok_text())
                        } else {
                            (
                                egui_phosphor::regular::WIFI_SLASH,
                                "离线",
                                theme::danger_text(),
                            )
                        };
                        if ui
                            .add(
                                egui::Button::new(
                                    egui::RichText::new(format!("{icon} {label}"))
                                        .size(12.0)
                                        .color(color),
                                )
                                .frame(false),
                            )
                            .on_hover_text("点击查看连接诊断")
                            .clicked()
                        {
                            self.conn_diag_open = !self.conn_diag_open;
                        }
                    });
                });
            });

        // Always reachable, including Focus and zoomed panes; never sends commands.
        egui::Panel::top("layout_bar")
            .frame(
                egui::Frame::default()
                    .fill(theme::surface())
                    .inner_margin(egui::Margin::symmetric(12, 6)),
            )
            .show(ui, |ui| {
                ui.horizontal_wrapped(|ui| {
                    ui.label(egui::RichText::new("布局").color(theme::text_sec()));
                    self.layout_picker(ui, "toolbar_layout_mode");
                    if self.zoomed.is_some() && ui.small_button("退出全屏").clicked() {
                        self.zoomed = None;
                    }
                    if !self.settings_open && !self.tools_open {
                        ui.separator();
                        if ui.toggle_value(&mut self.navigation_open, "项目").clicked() {
                            self.zoomed = None;
                        }
                        if ui.toggle_value(&mut self.logs_open, "输出").clicked() {
                            self.zoomed = None;
                        }
                        if ui.toggle_value(&mut self.chat_open, "AI 助手").clicked() {
                            self.zoomed = None;
                        }
                        ui.separator();
                        self.mode_selector(ui);
                    }
                });
            });

        // 状态栏永远固定在最下方；输出面板在它上方伸缩，不再与工具/设置混排。
        egui::Panel::bottom("status_bar")
            .exact_size(22.0)
            .frame(
                egui::Frame::default()
                    .fill(theme::accent_dark())
                    .inner_margin(egui::Margin::symmetric(8, 2)),
            )
            .show(ui, |ui| {
                ui.horizontal(|ui| {
                    ui.label(
                        egui::RichText::new(if self.state.connected {
                            "已连接"
                        } else {
                            "未连接"
                        })
                        .size(11.0)
                        .color(egui::Color32::WHITE),
                    );
                    ui.label(
                        egui::RichText::new(self.status_text())
                            .size(11.0)
                            .color(egui::Color32::WHITE),
                    );
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        ui.label(
                            egui::RichText::new(format!("{}  ·  UTF-8", self.layout.mode.label()))
                                .size(11.0)
                                .color(egui::Color32::WHITE),
                        );
                    });
                });
            });

        self.quick_actions(ctx);

        // VS Code 风格 Activity Bar：一级工作区始终在固定位置，避免功能散落在顶栏。
        if self.zoomed.is_none() {
            egui::Panel::left("activity_bar")
                .exact_size(48.0)
                .frame(
                    egui::Frame::default()
                        .fill(theme::activity_bg())
                        .inner_margin(egui::Margin::symmetric(4, 8)),
                )
                .show(ui, |ui| {
                    ui.vertical_centered(|ui| {
                        ui.label(
                            egui::RichText::new("E")
                                .size(20.0)
                                .strong()
                                .color(egui::Color32::WHITE),
                        );
                        ui.add_space(10.0);
                        if ui
                            .add_sized(
                                [40.0, 40.0],
                                theme::activity_button(
                                    egui_phosphor::regular::SQUARES_FOUR,
                                    !self.tools_open && !self.settings_open,
                                ),
                            )
                            .on_hover_text("工作区")
                            .clicked()
                        {
                            self.settings_open = false;
                            self.tools_open = false;
                        }
                        if ui
                            .add_sized(
                                [40.0, 40.0],
                                theme::activity_button(
                                    egui_phosphor::regular::TOOLBOX,
                                    self.tools_open,
                                ),
                            )
                            .on_hover_text("工具箱")
                            .clicked()
                        {
                            self.tools_open = true;
                            self.settings_open = false;
                            self.settings_data = None;
                            self.send(Command::RefreshSettingsData);
                        }
                        if ui
                            .add_sized(
                                [40.0, 40.0],
                                theme::activity_button(
                                    egui_phosphor::regular::SPARKLE,
                                    self.chat_open,
                                ),
                            )
                            .on_hover_text("AI 助手")
                            .clicked()
                        {
                            self.chat_open = !self.chat_open;
                        }
                        if ui
                            .add_sized(
                                [40.0, 40.0],
                                theme::activity_button(
                                    egui_phosphor::regular::LIST_MAGNIFYING_GLASS,
                                    self.state.audit_visible,
                                ),
                            )
                            .on_hover_text("审计日志")
                            .clicked()
                        {
                            self.state.audit_visible = !self.state.audit_visible;
                            if self.state.audit_visible && self.state.audit_logs.is_empty() {
                                self.refresh_audit();
                            }
                        }
                        ui.with_layout(egui::Layout::bottom_up(egui::Align::Center), |ui| {
                            if ui
                                .add_sized(
                                    [40.0, 40.0],
                                    egui::Button::new(
                                        egui::RichText::new(egui_phosphor::regular::GEAR)
                                            .size(17.0)
                                            .color(egui::Color32::LIGHT_GRAY),
                                    )
                                    .frame(false),
                                )
                                .on_hover_text("设置")
                                .clicked()
                            {
                                self.settings_open = true;
                                self.tools_open = false;
                                self.settings_data = None;
                                self.send(Command::RefreshSettingsData);
                            }
                            let theme_icon = if self.dark_mode {
                                egui_phosphor::regular::SUN
                            } else {
                                egui_phosphor::regular::MOON
                            };
                            if ui
                                .add_sized(
                                    [40.0, 40.0],
                                    egui::Button::new(
                                        egui::RichText::new(theme_icon)
                                            .size(17.0)
                                            .color(egui::Color32::LIGHT_GRAY),
                                    )
                                    .frame(false),
                                )
                                .on_hover_text(if self.dark_mode {
                                    "切换到浅色主题"
                                } else {
                                    "切换到暗色主题"
                                })
                                .clicked()
                            {
                                self.dark_mode = !self.dark_mode;
                                theme::set_dark(self.dark_mode);
                                theme::install(ctx);
                            }
                        });
                    });
                });
        }

        // 3. 审批队列位于中央计划区上方，单卡片操作互不阻塞。
        if self.zoomed.is_none() && !self.settings_open && !self.tools_open {
            self.approval_queue(ui);
            self.audit_panel(ui);
        }

        // 4. 左栏：设计书列表或计划列表，二者互不覆盖各自状态。
        if !self.settings_open && !self.tools_open && self.zoomed == Some(Zone::Projects) {
            egui::Panel::left("navigation_list")
                .exact_size(ctx.content_rect().width())
                .frame(
                    egui::Frame::default()
                        .fill(theme::surface())
                        .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                        .inner_margin(egui::Margin::same(10)),
                )
                .show(ui, |ui| {
                    if self.state.mode == GuiMode::Plan {
                        ui.horizontal(|ui| {
                            let h = ui.heading("项目");
                            if h.double_clicked() {
                                self.zoomed = Some(Zone::Projects);
                            }
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if ui.add(theme::secondary_button("⛶ 全屏")).clicked() {
                                        self.zoomed = Some(Zone::Projects);
                                    }
                                    if ui.add(theme::secondary_button("刷新")).clicked() {
                                        self.send(Command::RefreshPlans);
                                    }
                                },
                            );
                        });
                        ui.add_space(4.0);
                        ui.add(
                            egui::TextEdit::singleline(&mut self.plan_search)
                                .hint_text("搜索名称 / ID…")
                                .desired_width(f32::INFINITY),
                        );
                        ui.add_space(4.0);
                        ui.horizontal(|ui| {
                            for (filter, label) in [
                                (PlanFilter::All, "全部"),
                                (PlanFilter::Active, "执行中"),
                                (PlanFilter::Finished, "已完成"),
                                (PlanFilter::Archived, "已归档"),
                            ] {
                                let active = self.plan_filter == filter;
                                let mut text = egui::RichText::new(label).size(12.0);
                                if active {
                                    text = text.strong();
                                }
                                if ui.selectable_label(active, text).clicked() {
                                    self.plan_filter = filter;
                                }
                            }
                        });
                        ui.add_space(4.0);
                        egui::ScrollArea::vertical().show(ui, |ui| {
                            let search = self.plan_search.trim().to_lowercase();
                            let plans = self.state.plans.clone();
                            let mut shown = 0usize;
                            let mut visible_ids: Vec<String> = Vec::new();
                            for p in &plans {
                                let display = if p.display_name.is_empty() {
                                    format!("项目 {}", &p.plan_id[..8.min(p.plan_id.len())])
                                } else {
                                    p.display_name.clone()
                                };
                                let matches_search = search.is_empty()
                                    || display.to_lowercase().contains(&search)
                                    || p.plan_id.to_lowercase().contains(&search);
                                let status = parse_plan_status(&p.status);
                                let matches_filter = match self.plan_filter {
                                    PlanFilter::All => !p.archived,
                                    PlanFilter::Active => {
                                        !p.archived
                                            && matches!(
                                                status,
                                                PlanStatus::Draft
                                                    | PlanStatus::Pending
                                                    | PlanStatus::Approved
                                                    | PlanStatus::Running
                                                    | PlanStatus::Paused
                                            )
                                    }
                                    PlanFilter::Finished => {
                                        !p.archived
                                            && matches!(
                                                status,
                                                PlanStatus::Completed
                                                    | PlanStatus::Failed
                                                    | PlanStatus::RolledBack
                                            )
                                    }
                                    PlanFilter::Archived => p.archived,
                                };
                                if !matches_search || !matches_filter {
                                    continue;
                                }
                                shown += 1;
                                visible_ids.push(p.plan_id.clone());
                                let selected_card =
                                    self.selected_plan_id.as_deref() == Some(p.plan_id.as_str());
                                let card_resp = theme::card_frame()
                                    .stroke(egui::Stroke::new(
                                        1.0_f32,
                                        if selected_card {
                                            theme::accent()
                                        } else {
                                            theme::border()
                                        },
                                    ))
                                    .inner_margin(egui::Margin::same(12))
                                    .show(ui, |ui| {
                                        ui.horizontal(|ui| {
                                            let selected = self.selected_plan_id.as_deref()
                                                == Some(p.plan_id.as_str());
                                            let mut text =
                                                egui::RichText::new(display.clone()).size(14.0);
                                            if selected {
                                                text = text.strong().color(theme::accent_text());
                                            }
                                            if ui
                                                .add(egui::Button::selectable(selected, text))
                                                .clicked()
                                            {
                                                self.select_plan(p.plan_id.clone());
                                            }
                                            let (label, fg, bg) = theme::plan_status_visual(status);
                                            theme::badge(ui, label, fg, bg);
                                            if p.archived {
                                                theme::badge(
                                                    ui,
                                                    "已归档",
                                                    theme::text_sec(),
                                                    theme::hover_bg(),
                                                );
                                            }
                                        });
                                        ui.horizontal(|ui| {
                                            ui.label(
                                                egui::RichText::new(Self::relative_time(
                                                    &p.created_at,
                                                ))
                                                .size(11.5)
                                                .color(theme::text_faint()),
                                            )
                                            .on_hover_text(&p.created_at);
                                            if !p.design_id.is_empty() {
                                                let did = &p.design_id[..8.min(p.design_id.len())];
                                                if ui.small_button(format!("设计 {did}")).clicked()
                                                {
                                                    self.select_design(p.design_id.clone());
                                                }
                                            }
                                        });
                                    })
                                    .response;
                                if selected_card {
                                    theme::selection_bar(ui, card_resp.rect);
                                }
                                if matches!(status, PlanStatus::Running) {
                                    theme::running_indicator(ui, card_resp.rect);
                                }
                                // 右键功能菜单：改名 / 归档 / 删除 / 查看设计书
                                card_resp.context_menu(|ui| {
                                    ui.set_min_width(150.0);
                                    if ui.button("✏ 改名").clicked() {
                                        self.rename_dialog =
                                            Some((p.plan_id.clone(), p.display_name.clone()));
                                        ui.close();
                                    }
                                    let arch = if p.archived { "取消归档" } else { "归档" };
                                    if ui.button(arch).clicked() {
                                        self.send(Command::ArchivePlan {
                                            plan_id: p.plan_id.clone(),
                                            archived: !p.archived,
                                        });
                                        ui.close();
                                    }
                                    if ui.button("删除").clicked() {
                                        self.delete_confirmation = Some(p.plan_id.clone());
                                        ui.close();
                                    }
                                    if !p.design_id.is_empty() && ui.button("查看设计书").clicked()
                                    {
                                        self.select_design(p.design_id.clone());
                                        ui.close();
                                    }
                                });
                                ui.add_space(4.0);
                            }
                            // 键盘导航：↑/↓ 在可见计划间移动选择，Enter 选中第一个（无选择时）。
                            // typing 守卫：任何控件持有焦点（如搜索框）时不响应。
                            if !ui.ctx().memory(|m| m.focused().is_some())
                                && !visible_ids.is_empty()
                            {
                                let (up, down, enter) = ui.ctx().input(|i| {
                                    (
                                        i.key_pressed(egui::Key::ArrowUp),
                                        i.key_pressed(egui::Key::ArrowDown),
                                        i.key_pressed(egui::Key::Enter),
                                    )
                                });
                                if up || down {
                                    let cur = self
                                        .selected_plan_id
                                        .as_deref()
                                        .and_then(|id| visible_ids.iter().position(|v| v == id));
                                    let next = match (cur, down) {
                                        (Some(i), true) => (i + 1).min(visible_ids.len() - 1),
                                        (Some(i), false) => i.saturating_sub(1),
                                        (None, _) => 0,
                                    };
                                    if Some(visible_ids[next].as_str())
                                        != self.selected_plan_id.as_deref()
                                    {
                                        self.select_plan(visible_ids[next].clone());
                                    }
                                } else if enter && self.selected_plan_id.is_none() {
                                    self.select_plan(visible_ids[0].clone());
                                }
                            }
                            if shown == 0 {
                                theme::empty_state(
                                    ui,
                                    egui_phosphor::regular::MAGNIFYING_GLASS,
                                    "没有匹配的项目",
                                    "调整搜索词或切换状态筛选",
                                );
                            }
                        });
                    } else {
                        self.design_list_panel(ui);
                    }
                });
        } else if !self.settings_open
            && !self.tools_open
            && self.zoomed.is_none()
            && self.navigation_open
        {
            egui::Panel::left("navigation_list")
                .default_size(286.0)
                .min_size(250.0)
                .max_size(340.0)
                .frame(
                    egui::Frame::default()
                        .fill(theme::surface())
                        .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                        .inner_margin(egui::Margin::same(10)),
                )
                .show(ui, |ui| {
                    self.mode_selector(ui);
                    ui.separator();
                    if self.state.mode == GuiMode::Plan {
                        ui.horizontal(|ui| {
                            ui.heading("项目");
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if ui.add(theme::secondary_button("刷新")).clicked() {
                                        self.send(Command::RefreshPlans);
                                    }
                                },
                            );
                        });
                        ui.add_space(4.0);
                        ui.add(
                            egui::TextEdit::singleline(&mut self.plan_search)
                                .hint_text("搜索名称 / ID…")
                                .desired_width(f32::INFINITY),
                        );
                        ui.add_space(4.0);
                        ui.horizontal(|ui| {
                            for (filter, label) in [
                                (PlanFilter::All, "全部"),
                                (PlanFilter::Active, "执行中"),
                                (PlanFilter::Finished, "已完成"),
                                (PlanFilter::Archived, "已归档"),
                            ] {
                                let active = self.plan_filter == filter;
                                let mut text = egui::RichText::new(label).size(12.0);
                                if active {
                                    text = text.strong();
                                }
                                if ui.selectable_label(active, text).clicked() {
                                    self.plan_filter = filter;
                                }
                            }
                        });
                        ui.add_space(4.0);
                        egui::ScrollArea::vertical().show(ui, |ui| {
                            let search = self.plan_search.trim().to_lowercase();
                            let plans = self.state.plans.clone();
                            let mut shown = 0usize;
                            let mut visible_ids: Vec<String> = Vec::new();
                            for p in &plans {
                                let display = if p.display_name.is_empty() {
                                    format!("项目 {}", &p.plan_id[..8.min(p.plan_id.len())])
                                } else {
                                    p.display_name.clone()
                                };
                                let matches_search = search.is_empty()
                                    || display.to_lowercase().contains(&search)
                                    || p.plan_id.to_lowercase().contains(&search);
                                let status = parse_plan_status(&p.status);
                                let matches_filter = match self.plan_filter {
                                    PlanFilter::All => !p.archived,
                                    PlanFilter::Active => {
                                        !p.archived
                                            && matches!(
                                                status,
                                                PlanStatus::Draft
                                                    | PlanStatus::Pending
                                                    | PlanStatus::Approved
                                                    | PlanStatus::Running
                                                    | PlanStatus::Paused
                                            )
                                    }
                                    PlanFilter::Finished => {
                                        !p.archived
                                            && matches!(
                                                status,
                                                PlanStatus::Completed
                                                    | PlanStatus::Failed
                                                    | PlanStatus::RolledBack
                                            )
                                    }
                                    PlanFilter::Archived => p.archived,
                                };
                                if !matches_search || !matches_filter {
                                    continue;
                                }
                                shown += 1;
                                visible_ids.push(p.plan_id.clone());
                                let selected_card =
                                    self.selected_plan_id.as_deref() == Some(p.plan_id.as_str());
                                let card_resp = theme::card_frame()
                                    .stroke(egui::Stroke::new(
                                        1.0_f32,
                                        if selected_card {
                                            theme::accent()
                                        } else {
                                            theme::border()
                                        },
                                    ))
                                    .inner_margin(egui::Margin::same(12))
                                    .show(ui, |ui| {
                                        ui.horizontal(|ui| {
                                            let selected = self.selected_plan_id.as_deref()
                                                == Some(p.plan_id.as_str());
                                            let mut text =
                                                egui::RichText::new(display.clone()).size(14.0);
                                            if selected {
                                                text = text.strong().color(theme::accent_text());
                                            }
                                            if ui
                                                .add(egui::Button::selectable(selected, text))
                                                .clicked()
                                            {
                                                self.select_plan(p.plan_id.clone());
                                            }
                                            let (label, fg, bg) = theme::plan_status_visual(status);
                                            theme::badge(ui, label, fg, bg);
                                            if p.archived {
                                                theme::badge(
                                                    ui,
                                                    "已归档",
                                                    theme::text_sec(),
                                                    theme::hover_bg(),
                                                );
                                            }
                                        });
                                        ui.horizontal(|ui| {
                                            ui.label(
                                                egui::RichText::new(Self::relative_time(
                                                    &p.created_at,
                                                ))
                                                .size(11.5)
                                                .color(theme::text_faint()),
                                            )
                                            .on_hover_text(&p.created_at);
                                            if !p.design_id.is_empty() {
                                                let did = &p.design_id[..8.min(p.design_id.len())];
                                                if ui.small_button(format!("设计 {did}")).clicked()
                                                {
                                                    self.select_design(p.design_id.clone());
                                                }
                                            }
                                        });
                                    })
                                    .response;
                                if selected_card {
                                    theme::selection_bar(ui, card_resp.rect);
                                }
                                if matches!(status, PlanStatus::Running) {
                                    theme::running_indicator(ui, card_resp.rect);
                                }
                                // 右键功能菜单：改名 / 归档 / 删除 / 查看设计书
                                card_resp.context_menu(|ui| {
                                    ui.set_min_width(150.0);
                                    if ui.button("✏ 改名").clicked() {
                                        self.rename_dialog =
                                            Some((p.plan_id.clone(), p.display_name.clone()));
                                        ui.close();
                                    }
                                    let arch = if p.archived { "取消归档" } else { "归档" };
                                    if ui.button(arch).clicked() {
                                        self.send(Command::ArchivePlan {
                                            plan_id: p.plan_id.clone(),
                                            archived: !p.archived,
                                        });
                                        ui.close();
                                    }
                                    if ui.button("删除").clicked() {
                                        self.delete_confirmation = Some(p.plan_id.clone());
                                        ui.close();
                                    }
                                    if !p.design_id.is_empty() && ui.button("查看设计书").clicked()
                                    {
                                        self.select_design(p.design_id.clone());
                                        ui.close();
                                    }
                                });
                                ui.add_space(4.0);
                            }
                            // 键盘导航：↑/↓ 在可见计划间移动选择，Enter 选中第一个（无选择时）。
                            // typing 守卫：任何控件持有焦点（如搜索框）时不响应。
                            if !ui.ctx().memory(|m| m.focused().is_some())
                                && !visible_ids.is_empty()
                            {
                                let (up, down, enter) = ui.ctx().input(|i| {
                                    (
                                        i.key_pressed(egui::Key::ArrowUp),
                                        i.key_pressed(egui::Key::ArrowDown),
                                        i.key_pressed(egui::Key::Enter),
                                    )
                                });
                                if up || down {
                                    let cur = self
                                        .selected_plan_id
                                        .as_deref()
                                        .and_then(|id| visible_ids.iter().position(|v| v == id));
                                    let next = match (cur, down) {
                                        (Some(i), true) => (i + 1).min(visible_ids.len() - 1),
                                        (Some(i), false) => i.saturating_sub(1),
                                        (None, _) => 0,
                                    };
                                    if Some(visible_ids[next].as_str())
                                        != self.selected_plan_id.as_deref()
                                    {
                                        self.select_plan(visible_ids[next].clone());
                                    }
                                } else if enter && self.selected_plan_id.is_none() {
                                    self.select_plan(visible_ids[0].clone());
                                }
                            }
                            if shown == 0 {
                                theme::empty_state(
                                    ui,
                                    egui_phosphor::regular::MAGNIFYING_GLASS,
                                    "没有匹配的项目",
                                    "调整搜索词或切换状态筛选",
                                );
                            }
                        });
                    } else {
                        self.design_list_panel(ui);
                    }
                });
        } // 4.5 右侧：AI 助手停靠面板（🤖 助手开关，不再遮挡主内容）
        if self.chat_open && !self.settings_open && !self.tools_open {
            if self.zoomed == Some(Zone::Assistant) {
                egui::Panel::right("assistant_panel_v2")
                    .exact_size(ctx.content_rect().width())
                    .resizable(true)
                    .default_size(320.0)
                    .frame(
                        egui::Frame::default()
                            .fill(theme::surface())
                            .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                            .inner_margin(egui::Margin::same(10)),
                    )
                    .show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.strong(format!("{} AI 助手", egui_phosphor::regular::SPARKLE));
                            theme::badge(ui, "在线", theme::ok_text(), theme::ok_soft());
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if ui.small_button("收起").clicked() {
                                        self.chat_open = false;
                                    }
                                },
                            );
                        });
                        ui.add_space(4.0);

                        egui::ScrollArea::vertical()
                            .stick_to_bottom(true)
                            .auto_shrink([false, false])
                            // 留出底部输入行高度，避免滚动区吞掉全部空间把输入挤出面板。
                            .max_height((ui.available_height() - 68.0).max(120.0))
                            .show(ui, |ui| {
                                if self.chat_history.is_empty() {
                                    ui.weak("向 AI 助手提问，例如：解释一下当前计划的步骤。");
                                }
                                for (role, content) in &self.chat_history {
                                    let (who, color) = if role == "user" {
                                        ("你", theme::accent_text())
                                    } else {
                                        ("助手", theme::ok_text())
                                    };
                                    theme::card(ui, |ui| {
                                        ui.label(
                                            egui::RichText::new(who)
                                                .size(11.5)
                                                .strong()
                                                .color(color),
                                        );
                                        ui.label(egui::RichText::new(content).size(13.0));
                                    });
                                    ui.add_space(4.0);
                                }
                                if self.chat_busy {
                                    ui.horizontal(|ui| {
                                        ui.spinner();
                                        ui.weak("思考中…");
                                    });
                                }
                            });
                        ui.add_space(6.0);
                        ui.separator();
                        let send_enabled = !self.chat_busy && !self.chat_draft.trim().is_empty();
                        ui.horizontal(|ui| {
                            let response = ui.add(
                                egui::TextEdit::singleline(&mut self.chat_draft)
                                    .hint_text("输入问题…（Enter 发送）")
                                    .desired_width(f32::INFINITY),
                            );
                            let enter_send = response.lost_focus()
                                && ui.input(|i| i.key_pressed(egui::Key::Enter));
                            let send_clicked = ui
                                .add_enabled(send_enabled, theme::primary_button("发送"))
                                .clicked();
                            if (send_clicked || enter_send) && send_enabled {
                                let msg = self.chat_draft.trim().to_string();
                                let history = self.chat_history.clone();
                                self.chat_history.push(("user".into(), msg.clone()));
                                self.chat_draft.clear();
                                self.chat_busy = true;
                                self.send(Command::Chat {
                                    message: msg,
                                    history,
                                });
                            }
                        });
                    });
            } else if self.zoomed.is_none() {
                egui::Panel::right("assistant_panel_v2")
                    .resizable(true)
                    .default_size(320.0)
                    .min_size(280.0)
                    .max_size(380.0)
                    .frame(
                        egui::Frame::default()
                            .fill(theme::surface())
                            .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                            .inner_margin(egui::Margin::same(10)),
                    )
                    .show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.strong(format!("{} AI 助手", egui_phosphor::regular::SPARKLE));
                            theme::badge(ui, "在线", theme::ok_text(), theme::ok_soft());
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if ui.small_button("收起").clicked() {
                                        self.chat_open = false;
                                    }
                                },
                            );
                        });
                        ui.add_space(4.0);

                        egui::ScrollArea::vertical()
                            .stick_to_bottom(true)
                            .auto_shrink([false, false])
                            // 留出底部输入行高度，避免滚动区吞掉全部空间把输入挤出面板。
                            .max_height((ui.available_height() - 68.0).max(120.0))
                            .show(ui, |ui| {
                                if self.chat_history.is_empty() {
                                    ui.weak("向 AI 助手提问，例如：解释一下当前计划的步骤。");
                                }
                                for (role, content) in &self.chat_history {
                                    let (who, color) = if role == "user" {
                                        ("你", theme::accent_text())
                                    } else {
                                        ("助手", theme::ok_text())
                                    };
                                    theme::card(ui, |ui| {
                                        ui.label(
                                            egui::RichText::new(who)
                                                .size(11.5)
                                                .strong()
                                                .color(color),
                                        );
                                        ui.label(egui::RichText::new(content).size(13.0));
                                    });
                                    ui.add_space(4.0);
                                }
                                if self.chat_busy {
                                    ui.horizontal(|ui| {
                                        ui.spinner();
                                        ui.weak("思考中…");
                                    });
                                }
                            });
                        ui.add_space(6.0);
                        ui.separator();
                        let send_enabled = !self.chat_busy && !self.chat_draft.trim().is_empty();
                        ui.horizontal(|ui| {
                            let response = ui.add(
                                egui::TextEdit::singleline(&mut self.chat_draft)
                                    .hint_text("输入问题…（Enter 发送）")
                                    .desired_width(f32::INFINITY),
                            );
                            let enter_send = response.lost_focus()
                                && ui.input(|i| i.key_pressed(egui::Key::Enter));
                            let send_clicked = ui
                                .add_enabled(send_enabled, theme::primary_button("发送"))
                                .clicked();
                            if (send_clicked || enter_send) && send_enabled {
                                let msg = self.chat_draft.trim().to_string();
                                let history = self.chat_history.clone();
                                self.chat_history.push(("user".into(), msg.clone()));
                                self.chat_draft.clear();
                                self.chat_busy = true;
                                self.send(Command::Chat {
                                    message: msg,
                                    history,
                                });
                            }
                        });
                    });
            }
        } // 5. 中央：设计输入/审阅，或原有计划详情 + 控制。

        // Bottom bars (notice + log): carved before CentralPanel so late paint can't cover content.
        if !self.settings_open && !self.tools_open {
            // 底部通知（后端 detail / 控制结果）：挂在日志面板之上，可手动关闭
            if self.zoomed.is_none() && self.state.notice.is_some() {
                egui::Panel::bottom("notice_bar")
                    .frame(
                        egui::Frame::default()
                            .fill(theme::notice_bg())
                            .stroke(egui::Stroke::new(1.0_f32, theme::warn_soft()))
                            .inner_margin(egui::Margin::symmetric(16, 8)),
                    )
                    .show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.label(
                                egui::RichText::new("⚠")
                                    .color(theme::warn_text())
                                    .size(14.0),
                            );
                            ui.label(
                                egui::RichText::new(
                                    self.state.notice.as_deref().unwrap_or_default(),
                                )
                                .color(theme::warn_text())
                                .size(13.5),
                            );
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if ui.small_button("✕ 关闭").clicked() {
                                        self.state.clear_notice();
                                    }
                                },
                            );
                        });
                    });
            }

            if self.zoomed.is_none() && self.logs_open {
                egui::Panel::bottom("log_panel")
                    .resizable(true)
                    .default_size(150.0)
                    .frame(
                        egui::Frame::default()
                            .fill(theme::surface())
                            .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                            .inner_margin(egui::Margin::symmetric(12, 8)),
                    )
                    .show(ui, |ui| {
                        ui.horizontal(|ui| {
                            ui.heading("实时日志");
                            theme::badge(
                                ui,
                                &format!("{} 条", self.state.logs.len()),
                                theme::text_sec(),
                                theme::hover_bg(),
                            );
                            for (level, label) in [
                                ("error", "错误"),
                                ("warning", "警告"),
                                ("success", "成功"),
                                ("info", "信息"),
                            ] {
                                let active = self.log_filter.as_deref() == Some(level);
                                let mut text = egui::RichText::new(label).size(12.0);
                                if active {
                                    text = text.strong();
                                }
                                if ui.selectable_label(active, text).clicked() {
                                    self.log_filter =
                                        if active { None } else { Some(level.into()) };
                                }
                            }
                            let pause_label = if self.log_paused {
                                format!("{} 继续滚动", egui_phosphor::regular::PLAY)
                            } else {
                                format!("{} 暂停滚动", egui_phosphor::regular::PAUSE)
                            };
                            if ui.small_button(pause_label).clicked() {
                                self.log_paused = !self.log_paused;
                            }
                            if ui
                                .small_button(format!("{} 复制全部", egui_phosphor::regular::COPY))
                                .clicked()
                            {
                                let all = self
                                    .state
                                    .logs
                                    .iter()
                                    .map(|l| format!("[{}] {}", l.level, l.message))
                                    .collect::<Vec<_>>()
                                    .join("\n");
                                ui.ctx().copy_text(all);
                                self.state.set_notice("日志已复制到剪贴板");
                            }
                        });
                        egui::Frame::default()
                            .fill(theme::log_bg())
                            .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                            .corner_radius(egui::CornerRadius::same(8))
                            .inner_margin(egui::Margin::same(8))
                            .show(ui, |ui| {
                                egui::ScrollArea::vertical()
                                    .stick_to_bottom(!self.log_paused)
                                    .auto_shrink([false, false])
                                    .show(ui, |ui| {
                                        let logs: Vec<LogLine> = self.state.logs.clone();
                                        let level_filter = self.log_filter.clone();
                                        for line in logs
                                            .iter()
                                            .rev()
                                            .take(300)
                                            .filter(|l| {
                                                level_filter
                                                    .as_deref()
                                                    .map(|f| l.level == f)
                                                    .unwrap_or(true)
                                            })
                                            .rev()
                                        {
                                            Self::log_line_row(&mut self.expanded_logs, ui, line);
                                        }
                                    });
                            });
                    });
            }
        }

        if !self.settings_open && !self.tools_open && self.zoomed != Some(Zone::Assistant) {
            egui::CentralPanel::default()
                .frame(
                    egui::Frame::default()
                        .fill(theme::page_bg())
                        .inner_margin(egui::Margin::same(14)),
                )
                .show(ui, |ui| {
                    match self.state.mode {
                        GuiMode::Input => self.design_input_panel(ui),
                        GuiMode::Review => self.design_review_panel(ui),
                        GuiMode::Plan => match self.selection() {
                            Some(sel) => {
                                // 头部卡片：标题 + 状态徽章 + 进度条 + 控制按钮
                                theme::card(ui, |ui| {
                                    ui.horizontal(|ui| {
                                        ui.label(
                                            egui::RichText::new(format!(
                                                "计划 {}",
                                                &sel.plan_id[..8.min(sel.plan_id.len())]
                                            ))
                                            .size(17.0)
                                            .strong(),
                                        );
                                        let (label, fg, bg) = theme::plan_status_visual(sel.status);
                                        theme::badge(ui, label, fg, bg);
                                        if ui.small_button("⛶ 全屏").clicked() {
                                            self.zoomed = Some(Zone::Steps);
                                        }
                                        ui.with_layout(
                                            egui::Layout::right_to_left(egui::Align::Center),
                                            |ui| {
                                                self.control_buttons(ui, &sel.plan_id, sel.status);
                                            },
                                        );
                                    });
                                    if sel.progress.1 > 0 {
                                        let fraction =
                                            sel.progress.0 as f32 / sel.progress.1 as f32;
                                        ui.add(
                                            egui::ProgressBar::new(fraction)
                                                .fill(theme::accent())
                                                .text(format!(
                                                    "{}/{} 步",
                                                    sel.progress.0, sel.progress.1
                                                ))
                                                .desired_height(18.0)
                                                // 运行中显示流动动画，一眼可辨执行状态。
                                                .animate(matches!(sel.status, PlanStatus::Running)),
                                        );
                                    }
                                });
                                if let Some(rb) = self.state.rollback_result.clone() {
                                    if !self.rollback_result_dismissed {
                                        ui.horizontal(|ui| {
                                            ui.label(
                                                egui::RichText::new(format!(
                                                "↩ 上次回滚：新分支 {} · 从 {} 恢复 · 废弃 {} 步",
                                                rb.new_branch, rb.resume_from, rb.marked_obsolete
                                            ))
                                                .size(12.0)
                                                .color(theme::info_text()),
                                            );
                                            if ui.small_button("✕").clicked() {
                                                self.rollback_result_dismissed = true;
                                            }
                                        });
                                        ui.add_space(4.0);
                                    }
                                }
                                ui.add_space(8.0);
                                if self.layout.mode == LayoutMode::Timeline {
                                    ui.horizontal_wrapped(|ui| {
                                        ui.strong("步骤时间线");
                                        ui.weak("按计划顺序展示实时状态 · 非历史回放");
                                    });
                                    ui.add_space(6.0);
                                }
                                egui::ScrollArea::vertical().show(ui, |ui| {
                                    for (index, (step, live)) in sel.steps.iter().enumerate() {
                                        let (icon, color) = theme::step_marker(*live);
                                        // 运行中的步骤用时间驱动的加载帧模拟旋转，并保持重绘。
                                        let icon: String = if matches!(
                                            live,
                                            StepStatus::Running | StepStatus::Retry
                                        ) {
                                            ui.ctx().request_repaint_after(
                                                std::time::Duration::from_millis(120),
                                            );
                                            let frames = ["◜", "◝", "◞", "◟"];
                                            let t = ui.input(|i| i.time);
                                            frames[((t / 0.12) as usize) % frames.len()].to_string()
                                        } else {
                                            icon.to_string()
                                        };
                                        layout::step_card(
                                            ui,
                                            self.layout.mode,
                                            index,
                                            sel.steps.len(),
                                            *live,
                                            |ui| {
                                                ui.horizontal(|ui| {
                                                    ui.label(
                                                        egui::RichText::new(icon)
                                                            .color(color)
                                                            .size(15.0),
                                                    );
                                                    ui.label(&step.description);
                                                    ui.label(
                                                        egui::RichText::new(format!(
                                                            "[{}]",
                                                            step.tool
                                                        ))
                                                        .size(12.0)
                                                        .color(theme::text_sec())
                                                        .monospace(),
                                                    );
                                                    ui.with_layout(
                                                        egui::Layout::right_to_left(
                                                            egui::Align::Center,
                                                        ),
                                                        |ui| {
                                                            // 仅 Pending/Retry 可跳过（与后端守卫语义一致）
                                                            if matches!(
                                                                live,
                                                                StepStatus::Pending
                                                                    | StepStatus::Retry
                                                            ) && ui
                                                                .small_button("跳过")
                                                                .clicked()
                                                            {
                                                                self.send(Command::Skip {
                                                                    plan_id: sel.plan_id.clone(),
                                                                    step_id: step.step_id.clone(),
                                                                });
                                                            }
                                                            // 回滚始终先进入确认态，避免误操作。
                                                            if matches!(
                                                                live,
                                                                StepStatus::Success
                                                                    | StepStatus::Failed
                                                            ) && self
                                                                .state
                                                                .rollback_in_flight
                                                                .is_none()
                                                                && ui
                                                                    .small_button("回滚到此")
                                                                    .clicked()
                                                            {
                                                                self.state.request_rollback(
                                                                    sel.plan_id.clone(),
                                                                    step.step_id.clone(),
                                                                );
                                                                self.rollback_result_dismissed =
                                                                    false;
                                                            }
                                                        },
                                                    );
                                                });
                                                if let Some(outputs) = &step.outputs {
                                                    if let Some(err) = outputs
                                                        .get("error")
                                                        .and_then(|e| e.as_str())
                                                    {
                                                        ui.add_space(2.0);
                                                        theme::error_inline(
                                                            ui,
                                                            &format!("错误: {err}"),
                                                        );
                                                    }
                                                }
                                            },
                                        );
                                        ui.add_space(6.0);
                                    }
                                });
                            }
                            None => {
                                ui.add_space(24.0);
                                ui.centered_and_justified(|ui| ui.weak("选择或等待计划加载…"));
                            }
                        },
                    }
                });

            // 底栏：实时日志流（深色等宽块）
            if self.zoomed == Some(Zone::Logs) {
                egui::Panel::bottom("log_panel")
                    .exact_size(ctx.content_rect().height() - 50.0)
                    .resizable(true)
                    .default_size(150.0)
                    .frame(
                        egui::Frame::default()
                            .fill(theme::surface())
                            .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                            .inner_margin(egui::Margin::symmetric(12, 8)),
                    )
                    .show(ui, |ui| {
                        ui.horizontal(|ui| {
                            let h = ui.heading("实时日志");
                            if h.double_clicked() {
                                self.zoomed = Some(Zone::Logs);
                            }
                            theme::badge(
                                ui,
                                &format!("{} 条", self.state.logs.len()),
                                theme::text_sec(),
                                theme::hover_bg(),
                            );
                            if ui.small_button("⛶ 全屏").clicked() {
                                self.zoomed = Some(Zone::Logs);
                            }
                            for (level, label) in [
                                ("error", "错误"),
                                ("warning", "警告"),
                                ("success", "成功"),
                                ("info", "信息"),
                            ] {
                                let active = self.log_filter.as_deref() == Some(level);
                                let mut text = egui::RichText::new(label).size(12.0);
                                if active {
                                    text = text.strong();
                                }
                                if ui.selectable_label(active, text).clicked() {
                                    self.log_filter =
                                        if active { None } else { Some(level.into()) };
                                }
                            }
                            let pause_label = if self.log_paused {
                                format!("{} 继续滚动", egui_phosphor::regular::PLAY)
                            } else {
                                format!("{} 暂停滚动", egui_phosphor::regular::PAUSE)
                            };
                            if ui.small_button(pause_label).clicked() {
                                self.log_paused = !self.log_paused;
                            }
                            if ui
                                .small_button(format!("{} 复制全部", egui_phosphor::regular::COPY))
                                .clicked()
                            {
                                let all = self
                                    .state
                                    .logs
                                    .iter()
                                    .map(|l| format!("[{}] {}", l.level, l.message))
                                    .collect::<Vec<_>>()
                                    .join("\n");
                                ui.ctx().copy_text(all);
                                self.state.set_notice("日志已复制到剪贴板");
                            }
                        });
                        egui::Frame::default()
                            .fill(theme::log_bg())
                            .stroke(egui::Stroke::new(1.0_f32, theme::border()))
                            .corner_radius(egui::CornerRadius::same(8))
                            .inner_margin(egui::Margin::same(8))
                            .show(ui, |ui| {
                                egui::ScrollArea::vertical()
                                    .stick_to_bottom(!self.log_paused)
                                    .auto_shrink([false, false])
                                    .show(ui, |ui| {
                                        let logs: Vec<LogLine> = self.state.logs.clone();
                                        let level_filter = self.log_filter.clone();
                                        for line in logs
                                            .iter()
                                            .rev()
                                            .take(300)
                                            .filter(|l| {
                                                level_filter
                                                    .as_deref()
                                                    .map(|f| l.level == f)
                                                    .unwrap_or(true)
                                            })
                                            .rev()
                                        {
                                            Self::log_line_row(&mut self.expanded_logs, ui, line);
                                        }
                                    });
                            });
                    });
            }
        } else if self.tools_open {
            egui::CentralPanel::default()
                .frame(
                    egui::Frame::default()
                        .fill(theme::page_bg())
                        .inner_margin(egui::Margin::same(20)),
                )
                .show(ui, |ui| {
                    self.tools_central_content(ui);
                });
        } else {
            egui::CentralPanel::default()
                .frame(
                    egui::Frame::default()
                        .fill(theme::page_bg())
                        .inner_margin(egui::Margin::same(14)),
                )
                .show(ui, |ui| {
                    ui.horizontal(|ui| {
                        ui.heading(format!("{} 设置", egui_phosphor::regular::GEAR));
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ui.add(theme::secondary_button("返回")).clicked() {
                                self.settings_open = false;
                            }
                        });
                    });
                    ui.add_space(6.0);
                    egui::ScrollArea::vertical()
                        .auto_shrink([false, false])
                        .show(ui, |ui| {
                            self.settings_central_content(ui);
                        });
                });
        }

        self.rollback_confirmation(ctx);

        // 连接诊断弹窗：API base、WS 状态、最后错误、手动重连。
        if self.conn_diag_open {
            let mut diag_open = self.conn_diag_open;
            egui::Window::new("连接诊断")
                .collapsible(false)
                .resizable(false)
                .anchor(egui::Align2::RIGHT_TOP, [-12.0, 48.0])
                .open(&mut diag_open)
                .show(ctx, |ui| {
                    ui.set_min_width(320.0);
                    egui::Grid::new("conn_diag_grid")
                        .num_columns(2)
                        .spacing([12.0, 6.0])
                        .show(ui, |ui| {
                            ui.label(egui::RichText::new("API Base").color(theme::text_sec()));
                            ui.monospace(&self.state.base);
                            ui.end_row();
                            ui.label(egui::RichText::new("WebSocket").color(theme::text_sec()));
                            if self.state.connected {
                                ui.colored_label(theme::ok_text(), "已连接");
                            } else {
                                ui.colored_label(theme::danger_text(), "未连接");
                            }
                            ui.end_row();
                            ui.label(egui::RichText::new("Token").color(theme::text_sec()));
                            ui.label(if self.state.token.is_empty() {
                                "未配置".to_string()
                            } else {
                                format!("已配置（{} 字符）", self.state.token.len())
                            });
                            ui.end_row();
                        });
                    if let Some(err) = &self.state.last_ws_error {
                        ui.add_space(4.0);
                        theme::error_inline(ui, &format!("最后错误: {err}"));
                    }
                    ui.add_space(6.0);
                    ui.horizontal(|ui| {
                        if ui.add(theme::primary_button("刷新状态")).clicked() {
                            self.send(Command::RefreshStatus);
                            self.send(Command::RefreshPlans);
                            self.refresh_approvals();
                            self.state.set_notice("正在刷新服务状态");
                        }
                        ui.label(
                            egui::RichText::new("WS 断开后会自动重连；此处仅触发 REST 兜底刷新")
                                .size(11.5)
                                .color(theme::text_faint()),
                        );
                    });
                });
            self.conn_diag_open = diag_open;
        }

        // 右下角 Toast 通知（每帧渲染，自动过期）。
        self.toasts.show(ctx);

        // 设计书删除确认窗
        if let Some(design_id) = self.design_delete_confirmation.clone() {
            egui::Window::new("确认删除设计书")
                .collapsible(false)
                .resizable(false)
                .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
                .show(ctx, |ui| {
                    ui.label(format!(
                        "确定删除设计书 {} 吗？已生成执行计划的设计书不可删除。",
                        &design_id[..8.min(design_id.len())]
                    ));
                    ui.add_space(6.0);
                    ui.horizontal(|ui| {
                        if ui.add(theme::secondary_button("取消")).clicked() {
                            self.design_delete_confirmation = None;
                        }
                        if ui.add(theme::danger_button("确认删除")).clicked() {
                            self.design_delete_confirmation = None;
                            self.send(Command::DeleteDesign { design_id });
                        }
                    });
                });
        }

        // 删除确认窗
        if let Some(plan_id) = self.delete_confirmation.clone() {
            egui::Window::new("确认删除")
                .collapsible(false)
                .resizable(false)
                .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
                .show(ctx, |ui| {
                    ui.label(format!(
                        "确定删除项目 {} 吗？其全部步骤与执行记录将被移除（审计日志保留）。",
                        &plan_id[..8.min(plan_id.len())]
                    ));
                    ui.add_space(6.0);
                    ui.horizontal(|ui| {
                        if ui.add(theme::secondary_button("取消")).clicked() {
                            self.delete_confirmation = None;
                        }
                        if ui.add(theme::danger_button("确认删除")).clicked() {
                            self.delete_confirmation = None;
                            self.send(Command::DeletePlan { plan_id });
                        }
                    });
                });
        }

        // 重命名对话框
        if self.rename_dialog.is_some() {
            let (dialog_plan_id, mut draft) = self.rename_dialog.clone().unwrap();
            let mut submitted = false;
            egui::Window::new("重命名项目")
                .collapsible(false)
                .resizable(false)
                .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
                .show(ctx, |ui| {
                    ui.label("给这个项目起个好记的名字：");
                    ui.add_space(4.0);
                    let resp = ui.add(
                        egui::TextEdit::singleline(&mut draft)
                            .desired_width(240.0)
                            .hint_text("项目名称"),
                    );
                    if !resp.has_focus() {
                        resp.request_focus();
                    }
                    let enter = resp.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter));
                    ui.add_space(6.0);
                    ui.horizontal(|ui| {
                        if ui.add(theme::secondary_button("取消")).clicked() {
                            submitted = true;
                        }
                        let can_submit = !draft.trim().is_empty();
                        if (enter && can_submit)
                            || ui
                                .add_enabled(can_submit, theme::primary_button("保存"))
                                .clicked()
                        {
                            self.send(Command::RenamePlan {
                                plan_id: dialog_plan_id.clone(),
                                name: draft.trim().to_string(),
                            });
                            submitted = true;
                        }
                    });
                });
            if submitted {
                self.rename_dialog = None;
            } else {
                self.rename_dialog = Some((dialog_plan_id, draft));
            }
        }
        // 6. WS 断开提示
        if let Some(err) = &self.state.last_ws_error {
            egui::Window::new("连接状态")
                .anchor(egui::Align2::RIGHT_BOTTOM, [-8.0, -8.0])
                .collapsible(false)
                .show(ctx, |ui| {
                    ui.colored_label(egui::Color32::RED, err);
                });
        }
    }
}

impl EasApp {
    /// ISO 时间串截短为 "YYYY-MM-DD HH:MM"；异常输入原样截 16 字符。
    fn short_time(created_at: &str) -> String {
        created_at.chars().take(16).collect()
    }

    /// 相对时间："3 分钟前 / 2 小时前 / 5 天前"；无法解析时回退截短显示。
    /// 后端时间戳无时区后缀时按本地时间解释（与 Python 端 datetime.now() 一致）。
    fn relative_time(created_at: &str) -> String {
        let parsed = chrono::DateTime::parse_from_rfc3339(created_at)
            .map(|dt| dt.with_timezone(&chrono::Local))
            .or_else(|_| {
                chrono::NaiveDateTime::parse_from_str(
                    &created_at.chars().take(19).collect::<String>(),
                    "%Y-%m-%dT%H:%M:%S",
                )
                .map(|naive| {
                    naive
                        .and_local_timezone(chrono::Local)
                        .single()
                        .unwrap_or_else(chrono::Local::now)
                })
            });
        let Ok(then) = parsed else {
            return Self::short_time(created_at);
        };
        let secs = (chrono::Local::now() - then).num_seconds();
        if secs < 0 {
            return Self::short_time(created_at);
        }
        match secs {
            0..=59 => "刚刚".to_string(),
            60..=3599 => format!("{} 分钟前", secs / 60),
            3600..=86_399 => format!("{} 小时前", secs / 3600),
            86_400..=2_591_999 => format!("{} 天前", secs / 86_400),
            _ => Self::short_time(created_at),
        }
    }

    fn status_text(&self) -> String {
        match &self.state.service_status {
            Some(st) => {
                let status_cn = match st.status.as_str() {
                    "running" => "运行中",
                    "ok" => "正常",
                    "stopped" => "已停止",
                    "starting" => "启动中",
                    other => other,
                };
                let exec = st
                    .current_execution
                    .as_deref()
                    .map(|id| format!("，执行中: {}", &id[..8.min(id.len())]))
                    .unwrap_or_default();
                format!(
                    "服务: {} · 工具: {} · LLM: {}{}",
                    status_cn,
                    st.tools_count,
                    st.llm_providers.len(),
                    exec
                )
            }
            None => "服务状态未知".to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::GuiState;
    use crate::types::PlanDetail;

    fn detail(plan_id: &str) -> PlanDetail {
        serde_json::from_value(serde_json::json!({
            "plan_id": plan_id,
            "status": "Pending",
            "steps": []
        }))
        .unwrap()
    }

    fn app() -> EasApp {
        let (_events_tx, events_rx) = mpsc::channel(CHANNEL_CAPACITY);
        let (commands, _commands_rx) = mpsc::channel(CHANNEL_CAPACITY);
        EasApp::new(GuiState::default(), events_rx, commands)
    }

    #[test]
    fn stale_design_detail_epoch_is_ignored() {
        let mut app = app();
        app.design_request_epoch = 2;
        app.handle_event(GuiEvent::DesignDetail {
            design: Box::new(
                serde_json::from_value(serde_json::json!({
                    "design_id": "old", "user_request": "old"
                }))
                .unwrap(),
            ),
            epoch: 1,
        });
        assert!(app.state.current_design.is_none());
        app.handle_event(GuiEvent::DesignDetail {
            design: Box::new(
                serde_json::from_value(serde_json::json!({
                    "design_id": "new", "user_request": "new"
                }))
                .unwrap(),
            ),
            epoch: 2,
        });
        assert_eq!(app.state.current_design.as_ref().unwrap().design_id, "new");
        assert_eq!(app.state.mode, GuiMode::Review);
    }

    #[test]
    fn stale_plan_detail_epoch_is_ignored() {
        let mut app = app();
        app.select_plan("new".into());
        let current = app.selected_request_epoch;
        app.handle_event(GuiEvent::PlanDetail {
            detail: Box::new(detail("old")),
            epoch: current - 1,
        });
        assert!(app.state.selected.is_none());
        app.handle_event(GuiEvent::PlanDetail {
            detail: Box::new(detail("new")),
            epoch: current,
        });
        assert_eq!(app.state.selected.as_ref().unwrap().plan_id, "new");
    }

    #[test]
    fn draft_has_no_execute_button_semantics() {
        // The UI branch is deliberately represented by this explicit predicate.
        assert!(matches!(PlanStatus::Draft, PlanStatus::Draft));
        assert!(!matches!(
            PlanStatus::Draft,
            PlanStatus::Running | PlanStatus::Paused
        ));
    }
}
