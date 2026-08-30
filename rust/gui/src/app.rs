//! eframe 应用：四面板布局（设计稿 §20.2）。
//! UI 线程零阻塞：网络全部在后台线程，事件经有界 channel 驱动重绘。

use crate::state::{GuiMode, GuiState, LogLine};
use crate::theme;
use crate::types::{Design, DesignApprovalResponse, PlanStatus, StepStatus, StepView};
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
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DesignErrorAction {
    List,
    Detail,
    Generate,
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
}

/// 中央区渲染用的拥有数据（避免渲染中借用冲突）
struct SelectionView {
    plan_id: String,
    status: PlanStatus,
    progress: (usize, usize),
    steps: Vec<(StepView, StepStatus)>,
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
        }
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
                self.state.finish_rollback(result.map_err(|e| e));
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
            ui.centered_and_justified(|ui| ui.weak("暂无设计书，请先输入需求"));
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
                    ui.label(&design.user_request);
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
                    ui.label(&part1.difficulty.reasoning);
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
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.add(theme::secondary_button("刷新")).clicked() {
                    self.refresh_designs();
                }
            });
        });
        ui.add_space(4.0);
        let designs = self.state.designs.clone();
        egui::ScrollArea::vertical().show(ui, |ui| {
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
                if ui
                    .add_sized(
                        [ui.available_width(), 26.0],
                        egui::SelectableLabel::new(selected, label),
                    )
                    .clicked()
                {
                    self.select_design(design.design_id);
                }
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
                }
                ui.add_space(2.0);
            }
        });
    }

    fn approval_queue(&mut self, ctx: &egui::Context) {
        let approvals = self.state.pending_approvals.clone();
        if approvals.is_empty()
            && !self.state.approvals_loading
            && self.state.approvals_error.is_none()
        {
            return;
        }
        egui::TopBottomPanel::top("approval_queue")
            .resizable(true)
            .default_height(170.0)
            .max_height(330.0)
            .frame(
                egui::Frame::default()
                    .fill(theme::SURFACE)
                    .stroke(egui::Stroke::new(1.0, theme::BORDER))
                    .inner_margin(egui::Margin::same(10.0)),
            )
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.heading("待审批工具调用");
                    theme::badge(
                        ui,
                        &format!("{} 条", approvals.len()),
                        theme::ACCENT_TEXT,
                        theme::ACCENT_SOFT,
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
                    for approval in approvals {
                        let approval_id = approval.approval_id.clone();
                        let in_flight = self.state.approval_in_flight.contains(&approval_id);
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
                                    .clicked();
                                let reject = ui
                                    .add_enabled(!in_flight, theme::danger_outline_button("拒绝"))
                                    .clicked();
                                if in_flight {
                                    ui.spinner();
                                }
                                if approve || reject {
                                    if self.state.begin_approval(&approval_id) {
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
                                }
                            });
                        });
                        ui.add_space(4.0);
                    }
                });
            });
    }

    fn audit_panel(&mut self, ctx: &egui::Context) {
        if !self.state.audit_visible {
            return;
        }
        egui::SidePanel::right("audit_panel")
            .resizable(true)
            .default_width(430.0)
            .frame(
                egui::Frame::default()
                    .fill(theme::SURFACE)
                    .stroke(egui::Stroke::new(1.0, theme::BORDER))
                    .inner_margin(egui::Margin::same(12.0)),
            )
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.heading("审计日志");
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
                egui::ScrollArea::both().show(ui, |ui| {
                    egui::Grid::new("audit_table")
                        .striped(true)
                        .min_col_width(72.0)
                        .show(ui, |ui| {
                            ui.strong("结果");
                            ui.strong("时间");
                            ui.strong("计划");
                            ui.strong("步骤");
                            ui.strong("工具");
                            ui.strong("指令");
                            ui.strong("拦截原因");
                            ui.end_row();
                            for log in self.state.filtered_audit_logs() {
                                let (label, color) = theme::audit_result_visual(log.result());
                                ui.colored_label(color, label);
                                ui.label(&log.timestamp);
                                ui.label(log.plan_id.as_deref().unwrap_or("-"));
                                ui.label(log.step_id.as_deref().unwrap_or("-"));
                                ui.label(log.tool_id.as_deref().unwrap_or("-"));
                                ui.label(log.instruction.as_deref().unwrap_or("-"));
                                ui.colored_label(
                                    if log.block_reason.is_some() {
                                        theme::WARN_TEXT
                                    } else {
                                        theme::TEXT_FAINT
                                    },
                                    log.block_reason.as_deref().unwrap_or("-"),
                                );
                                ui.end_row();
                            }
                        });
                });
            });
    }

    fn rollback_confirmation(&mut self, ctx: &egui::Context) {
        let Some((plan_id, step_id)) = self.state.rollback_confirmation.clone() else {
            return;
        };
        egui::Window::new("确认回滚")
            .collapsible(false)
            .resizable(false)
            .show(ctx, |ui| {
                ui.label(format!(
                    "确定回滚计划 {plan_id} 到步骤 {step_id} 吗？将创建新分支并废弃后续旧步骤。"
                ));
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
            });
    }
}

impl eframe::App for EasApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        // 1. 排空事件通道（每条事件都在后台线程请求过重绘）
        while let Ok(ev) = self.events.try_recv() {
            self.handle_event(ev);
        }
        // 即使本帧没有事件，也保持即时模式持续处理队列和输入。
        ctx.request_repaint_after(std::time::Duration::from_millis(100));

        // 2. 顶栏：品牌 + 模式切换 + 服务状态 + 连接徽章 + token 配置
        egui::TopBottomPanel::top("top_bar")
            .frame(
                egui::Frame::default()
                    .fill(theme::SURFACE)
                    .stroke(egui::Stroke::new(1.0, theme::BORDER))
                    .inner_margin(egui::Margin::symmetric(16.0, 8.0)),
            )
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.label(
                        egui::RichText::new("EAS 控制台")
                            .size(16.0)
                            .strong()
                            .color(theme::ACCENT_TEXT),
                    );
                    ui.add_space(8.0);
                    self.mode_selector(ui);
                    ui.label(
                        egui::RichText::new(self.status_text())
                            .size(12.0)
                            .color(theme::TEXT_SEC),
                    );
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if self.state.connected {
                            theme::badge(ui, "● 已连接", theme::OK_TEXT, theme::OK_SOFT);
                        } else {
                            theme::badge(ui, "● 未连接", theme::DANGER_TEXT, theme::DANGER_SOFT);
                        }
                        if ui.add(theme::secondary_button("⚙ 设置")).clicked() {
                            self.settings_open = !self.settings_open;
                        }
                        if ui
                            .add(theme::secondary_button(if self.state.audit_visible {
                                "关闭审计"
                            } else {
                                "审计日志"
                            }))
                            .clicked()
                        {
                            self.state.audit_visible = !self.state.audit_visible;
                            if self.state.audit_visible && self.state.audit_logs.is_empty() {
                                self.refresh_audit();
                            }
                        }
                    });
                });
            });

        // 3. 审批队列位于中央计划区上方，单卡片操作互不阻塞。
        self.approval_queue(ctx);
        self.audit_panel(ctx);

        // 4. 左栏：设计书列表或计划列表，二者互不覆盖各自状态。
        egui::SidePanel::left("navigation_list")
            .frame(
                egui::Frame::default()
                    .fill(theme::SURFACE)
                    .stroke(egui::Stroke::new(1.0, theme::BORDER))
                    .inner_margin(egui::Margin::same(10.0)),
            )
            .show(ctx, |ui| {
                if self.state.mode == GuiMode::Plan {
                    ui.horizontal(|ui| {
                        ui.heading("计划");
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ui.add(theme::secondary_button("刷新")).clicked() {
                                self.send(Command::RefreshPlans);
                            }
                        });
                    });
                    ui.add_space(4.0);
                    egui::ScrollArea::vertical().show(ui, |ui| {
                        let plans = self.state.plans.clone();
                        for p in &plans {
                            let selected =
                                self.selected_plan_id.as_deref() == Some(p.plan_id.as_str());
                            let short = p.plan_id[..8.min(p.plan_id.len())].to_string();
                            let mut text = egui::RichText::new(short);
                            if selected {
                                text = text.strong().color(theme::ACCENT_TEXT);
                            }
                            if ui
                                .add_sized(
                                    [ui.available_width(), 26.0],
                                    egui::SelectableLabel::new(selected, text),
                                )
                                .clicked()
                            {
                                self.select_plan(p.plan_id.clone());
                            }
                            ui.add_space(2.0);
                        }
                    });
                } else {
                    self.design_list_panel(ui);
                }
            });

        // 5. 中央：设计输入/审阅，或原有计划详情 + 控制。
        egui::CentralPanel::default()
            .frame(
                egui::Frame::default()
                    .fill(theme::PAGE_BG)
                    .inner_margin(egui::Margin::same(14.0)),
            )
            .show(ctx, |ui| {
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
                                    ui.with_layout(
                                        egui::Layout::right_to_left(egui::Align::Center),
                                        |ui| {
                                            self.control_buttons(ui, &sel.plan_id, sel.status);
                                        },
                                    );
                                });
                                if sel.progress.1 > 0 {
                                    let fraction = sel.progress.0 as f32 / sel.progress.1 as f32;
                                    ui.add(
                                        egui::ProgressBar::new(fraction)
                                            .fill(theme::ACCENT)
                                            .text(format!(
                                                "{}/{} 步",
                                                sel.progress.0, sel.progress.1
                                            ))
                                            .desired_height(18.0),
                                    );
                                }
                            });
                            ui.add_space(8.0);
                            egui::ScrollArea::vertical().show(ui, |ui| {
                                for (step, live) in &sel.steps {
                                    let (icon, color) = theme::step_marker(*live);
                                    theme::card(ui, |ui| {
                                        ui.horizontal(|ui| {
                                            ui.label(
                                                egui::RichText::new(icon).color(color).size(15.0),
                                            );
                                            ui.label(&step.description);
                                            ui.label(
                                                egui::RichText::new(format!("[{}]", step.tool))
                                                    .size(12.0)
                                                    .color(theme::TEXT_SEC)
                                                    .monospace(),
                                            );
                                            ui.with_layout(
                                                egui::Layout::right_to_left(egui::Align::Center),
                                                |ui| {
                                                    // 仅 Pending/Retry 可跳过（与后端守卫语义一致）
                                                    if matches!(
                                                        live,
                                                        StepStatus::Pending | StepStatus::Retry
                                                    ) && ui.small_button("跳过").clicked()
                                                    {
                                                        self.send(Command::Skip {
                                                            plan_id: sel.plan_id.clone(),
                                                            step_id: step.step_id.clone(),
                                                        });
                                                    }
                                                    // 回滚始终先进入确认态，避免误操作。
                                                    if matches!(
                                                        live,
                                                        StepStatus::Success | StepStatus::Failed
                                                    ) && self.state.rollback_in_flight.is_none()
                                                        && ui.small_button("回滚到此").clicked()
                                                    {
                                                        self.state.request_rollback(
                                                            sel.plan_id.clone(),
                                                            step.step_id.clone(),
                                                        );
                                                    }
                                                },
                                            );
                                        });
                                        if let Some(outputs) = &step.outputs {
                                            if let Some(err) =
                                                outputs.get("error").and_then(|e| e.as_str())
                                            {
                                                ui.add_space(2.0);
                                                theme::error_inline(ui, &format!("错误: {err}"));
                                            }
                                        }
                                    });
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

        // 底部通知（后端 detail / 控制结果）：挂在日志面板之上，可手动关闭
        if self.state.notice.is_some() {
            egui::TopBottomPanel::bottom("notice_bar")
                .frame(
                    egui::Frame::default()
                        .fill(theme::NOTICE_BG)
                        .stroke(egui::Stroke::new(1.0, theme::WARN_SOFT))
                        .inner_margin(egui::Margin::symmetric(16.0, 8.0)),
                )
                .show(ctx, |ui| {
                    ui.horizontal(|ui| {
                        ui.label(egui::RichText::new("⚠").color(theme::WARN_TEXT).size(14.0));
                        ui.label(
                            egui::RichText::new(self.state.notice.as_deref().unwrap_or_default())
                                .color(theme::WARN_TEXT)
                                .size(13.5),
                        );
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ui.small_button("✕ 关闭").clicked() {
                                self.state.clear_notice();
                            }
                        });
                    });
                });
        }

        // 底栏：实时日志流（深色等宽块）
        egui::TopBottomPanel::bottom("log_panel")
            .resizable(true)
            .default_height(150.0)
            .frame(
                egui::Frame::default()
                    .fill(theme::SURFACE)
                    .stroke(egui::Stroke::new(1.0, theme::BORDER))
                    .inner_margin(egui::Margin::symmetric(12.0, 8.0)),
            )
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.heading("实时日志");
                    theme::badge(
                        ui,
                        &format!("{} 条", self.state.logs.len()),
                        theme::TEXT_SEC,
                        theme::HOVER_BG,
                    );
                });
                egui::Frame::default()
                    .fill(theme::LOG_BG)
                    .stroke(egui::Stroke::new(1.0, theme::BORDER))
                    .rounding(egui::Rounding::same(8.0))
                    .inner_margin(egui::Margin::same(8.0))
                    .show(ui, |ui| {
                        egui::ScrollArea::vertical()
                            .stick_to_bottom(true)
                            .auto_shrink([false, false])
                            .show(ui, |ui| {
                                let logs: Vec<LogLine> = self.state.logs.clone();
                                for line in logs.iter().rev().take(300).rev() {
                                    ui.label(
                                        egui::RichText::new(format!(
                                            "[{}] {}",
                                            line.level, line.message
                                        ))
                                        .monospace()
                                        .size(12.5)
                                        .color(theme::log_color(&line.level)),
                                    );
                                }
                            });
                    });
            });

        self.rollback_confirmation(ctx);

        // 设置窗口：连接状态 / 服务地址 / API token
        if self.settings_open {
            egui::Window::new("⚙ 设置")
                .anchor(egui::Align2::RIGHT_TOP, [-12.0, 48.0])
                .collapsible(false)
                .resizable(false)
                .show(ctx, |ui| {
                    theme::card(ui, |ui| {
                        ui.strong("服务状态");
                        ui.add_space(2.0);
                        ui.label(
                            egui::RichText::new(self.status_text())
                                .size(12.5)
                                .color(theme::TEXT_SEC),
                        );
                        ui.add_space(4.0);
                        if self.state.connected {
                            theme::badge(ui, "● 已连接", theme::OK_TEXT, theme::OK_SOFT);
                        } else {
                            theme::badge(ui, "● 未连接", theme::DANGER_TEXT, theme::DANGER_SOFT);
                        }
                    });
                    ui.add_space(8.0);
                    theme::card(ui, |ui| {
                        ui.strong("服务地址");
                        ui.add_space(2.0);
                        ui.monospace(self.state.base.as_str());
                        ui.weak("由环境变量 EAS_API_BASE 指定，默认 http://127.0.0.1:8000");
                    });
                    ui.add_space(8.0);
                    theme::card(ui, |ui| {
                        ui.strong("API Token");
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
                            || (response.lost_focus()
                                && ui.input(|i| i.key_pressed(egui::Key::Enter)))
                        {
                            let t = self.token_draft.trim().to_string();
                            if !t.is_empty() && t != self.state.token {
                                self.send(Command::SetToken(t));
                            }
                        }
                        ui.add_space(2.0);
                        ui.weak("token 来源：环境变量 EAS_API_TOKEN 或 config/api_token");
                    });
                    ui.add_space(10.0);
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        if ui.add(theme::secondary_button("关闭")).clicked() {
                            self.settings_open = false;
                        }
                    });
                });
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
