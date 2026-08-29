//! WebSocket 日志与 plan_update 订阅（tokio-tungstenite）。
//!
//! 断线重连：指数退避 1s→2s→…→30s 封顶；服务端拒绝（1008，token 无效）
//! 时遵循 Web 端语义——不再重连（重连只会无限失败）。
//! 事件经 mpsc 通道送 UI 线程（设计稿 §20.2）。

use crate::types::PlanUpdateEvent;
use futures_util::{SinkExt, StreamExt};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;
#[derive(Debug, Clone)]
pub enum WsEvent {
    Log { level: String, message: String },
    PlanUpdate(PlanUpdateEvent),
    Connected,
    Disconnected(String),
}

/// ws://host:port/ws/logs?token=...
fn ws_url(base: &str, token: &str) -> String {
    let base = base.trim_end_matches('/');
    let base = base
        .strip_prefix("https://")
        .map(|rest| format!("wss://{rest}"))
        .unwrap_or_else(|| {
            base.strip_prefix("http://")
                .map(|rest| format!("ws://{rest}"))
                .unwrap_or_else(|| format!("ws://{base}"))
        });
    if token.is_empty() {
        format!("{base}/ws/logs")
    } else {
        format!("{base}/ws/logs?token={}", urlencode(token))
    }
}

fn urlencode(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// 常驻订阅循环：断开后按指数退避重连，直到通道关闭。
pub async fn run_ws(base: String, token: String, tx: mpsc::UnboundedSender<WsEvent>) {
    let mut backoff_secs: u64 = 1;
    loop {
        let url = ws_url(&base, &token);
        let connect = tokio_tungstenite::connect_async(&url).await;
        match connect {
            Err(e) => {
                let _ = tx
                    .send(WsEvent::Disconnected(format!("WS 连接失败: {e}")));
            }
            Ok((mut ws, _)) => {
                backoff_secs = 1; // 连接成功，重置退避
                let _ = tx.send(WsEvent::Connected);
                let mut errored = None;
                while let Some(msg) = ws.next().await {
                    match msg {
                        Ok(Message::Text(text)) => {
                            if let Some(ev) = parse_frame(&text) {
                                if tx.send(ev).is_err() {
                                    return; // UI 已退出
                                }
                            }
                        }
                        Ok(Message::Ping(p)) => {
                            let _ = ws.send(Message::Pong(p)).await;
                        }
                        Ok(_) => {}
                        Err(e) => {
                            errored = Some(format!("WS 断开: {e}"));
                            break;
                        }
                    }
                }
                let reason = errored.unwrap_or_else(|| "WS 关闭".to_string());
                let _ = tx.send(WsEvent::Disconnected(reason.clone()));
                // token 无效（1008）不重连，与 Web 端语义一致
                if reason.contains("1008") {
                    let _ = tx
                        .send(WsEvent::Disconnected("认证被拒（token 无效），停止重连".into()));
                    return;
                }
            }
        }
        if tx.is_closed() {
            return;
        }
        tokio::time::sleep(std::time::Duration::from_secs(backoff_secs)).await;
        backoff_secs = (backoff_secs * 2).min(30);
    }
}

/// 解析一帧：type=log → Log；type=plan_update → PlanUpdate。其余忽略。
fn parse_frame(text: &str) -> Option<WsEvent> {
    let v: serde_json::Value = serde_json::from_str(text).ok()?;
    match v.get("type")?.as_str()? {
        "log" => Some(WsEvent::Log {
            level: v
                .get("level")
                .and_then(|l| l.as_str())
                .unwrap_or("info")
                .to_string(),
            message: v
                .get("message")
                .and_then(|m| m.as_str())
                .unwrap_or_default()
                .to_string(),
        }),
        "plan_update" => {
            let ev: PlanUpdateEvent = serde_json::from_value(v).ok()?;
            Some(WsEvent::PlanUpdate(ev))
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ws_url_builds_query_token() {
        assert_eq!(
            ws_url("http://127.0.0.1:8000", "t o/k"),
            "ws://127.0.0.1:8000/ws/logs?token=t%20o%2Fk"
        );
        assert_eq!(
            ws_url("http://127.0.0.1:8000/", ""),
            "ws://127.0.0.1:8000/ws/logs"
        );
    }

    #[test]
    fn parse_log_and_plan_update_frames() {
        let log = parse_frame(r#"{"type":"log","level":"error","message":" boom "}"#).unwrap();
        match log {
            WsEvent::Log { level, message } => {
                assert_eq!(level, "error");
                assert_eq!(message.trim(), "boom");
            }
            _ => panic!("应为 Log"),
        }
        let pu = parse_frame(
            r#"{"type":"plan_update","plan_id":"p1","status":"Running","steps":[{"step_id":"s1","status":"Running"}],"version":7}"#,
        )
        .unwrap();
        match pu {
            WsEvent::PlanUpdate(ev) => {
                assert_eq!(ev.plan_id, "p1");
                assert_eq!(ev.version, 7);
            }
            _ => panic!("应为 PlanUpdate"),
        }
        assert!(parse_frame(r#"{"type":"unknown"}"#).is_none());
        assert!(parse_frame("not json").is_none());
    }
}
