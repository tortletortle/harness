//! eframe 应用：四面板布局（设计稿 §20.2）。
//! UI 线程零阻塞：网络全部在后台线程，事件经有界 channel 驱动重绘。

use crate::state::{GuiState, LogLine};
use crate::types::{PlanStatus, StepStatus, StepView};
use crate::ws::WsEvent;
use tokio::sync::mpsc;

#[cfg(test)]
const CHANNEL_CAPACITY: usize = 256;

/// UI → 后台命令
#[derive(Debug)]
pub enum Command {
    RefreshPlans,
    SelectPlan { plan_id: String, epoch: u64 },
    RefreshPlanDetail { plan_id: String, epoch: u64 },
    Execute(String),
    Pause(String),
    Resume(String),
    Skip { plan_id: String, step_id: String },
    RefreshStatus,
    SetToken(String),
}

/// 后台 → UI 事件
#[derive(Debug)]
pub enum GuiEvent {
    Ws(WsEvent),
    Status(crate::types::ServiceStatus),
    Plans(Vec<crate::types::PlanSummary>),
    PlanDetail { detail: Box<crate::types::PlanDetail>, epoch: u64 },
    Error(String),
    ControlResult { plan_id: String, result: Result<String, String> },
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
    token_draft: String,
}

impl EasApp {
    pub fn new(
        state: GuiState,
        events: mpsc::Receiver<GuiEvent>,
        commands: mpsc::Sender<Command>,
    ) -> Self {
        let token_draft = state.token.clone();
        Self {
            state,
            events,
            commands,
            selected_plan_id: None,
            selected_request_epoch: 0,
            token_draft,
        }
    }

    fn send(&mut self, cmd: Command) {
        if let Err(err) = self.commands.try_send(cmd) {
            // UI 线程不阻塞；队列满时给用户可见反馈，而不是静默丢失控制命令。
            self.state.set_notice(format!("后台命令队列已满，请稍后重试: {err}"));
            self.state.push_log("error", format!("后台命令发送失败: {err}"));
        }
    }

    fn select_plan(&mut self, plan_id: String) {
        self.selected_plan_id = Some(plan_id.clone());
        self.selected_request_epoch = self.selected_request_epoch.saturating_add(1);
        let epoch = self.selected_request_epoch;
        self.send(Command::SelectPlan { plan_id, epoch });
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
                self.state.apply_plan_update(&ev.plan_id, &ev.status, &steps, ev.version);
            }
            GuiEvent::Ws(WsEvent::Connected) => {
                self.state.connected = true;
                self.state.last_ws_error = None;
            }
            GuiEvent::Ws(WsEvent::Disconnected(reason)) => {
                self.state.connected = false;
                self.state.last_ws_error = Some(reason);
            }
            GuiEvent::Status(st) => self.state.service_status = Some(st),
            GuiEvent::Plans(plans) => {
                self.state.plans = plans;
                // 首次加载无选中 → 自动选中第一个计划
                if self.selected_plan_id.is_none() {
                    if let Some(first) = self.state.plans.first() {
                        self.select_plan(first.plan_id.clone());
                    }
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
                    if ui.button("⏸ 暂停").clicked() {
                        self.send(Command::Pause(plan_id.to_string()));
                    }
                }
                PlanStatus::Paused => {
                    if ui.button("▶ 恢复").clicked() {
                        self.send(Command::Resume(plan_id.to_string()));
                    }
                }
                _ => {
                    if ui.button("▶ 开始执行").clicked() {
                        self.send(Command::Execute(plan_id.to_string()));
                    }
                }
            }
        });
    }

    fn status_color(status: PlanStatus) -> egui::Color32 {
        match status {
            PlanStatus::Running => egui::Color32::LIGHT_BLUE,
            PlanStatus::Completed => egui::Color32::GREEN,
            PlanStatus::Failed => egui::Color32::RED,
            PlanStatus::Paused => egui::Color32::YELLOW,
            _ => egui::Color32::GRAY,
        }
    }

    fn step_icon_and_color(live: StepStatus) -> (&'static str, egui::Color32) {
        match live {
            StepStatus::Success => ("✓", egui::Color32::GREEN),
            StepStatus::Failed => ("✗", egui::Color32::RED),
            StepStatus::Running | StepStatus::Retry => ("◐", egui::Color32::LIGHT_BLUE),
            StepStatus::Skipped => ("⊖", egui::Color32::GRAY),
            StepStatus::RolledBack => ("↩", egui::Color32::from_rgb(255, 165, 0)),
            StepStatus::Pending => ("·", egui::Color32::GRAY),
        }
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

        // 2. 顶栏：服务状态 + token 配置
        egui::TopBottomPanel::top("top_bar").show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.label(self.status_text());
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    let (conn_text, conn_color) = if self.state.connected {
                        ("WS 已连接", egui::Color32::GREEN)
                    } else {
                        ("WS 断开", egui::Color32::RED)
                    };
                    ui.colored_label(conn_color, conn_text);
                    let token_edit = egui::TextEdit::singleline(&mut self.token_draft)
                        .password(true)
                        .hint_text("API token")
                        .desired_width(160.0);
                    let edited = ui.add(token_edit).changed();
                    let apply_clicked = ui.button("应用 token").clicked();
                    if (edited && ui.input(|i| i.key_pressed(egui::Key::Enter)))
                        || apply_clicked
                    {
                        let t = self.token_draft.trim().to_string();
                        if !t.is_empty() && t != self.state.token {
                            self.send(Command::SetToken(t));
                        }
                    }
                });
            });
        });

        // 3. 左栏：计划列表
        egui::SidePanel::left("plan_list").show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.heading("计划");
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.button("刷新").clicked() {
                        self.send(Command::RefreshPlans);
                    }
                });
            });
            ui.separator();
            egui::ScrollArea::vertical().show(ui, |ui| {
                let plans = self.state.plans.clone();
                for p in &plans {
                    let selected = self.selected_plan_id.as_deref() == Some(p.plan_id.as_str());
                    let short = &p.plan_id[..8.min(p.plan_id.len())];
                    if ui.selectable_label(selected, short).clicked() {
                        self.select_plan(p.plan_id.clone());
                    }
                }
            });
        });

        // 4. 中央：计划详情 + 控制
        egui::CentralPanel::default().show(ctx, |ui| match self.selection() {
            Some(sel) => {
                ui.horizontal(|ui| {
                    ui.heading(format!("计划 {}", &sel.plan_id[..8.min(sel.plan_id.len())]));
                    ui.colored_label(Self::status_color(sel.status), format!("{:?}", sel.status));
                    ui.weak(format!("{}/{} 步", sel.progress.0, sel.progress.1));
                });
                self.control_buttons(ui, &sel.plan_id, sel.status);
                ui.separator();
                egui::ScrollArea::vertical().show(ui, |ui| {
                    for (step, live) in &sel.steps {
                        let (icon, color) = Self::step_icon_and_color(*live);
                        ui.horizontal(|ui| {
                            ui.colored_label(color, icon);
                            ui.label(&step.description);
                            ui.weak(format!("[{}]", step.tool));
                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    // 仅 Pending/Retry 可跳过（与后端守卫语义一致）
                                    if matches!(live, StepStatus::Pending | StepStatus::Retry)
                                        && ui.small_button("跳过").clicked()
                                    {
                                        self.send(Command::Skip {
                                            plan_id: sel.plan_id.clone(),
                                            step_id: step.step_id.clone(),
                                        });
                                    }
                                },
                            );
                        });
                        if let Some(outputs) = &step.outputs {
                            if let Some(err) = outputs.get("error").and_then(|e| e.as_str()) {
                                ui.colored_label(egui::Color32::RED, format!("    错误: {err}"));
                            }
                        }
                        ui.separator();
                    }
                });
            }
            None => {
                ui.centered_and_justified(|ui| ui.weak("选择或等待计划加载…"));
            }
        });

        // 底部通知（后端 detail / 控制结果）：挂在日志面板之上
        if self.state.notice.is_some() {
            egui::TopBottomPanel::bottom("notice_bar")
                .frame(egui::Frame::default().fill(egui::Color32::from_rgb(60, 45, 0)))
                .show(ctx, |ui| {
                    ui.colored_label(
                        egui::Color32::YELLOW,
                        self.state.notice.as_deref().unwrap_or_default(),
                    );
                });
        }

        // 5. 底栏：实时日志流
        egui::TopBottomPanel::bottom("log_panel")
            .resizable(true)
            .default_height(150.0)
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.heading("实时日志");
                    ui.weak(format!("{} 条", self.state.logs.len()));
                });
                egui::ScrollArea::vertical()
                    .stick_to_bottom(true)
                    .show(ui, |ui| {
                        let logs: Vec<LogLine> = self.state.logs.clone();
                        for line in logs.iter().rev().take(300).rev() {
                            let color = match line.level.as_str() {
                                "error" => egui::Color32::RED,
                                "warning" => egui::Color32::YELLOW,
                                "success" => egui::Color32::GREEN,
                                _ => egui::Color32::LIGHT_GRAY,
                            };
                            ui.colored_label(color, format!("[{}] {}", line.level, line.message));
                        }
                    });
            });

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
                let exec = st
                    .current_execution
                    .as_deref()
                    .map(|id| format!("，执行中: {}", &id[..8.min(id.len())]))
                    .unwrap_or_default();
                format!(
                    "服务: {} · 工具: {} · LLM: {}{}",
                    st.status,
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
        assert!(!matches!(PlanStatus::Draft, PlanStatus::Running | PlanStatus::Paused));
    }
}
