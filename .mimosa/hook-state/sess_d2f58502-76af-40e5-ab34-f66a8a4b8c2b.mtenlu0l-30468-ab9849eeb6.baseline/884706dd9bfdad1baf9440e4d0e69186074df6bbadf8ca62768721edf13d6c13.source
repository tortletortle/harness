//! EAS 2.0 桌面控制台入口（设计稿 §20）。
//!
//! 架构：eframe UI 线程 + tokio 后台线程（REST 轮询 / WS 订阅 / 命令执行），
//! 有界 channel 桥接；后台线程在每次事件转发后 request_repaint。

mod api;
mod app;
mod state;
mod types;
mod ws;

use app::{Command, GuiEvent};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{mpsc, watch, Mutex, RwLock};

const CHANNEL_CAPACITY: usize = 256;

fn env_or(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

/// 转发事件并唤醒 UI。日志在 UI 队列满时跳过，控制/状态/计划事件等待空间，
/// 避免关键事件静默丢失。
async fn forward_event(
    events_tx: &mpsc::Sender<GuiEvent>,
    ctx: &egui::Context,
    event: GuiEvent,
) -> bool {
    let sent = if matches!(&event, GuiEvent::Ws(ws::WsEvent::Log { .. })) {
        match events_tx.try_send(event) {
            Ok(()) | Err(mpsc::error::TrySendError::Full(_)) => true,
            Err(mpsc::error::TrySendError::Closed(_)) => false,
        }
    } else {
        events_tx.send(event).await.is_ok()
    };
    ctx.request_repaint();
    sent
}

/// 后台线程主体：WS 订阅 + 30s 兜底轮询 + 命令执行。
/// 每转发一条事件都 request_repaint 唤醒 UI（egui 即时模式只在有输入/重绘请求时渲染）。
async fn supervisor(
    base: String,
    token: String,
    events_tx: mpsc::Sender<GuiEvent>,
    mut commands_rx: mpsc::Receiver<Command>,
    ctx: egui::Context,
) {
    let api = match api::ApiClient::new(base, token) {
        Ok(api) => Arc::new(api),
        Err(e) => {
            let _ = forward_event(&events_tx, &ctx, GuiEvent::Error(e.to_string())).await;
            return;
        }
    };

    // 当前选择供轮询任务读取；epoch 使轮询响应也受 UI 选择围栏保护。
    let selected = Arc::new(RwLock::new(None::<(String, u64)>));
    // 所有详情请求共享一个锁，避免轮询、控制刷新、选择刷新并发打爆后端。
    let detail_fetch_lock = Arc::new(Mutex::new(()));

    // WS 事件内部通道 → 统一转发到 UI（有界，日志可跳过）。
    // WS manager 监听运行时 config generation，在 token/base 成功变更后
    // 取消旧连接并以新快照启动；认证拒绝则由 run_ws 终止且不重试。
    let (ws_tx, mut ws_rx) = mpsc::channel::<ws::WsEvent>(CHANNEL_CAPACITY);
    spawn_ws_manager(api.clone(), ws_tx);

    // 事件转发 + 重绘唤醒（所有后台事件走这里）
    let forward_tx = events_tx.clone();
    let forward_ctx = ctx.clone();
    tokio::spawn(async move {
        while let Some(ev) = ws_rx.recv().await {
            if !forward_event(&forward_tx, &forward_ctx, GuiEvent::Ws(ev)).await {
                // UI 已退出，事件泵无需继续占用任务。
                break;
            }
        }
    });

    // 30s 兜底轮询：状态、计划列表、当前选中计划详情全部刷新。
    // fetch 顺序执行，下一轮不会在上一轮完成前启动，避免重复堆积。
    {
        let poll_api = api.clone();
        let poll_selected = selected.clone();
        let poll_lock = detail_fetch_lock.clone();
        let poll_tx = events_tx.clone();
        let poll_ctx = ctx.clone();
        tokio::spawn(async move {
            let fetch = || async {
                if let Ok(st) = poll_api.get_status().await {
                    forward_event(&poll_tx, &poll_ctx, GuiEvent::Status(st)).await;
                }
                if let Ok(plans) = poll_api.get_plans(20).await {
                    forward_event(&poll_tx, &poll_ctx, GuiEvent::Plans(plans)).await;
                }
                if let Ok(designs) = poll_api.get_designs(20).await {
                    forward_event(&poll_tx, &poll_ctx, GuiEvent::Designs { designs, epoch: 0 })
                        .await;
                }
                // Approval notifications normally arrive via WS; polling closes the
                // reconnect/startup gap and also recovers events missed while offline.
                if let Ok(approvals) = poll_api.get_pending_approvals(None).await {
                    forward_event(
                        &poll_tx,
                        &poll_ctx,
                        GuiEvent::Approvals {
                            approvals,
                            epoch: 0,
                        },
                    )
                    .await;
                }
                let current = poll_selected.read().await.clone();
                if let Some((plan_id, epoch)) = current {
                    let _guard = poll_lock.lock().await;
                    if let Ok(detail) = poll_api.get_plan(&plan_id).await {
                        forward_event(
                            &poll_tx,
                            &poll_ctx,
                            GuiEvent::PlanDetail {
                                detail: Box::new(detail),
                                epoch,
                            },
                        )
                        .await;
                    }
                }
            };
            fetch().await;
            let mut tick = tokio::time::interval(Duration::from_secs(30));
            // interval 的第一次 tick 立即返回；跳过它，启动拉取已在上面完成。
            tick.tick().await;
            loop {
                tick.tick().await;
                fetch().await;
            }
        });
    }

    // 命令处理：单一后台消费者顺序处理，避免每次刷新 spawn 无界任务。
    while let Some(cmd) = commands_rx.recv().await {
        match cmd {
            Command::RefreshStatus => {
                if let Ok(st) = api.get_status().await {
                    forward_event(&events_tx, &ctx, GuiEvent::Status(st)).await;
                }
            }
            Command::RefreshPlans => {
                if let Ok(plans) = api.get_plans(20).await {
                    forward_event(&events_tx, &ctx, GuiEvent::Plans(plans)).await;
                }
            }
            Command::RefreshDesigns { epoch } => match api.get_designs(20).await {
                Ok(designs) => {
                    forward_event(&events_tx, &ctx, GuiEvent::Designs { designs, epoch }).await;
                }
                Err(error) => {
                    forward_event(
                        &events_tx,
                        &ctx,
                        GuiEvent::DesignError {
                            message: format!("获取设计书列表失败: {error}"),
                            epoch,
                            action: app::DesignErrorAction::List,
                        },
                    )
                    .await;
                }
            },
            Command::SelectDesign { design_id, epoch } => match api.get_design(&design_id).await {
                Ok(design) => {
                    forward_event(
                        &events_tx,
                        &ctx,
                        GuiEvent::DesignDetail {
                            design: Box::new(design),
                            epoch,
                        },
                    )
                    .await;
                }
                Err(error) => {
                    forward_event(
                        &events_tx,
                        &ctx,
                        GuiEvent::DesignError {
                            message: format!("获取设计书失败: {error}"),
                            epoch,
                            action: app::DesignErrorAction::Detail,
                        },
                    )
                    .await;
                }
            },
            Command::GenerateDesign {
                user_request,
                epoch,
            } => {
                let design_api = api.clone();
                let design_tx = events_tx.clone();
                let design_ctx = ctx.clone();
                tokio::spawn(async move {
                    let result = design_api.create_design(&user_request).await;
                    match result {
                        Ok(design) => {
                            forward_event(
                                &design_tx,
                                &design_ctx,
                                GuiEvent::DesignGenerated {
                                    design: Box::new(design),
                                    epoch,
                                },
                            )
                            .await;
                        }
                        Err(error) => {
                            forward_event(
                                &design_tx,
                                &design_ctx,
                                GuiEvent::DesignError {
                                    message: format!("生成设计书失败: {error}"),
                                    epoch,
                                    action: app::DesignErrorAction::Generate,
                                },
                            )
                            .await;
                        }
                    }
                });
            }
            Command::ApproveDesign {
                design_id,
                approved,
                feedback,
                epoch,
            } => {
                let design_api = api.clone();
                let design_tx = events_tx.clone();
                let design_ctx = ctx.clone();
                tokio::spawn(async move {
                    let result = design_api
                        .approve_design(&design_id, approved, feedback.as_deref())
                        .await
                        .map_err(|error| error.to_string());
                    forward_event(
                        &design_tx,
                        &design_ctx,
                        GuiEvent::DesignApprovalResult {
                            design_id,
                            approved,
                            result,
                            epoch,
                        },
                    )
                    .await;
                });
            }
            Command::SelectPlan { plan_id, epoch }
            | Command::RefreshPlanDetail { plan_id, epoch } => {
                *selected.write().await = Some((plan_id.clone(), epoch));
                let _guard = detail_fetch_lock.lock().await;
                match api.get_plan(&plan_id).await {
                    Ok(detail) => {
                        forward_event(
                            &events_tx,
                            &ctx,
                            GuiEvent::PlanDetail {
                                detail: Box::new(detail),
                                epoch,
                            },
                        )
                        .await;
                    }
                    Err(e) => {
                        forward_event(&events_tx, &ctx, GuiEvent::Error(e.to_string())).await;
                    }
                }
            }
            Command::RefreshApprovals { plan_id, epoch } => {
                match api.get_pending_approvals(plan_id.as_deref()).await {
                    Ok(approvals) => {
                        forward_event(&events_tx, &ctx, GuiEvent::Approvals { approvals, epoch })
                            .await;
                    }
                    Err(error) => {
                        forward_event(
                            &events_tx,
                            &ctx,
                            GuiEvent::ApprovalsError {
                                message: error.to_string(),
                                epoch,
                            },
                        )
                        .await;
                    }
                }
            }
            Command::Approve {
                plan_id,
                approval_id,
                comment,
            } => {
                let approval_api = api.clone();
                let approval_tx = events_tx.clone();
                let approval_ctx = ctx.clone();
                tokio::spawn(async move {
                    let result = approval_api
                        .approve_tool_call(&plan_id, &approval_id, comment.as_deref())
                        .await
                        .map_err(|error| error.to_string());
                    forward_event(
                        &approval_tx,
                        &approval_ctx,
                        GuiEvent::ApprovalResult {
                            approval_id,
                            result,
                        },
                    )
                    .await;
                });
            }
            Command::Reject {
                plan_id,
                approval_id,
                comment,
            } => {
                let approval_api = api.clone();
                let approval_tx = events_tx.clone();
                let approval_ctx = ctx.clone();
                tokio::spawn(async move {
                    let result = approval_api
                        .reject_tool_call(&plan_id, &approval_id, comment.as_deref())
                        .await
                        .map_err(|error| error.to_string());
                    forward_event(
                        &approval_tx,
                        &approval_ctx,
                        GuiEvent::ApprovalResult {
                            approval_id,
                            result,
                        },
                    )
                    .await;
                });
            }
            Command::RefreshAudit {
                plan_id,
                limit,
                epoch,
            } => match api.get_audit_logs(limit, plan_id.as_deref()).await {
                Ok(logs) => {
                    forward_event(&events_tx, &ctx, GuiEvent::AuditLogs { logs, epoch }).await;
                }
                Err(error) => {
                    forward_event(
                        &events_tx,
                        &ctx,
                        GuiEvent::AuditError {
                            message: error.to_string(),
                            epoch,
                        },
                    )
                    .await;
                }
            },
            Command::Rollback {
                plan_id,
                step_id,
                strategy,
                epoch,
            } => {
                let result = api
                    .rollback_plan(&plan_id, &step_id, &strategy)
                    .await
                    .map_err(|error| error.to_string());
                forward_event(
                    &events_tx,
                    &ctx,
                    GuiEvent::RollbackResult {
                        plan_id,
                        result,
                        epoch,
                    },
                )
                .await;
            }
            Command::Execute(plan_id) => {
                let result = api.execute_plan(&plan_id).await.map_err(|e| e.to_string());
                forward_event(
                    &events_tx,
                    &ctx,
                    GuiEvent::ControlResult { plan_id, result },
                )
                .await;
            }
            Command::Pause(plan_id) => {
                let result = api.pause_plan(&plan_id).await.map_err(|e| e.to_string());
                forward_event(
                    &events_tx,
                    &ctx,
                    GuiEvent::ControlResult { plan_id, result },
                )
                .await;
            }
            Command::Resume(plan_id) => {
                let result = api.resume_plan(&plan_id).await.map_err(|e| e.to_string());
                forward_event(
                    &events_tx,
                    &ctx,
                    GuiEvent::ControlResult { plan_id, result },
                )
                .await;
            }
            Command::Skip { plan_id, step_id } => {
                let result = api
                    .skip_step(&plan_id, &step_id)
                    .await
                    .map_err(|e| e.to_string());
                forward_event(
                    &events_tx,
                    &ctx,
                    GuiEvent::ControlResult { plan_id, result },
                )
                .await;
            }
            Command::SetToken(new_token) => {
                // 校验使用现有 API base；只有成功后 REST token 才替换。
                // WS manager 观察 generation 并负责重启连接。
                match api.validate_and_set_token(new_token).await {
                    Ok(config) => {
                        forward_event(&events_tx, &ctx, GuiEvent::TokenApplied(config.token)).await;
                    }
                    Err(e) => {
                        forward_event(
                            &events_tx,
                            &ctx,
                            GuiEvent::Error(format!("token 校验失败: {e}")),
                        )
                        .await;
                    }
                }
            }
        }
    }
}

/// 监听运行时 API 配置，保证同一时刻只有一个 WS 订阅任务。
/// token/base 只有在 REST 配置变更成功后才会触发 generation 更新。
fn spawn_ws_manager(api: Arc<api::ApiClient>, ws_tx: mpsc::Sender<ws::WsEvent>) {
    let mut config_rx = api.subscribe_config_changes();
    tokio::spawn(async move {
        let mut cancel_tx = None::<watch::Sender<bool>>;
        let mut running_generation = None::<u64>;
        loop {
            let config = api.config();
            if running_generation != Some(config.generation) {
                if let Some(previous) = cancel_tx.take() {
                    let _ = previous.send(true);
                }
                let (next_cancel_tx, next_cancel_rx) = watch::channel(false);
                cancel_tx = Some(next_cancel_tx);
                running_generation = Some(config.generation);
                let run_tx = ws_tx.clone();
                tokio::spawn(async move {
                    let _ = ws::run_ws(config.base, config.token, run_tx, next_cancel_rx).await;
                });
            }
            if config_rx.changed().await.is_err() {
                if let Some(cancel) = cancel_tx {
                    let _ = cancel.send(true);
                }
                return;
            }
        }
    });
}

/// 加载系统中文字体（egui 默认字体无 CJK 字形）。
/// 找不到字体文件时静默跳过——界面退化为方块但不崩溃。
fn load_cjk_font(ctx: &egui::Context) {
    let candidates = [
        "C:/Windows/Fonts/msyh.ttc",   // 微软雅黑
        "C:/Windows/Fonts/simhei.ttf", // 黑体
        "C:/Windows/Fonts/simsun.ttc", // 宋体
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ];
    for path in candidates {
        if let Ok(bytes) = std::fs::read(path) {
            let mut fonts = egui::FontDefinitions::default();
            fonts
                .font_data
                .insert("cjk".into(), egui::FontData::from_owned(bytes).into());
            if let Some(family) = fonts.families.get_mut(&egui::FontFamily::Proportional) {
                family.insert(0, "cjk".into());
            }
            if let Some(family) = fonts.families.get_mut(&egui::FontFamily::Monospace) {
                family.insert(0, "cjk".into());
            }
            ctx.set_fonts(fonts);
            return;
        }
    }
}

fn main() -> eframe::Result<()> {
    let base = env_or("EAS_API_BASE", "http://127.0.0.1:8000");
    let token = env_or("EAS_API_TOKEN", "");

    let (events_tx, events_rx) = tokio::sync::mpsc::channel::<GuiEvent>(CHANNEL_CAPACITY);
    let (cmd_tx, cmd_rx) = tokio::sync::mpsc::channel::<Command>(CHANNEL_CAPACITY);

    let native_options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1100.0, 720.0])
            .with_title("EAS 2.0 桌面控制台"),
        ..Default::default()
    };

    eframe::run_native(
        "EAS 2.0 桌面控制台",
        native_options,
        Box::new(move |cc| {
            // 后台线程 + tokio 运行时（cc 提供 UI Context 用于重绘唤醒）
            let ctx = cc.egui_ctx.clone();
            load_cjk_font(&ctx);
            let initial_state = state::GuiState {
                token: token.clone(),
                ..Default::default()
            };
            let thread_base = base.clone();
            let thread_token = token.clone();
            std::thread::spawn(move || {
                let rt = tokio::runtime::Builder::new_multi_thread()
                    .enable_all()
                    .build()
                    .expect("tokio runtime");
                rt.block_on(supervisor(
                    thread_base,
                    thread_token,
                    events_tx,
                    cmd_rx,
                    ctx,
                ));
            });

            Ok(Box::new(app::EasApp::new(initial_state, events_rx, cmd_tx)))
        }),
    )
}
