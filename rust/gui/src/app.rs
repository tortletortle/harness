//! eframe 应用：四面板布局（设计稿 §20.2）。
//! UI 线程零阻塞：网络全部在后台线程，事件经 channel 驱动重绘。

use crate::state::{GuiState, LogLine};
use crate::types::{PlanStatus, StepStatus, StepView};
use crate::ws::WsEvent;
use std::collections::HashMap;
use tokio::sync::mpsc;

/// UI → 后台命令
#[derive(Debug)]
pub enum Command {
    RefreshPlans,
    SelectPlan(String),
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
    PlanDetail(Box<crate::types::PlanDetail>),
    Notice(String),
    Error(String),
    ControlResult(Result<String, String>),
    TokenApplied,
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
    events: mpsc::UnboundedReceiver<GuiEvent>,
    commands: mpsc::UnboundedSender<Command>,
    selected_plan_id: Option<String>,
    token_draft: String,
}

impl EasApp {
    pub fn new(
        state: GuiState,
        events: mpsc::UnboundedReceiver<GuiEvent>,
        commands: mpsc::UnboundedSender<Command>,
    ) -> Self {
        let token_draft = state.token.clone();
        Self {
            state,
            events,
            commands,
            selected_plan_id: None,
            token_draft,
        }
    }

    fn send(&self, cmd: Command) {
        let _ = self.commands.send(cmd);
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
                        let id = first.plan_id.clone();
                        self.selected_plan_id = Some(id.clone());
                        self.send(Command::SelectPlan(id));
                    }
                }
            }
            GuiEvent::PlanDetail(detail) => {
                self.selected_plan_id = Some(detail.plan_id.clone());
                self.state.set_plan_detail(*detail);
            }
            GuiEvent::Notice(msg) => self.state.set_notice(msg),
            GuiEvent::Error(msg) => {
                self.state.push_log("error", msg.clone());
                self.state.set_notice(msg);
            }
            GuiEvent::ControlResult(Ok(msg)) => {
                self.state.set_notice(msg);
                self.send(Command::RefreshStatus);
            }
            GuiEvent::ControlResult(Err(e)) => {
                // 409/404 等冲突：状态栏显示后端中文 detail（设计稿 §20.3）
                self.state.set_notice(e.to_string());
                self.state.push_log("error", e.to_string());
            }
            GuiEvent::TokenApplied => {
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

    fn control_buttons(&self, ui: &mut egui::Ui, plan_id: &str, status: PlanStatus) {
        ui.horizontal(|ui| {
            match status {
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
                            self.state.token = t.clone();
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
                        self.selected_plan_id = Some(p.plan_id.clone());
                        self.send(Command::SelectPlan(p.plan_id.clone()));
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
