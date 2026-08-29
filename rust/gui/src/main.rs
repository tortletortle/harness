//! EAS 2.0 桌面控制台入口（设计稿 §20）。
//!
//! 架构：eframe UI 线程 + tokio 后台线程（REST 轮询 / WS 订阅 / 命令执行），
//! 两条 unbounded channel 桥接；后台线程在每次事件转发后 request_repaint。

mod api;
mod app;
mod state;
mod types;
mod ws;

use app::{Command, GuiEvent};
use std::time::Duration;
use tokio::sync::mpsc;

fn env_or(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

/// 后台线程主体：WS 订阅 + 30s 兜底轮询 + 命令执行。
/// 每转发一条事件都 request_repaint 唤醒 UI（egui 即时模式只在有输入/重绘请求时渲染）。
async fn supervisor(
    base: String,
    token: String,
    events_tx: mpsc::UnboundedSender<GuiEvent>,
    mut commands_rx: mpsc::UnboundedReceiver<Command>,
    ctx: egui::Context,
) {
    let api = std::sync::Arc::new(api::ApiClient::new(base.clone(), token.clone()));

    // WS 事件内部通道 → 统一转发到 UI（带重绘唤醒）
    let (ws_tx, mut ws_rx) = mpsc::unbounded_channel::<ws::WsEvent>();
    {
        let events_tx = events_tx.clone();
        let ctx = ctx.clone();
        tokio::spawn(async move {
            // run_ws 只在 UI 退出或认证被拒时返回
            ws::run_ws(base, token, ws_tx).await;
            drop(events_tx);
            drop(ctx);
        });
    }

    // 事件转发 + 重绘唤醒（所有后台事件走这里）
    let forward = {
        let events_tx = events_tx.clone();
        let ctx = ctx.clone();
        move |ev: GuiEvent| {
            let _ = events_tx.send(ev);
            ctx.request_repaint();
        }
    };

    // WS 事件泵
    {
        let forward = forward.clone();
        let ctx = ctx.clone();
        tokio::spawn(async move {
            while let Some(ev) = ws_rx.recv().await {
                forward(GuiEvent::Ws(ev));
                let _ = ctx;
            }
        });
    }

    // 30s 兜底轮询 + 启动即拉取（设计稿 §20.3）
    {
        let poll_api = api.clone();
        let forward = forward.clone();
        tokio::spawn(async move {
            let fetch = || async {
                if let Ok(st) = poll_api.get_status().await {
                    forward(GuiEvent::Status(st));
                }
                if let Ok(plans) = poll_api.get_plans(20).await {
                    forward(GuiEvent::Plans(plans));
                }
            };
            fetch().await;
            let mut tick = tokio::time::interval(Duration::from_secs(30));
            loop {
                tick.tick().await;
                fetch().await;
            }
        });
    }

    // 命令处理：UI 的控制/选择请求
    while let Some(cmd) = commands_rx.recv().await {
        let api = api.clone();
        let forward = forward.clone();
        tokio::spawn(async move {
            match cmd {
                Command::RefreshStatus => {
                    if let Ok(st) = api.get_status().await {
                        forward(GuiEvent::Status(st));
                    }
                }
                Command::RefreshPlans => {
                    if let Ok(plans) = api.get_plans(20).await {
                        forward(GuiEvent::Plans(plans));
                    }
                }
                Command::SelectPlan(plan_id) => match api.get_plan(&plan_id).await {
                    Ok(detail) => forward(GuiEvent::PlanDetail(Box::new(detail))),
                    Err(e) => forward(GuiEvent::Error(e.to_string())),
                },
                Command::Execute(plan_id) => {
                    let r = api.execute_plan(&plan_id).await;
                    forward(GuiEvent::ControlResult(r.map_err(|e| e.to_string())));
                }
                Command::Pause(plan_id) => {
                    let r = api.pause_plan(&plan_id).await;
                    forward(GuiEvent::ControlResult(r.map_err(|e| e.to_string())));
                }
                Command::Resume(plan_id) => {
                    let r = api.resume_plan(&plan_id).await;
                    forward(GuiEvent::ControlResult(r.map_err(|e| e.to_string())));
                }
                Command::Skip { plan_id, step_id } => {
                    let r = api.skip_step(&plan_id, &step_id).await;
                    forward(GuiEvent::ControlResult(r.map_err(|e| e.to_string())));
                }
                Command::SetToken(new_token) => {
                    // token 变更：以新 token 校验一次并通知 UI。
                    // WS 重连需重启 supervisor——简化为提示用户重启应用（阶段 2 改进项）。
                    let probe = api::ApiClient::new(String::new(), new_token.clone());
                    match probe.get_status().await {
                        Ok(_) => forward(GuiEvent::TokenApplied),
                        Err(e) => {
                            forward(GuiEvent::Error(format!("token 校验失败: {e}")))
                        }
                    }
                }
            }
        });
    }
}

/// 加载系统中文字体（egui 默认字体无 CJK 字形）。
/// 找不到字体文件时静默跳过——界面退化为方块但不崩溃。
fn load_cjk_font(ctx: &egui::Context) {
    let candidates = [
        "C:/Windows/Fonts/msyh.ttc",     // 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",   // 黑体
        "C:/Windows/Fonts/simsun.ttc",   // 宋体
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ];
    for path in candidates {
        if let Ok(bytes) = std::fs::read(path) {
            let mut fonts = egui::FontDefinitions::default();
            fonts.font_data.insert(
                "cjk".into(),
                egui::FontData::from_owned(bytes).into(),
            );
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

    let (events_tx, events_rx) = tokio::sync::mpsc::unbounded_channel::<GuiEvent>();
    let (cmd_tx, cmd_rx) = tokio::sync::mpsc::unbounded_channel::<Command>();

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
            std::thread::spawn(move || {
                let rt = tokio::runtime::Builder::new_multi_thread()
                    .enable_all()
                    .build()
                    .expect("tokio runtime");
                rt.block_on(supervisor(
                    base, token, events_tx, cmd_rx, ctx,
                ));
            });

            Ok(Box::new(app::EasApp::new(initial_state, events_rx, cmd_tx)))
        }),
    )
}
