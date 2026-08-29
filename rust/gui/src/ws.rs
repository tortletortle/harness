//! WebSocket 日志与 plan_update 订阅（tokio-tungstenite）。
//!
//! 断开原因分类：Intentional（本次有意重连，如 token 更新）、Cancelled（被调用方
//! cancel 信号中止，不算错误）、Normal（服务端正常断连）、AuthRejected（服务端
//! 拒绝，token 无效，不再重连）、Error（网络/协议错误，按指数退避重连）。
//! 事件经有界 mpsc 通道送 UI 线程（设计稿 §20.2）。

use crate::types::{ApprovalRequest, ApprovalResolved, PlanUpdateEvent};
use futures_util::{SinkExt, StreamExt};
use std::time::Duration;
use tokio::sync::{mpsc, watch};
use tokio_tungstenite::tungstenite::{
    protocol::frame::coding::CloseCode, Error as WsError, Message,
};

/// WS 断开分类（供 UI 提示与重连决策）。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DisconnectKind {
    /// 有意重连（token 已更新，旧连接被取消）。
    Intentional,
    /// 调用方通过 cancel 信号请求中止，非错误。
    Cancelled,
    /// 服务端走关闭握手正常断连。
    Normal,
    /// 服务端拒绝（关闭码 1008 = policy violation，token 无效），停止重连。
    AuthRejected,
    /// 网络/协议错误等，按指数退避重连。
    Error,
}

/// 是否应继续尝试重连。
fn should_reconnect(kind: DisconnectKind) -> bool {
    !matches!(
        kind,
        DisconnectKind::Intentional | DisconnectKind::Cancelled | DisconnectKind::AuthRejected
    )
}

fn classify_close(reason: Option<&str>, code: CloseCode) -> DisconnectKind {
    let _ = reason; // @todo 可进一步用 reason 文本辅助判断
    match code {
        CloseCode::Policy => DisconnectKind::AuthRejected,
        CloseCode::Normal | CloseCode::Away => DisconnectKind::Normal,
        _ => DisconnectKind::Error,
    }
}

/// 从结果流中解析断开类型：
/// - Ok(Close(frame)) → 依据 frame.code 分类（1008 → AuthRejected）
/// - 连接正常关闭 → Normal
/// - 通道已断 → Cancelled（视为正常结束）
/// - 其余错误 → Error
fn classify_err(e: &WsError) -> DisconnectKind {
    match e {
        WsError::ConnectionClosed => DisconnectKind::Normal,
        // FastAPI/auth middleware can reject the upgrade before a Close frame.
        WsError::Http(response) if matches!(response.status().as_u16(), 401 | 403) => {
            DisconnectKind::AuthRejected
        }
        _ => DisconnectKind::Error,
    }
}

#[derive(Debug, Clone)]
pub enum WsEvent {
    Log { level: String, message: String },
    PlanUpdate(PlanUpdateEvent),
    ApprovalRequest(ApprovalRequest),
    ApprovalResolved(ApprovalResolved),
    Connected,
    Disconnected(String),
}

/// 日志是尽力而为事件，内部通道满时跳过；连接/计划事件等待空间，
/// 避免日志洪峰阻塞或静默丢弃控制相关状态。
async fn send_event(
    tx: &mpsc::Sender<WsEvent>,
    event: WsEvent,
    cancel: &mut watch::Receiver<bool>,
) -> bool {
    if matches!(event, WsEvent::Log { .. }) {
        tx.try_send(event).is_ok()
    } else {
        tokio::select! {
            biased;
            result = tx.send(event) => result.is_ok(),
            changed = cancel.changed() => changed.is_ok() && !*cancel.borrow(),
        }
    }
}

/// ws://host:port/ws/logs?token=...（token 放 query 是后端契约）
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
                out.push(char::from(b))
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// 常驻订阅循环：断开后按指数退避重连，直到通道关闭。
///
/// `cancel` 收到 Some 时立即中止当前连接/等待并返回 `Cancelled`（不发送 Disconnected 事件）。
/// 认证拒绝（1008）停止重连；其余错误按 1s→2s→…→30s 封顶退避。
pub async fn run_ws(
    base: String,
    token: String,
    tx: mpsc::Sender<WsEvent>,
    mut cancel: watch::Receiver<bool>,
) -> DisconnectKind {
    let mut backoff_secs: u64 = 1;
    loop {
        if *cancel.borrow() {
            return DisconnectKind::Cancelled;
        }
        let url = ws_url(&base, &token);

        let connected = tokio::select! {
            biased;
            outcome = tokio_tungstenite::connect_async(&url) => outcome,
            changed = cancel.changed() => {
                // 取消发生在重连尝试期间；watch sender 被丢弃也应结束任务。
                if changed.is_err() || *cancel.borrow() {
                    return DisconnectKind::Cancelled;
                }
                continue;
            }
        };

        match connected {
            Err(e) => {
                let mut kind = classify_err(&e);
                if e.to_string().contains("1008") {
                    kind = DisconnectKind::AuthRejected;
                }
                if !send_event(
                    &tx,
                    WsEvent::Disconnected(format!("WS 连接失败: {e}")),
                    &mut cancel,
                )
                .await
                {
                    return DisconnectKind::Cancelled;
                }
                if !should_reconnect(kind) {
                    return kind;
                }
            }
            Ok((mut ws, _)) => {
                backoff_secs = 1; // 连接成功，重置退避
                if !send_event(&tx, WsEvent::Connected, &mut cancel).await {
                    return DisconnectKind::Cancelled;
                }
                let kind = loop {
                    let next = tokio::select! {
                        biased;
                        item = ws.next() => item,
                        changed = cancel.changed() => {
                            // token/base 改动或上层关闭：停止当前连接，不再对 UI 发送断开提示。
                            if changed.is_err() || *cancel.borrow() {
                                break DisconnectKind::Intentional;
                            }
                            break DisconnectKind::Cancelled;
                        }
                    };
                    let Some(item) = next else {
                        // 底层流结束（连接被对端/网络断开）
                        break DisconnectKind::Error;
                    };
                    match item {
                        Ok(Message::Text(text)) => {
                            if let Some(ev) = parse_frame(&text) {
                                if !send_event(&tx, ev, &mut cancel).await {
                                    break DisconnectKind::Cancelled; // UI 已退出
                                }
                            }
                        }
                        Ok(Message::Ping(p)) => {
                            let _ = ws.send(Message::Pong(p)).await;
                        }
                        Ok(Message::Close(frame)) => {
                            // 服务端主动关闭：按关闭码分类
                            let code = frame
                                .as_ref()
                                .map(|cf| cf.code)
                                .unwrap_or(CloseCode::Status);
                            let reason = frame.as_ref().map(|cf| cf.reason.as_ref());
                            break classify_close(reason, code);
                        }
                        Ok(_) => {}
                        Err(e) => {
                            let mut kind = classify_err(&e);
                            if e.to_string().contains("1008") {
                                kind = DisconnectKind::AuthRejected;
                            }
                            break kind;
                        }
                    }
                };

                if !matches!(kind, DisconnectKind::Intentional) {
                    let text = match kind {
                        DisconnectKind::AuthRejected => {
                            "认证被拒（token 无效），停止重连".to_string()
                        }
                        DisconnectKind::Normal => "WS 正常关闭".to_string(),
                        DisconnectKind::Cancelled => "WS 已取消".to_string(),
                        DisconnectKind::Error => "WS 断开: Error".to_string(),
                        DisconnectKind::Intentional => unreachable!(),
                    };
                    if !send_event(&tx, WsEvent::Disconnected(text), &mut cancel).await {
                        return DisconnectKind::Cancelled;
                    }
                }
                if !should_reconnect(kind) {
                    return kind;
                }
            }
        }

        if tx.is_closed() {
            return DisconnectKind::Cancelled;
        }
        // Cancellation must interrupt the backoff too, otherwise token changes
        // can leave an obsolete task sleeping for up to 30 seconds.
        tokio::select! {
            biased;
            _ = tokio::time::sleep(Duration::from_secs(backoff_secs)) => {}
            changed = cancel.changed() => {
                if changed.is_err() || *cancel.borrow() {
                    return DisconnectKind::Cancelled;
                }
                continue;
            }
        }
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
        "approval_request" => {
            let ev: ApprovalRequest = serde_json::from_value(v).ok()?;
            Some(WsEvent::ApprovalRequest(ev))
        }
        "approval_resolved" => {
            let ev: ApprovalResolved = serde_json::from_value(v).ok()?;
            Some(WsEvent::ApprovalResolved(ev))
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
    use tokio_tungstenite::tungstenite::Message;

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
        let approval = parse_frame(r#"{"type":"approval_request","approval_id":"a1","plan_id":"p1","step_id":"s1","tool_id":"shell","instruction":"run","inputs":{"x":1},"created_at":"t"}"#).unwrap();
        match approval {
            WsEvent::ApprovalRequest(ev) => {
                assert_eq!(ev.approval_id, "a1");
                assert_eq!(ev.inputs["x"], 1);
            }
            _ => panic!("应为 ApprovalRequest"),
        }
        let resolved =
            parse_frame(r#"{"type":"approval_resolved","approval_id":"a1","approved":false}"#)
                .unwrap();
        match resolved {
            WsEvent::ApprovalResolved(ev) => {
                assert_eq!(ev.approval_id, "a1");
                assert!(!ev.approved);
            }
            _ => panic!("应为 ApprovalResolved"),
        }
        assert!(parse_frame(r#"{"type":"unknown"}"#).is_none());
        assert!(parse_frame("not json").is_none());
    }

    #[test]
    fn close_frame_1008_is_auth_rejection() {
        assert_eq!(
            classify_close(None, CloseCode::Policy),
            DisconnectKind::AuthRejected
        );
        assert_eq!(
            classify_close(None, CloseCode::Normal),
            DisconnectKind::Normal
        );
        assert_eq!(
            classify_close(None, CloseCode::Abnormal),
            DisconnectKind::Error
        );
    }

    #[test]
    fn distinguish_intentional_and_cancelled() {
        assert_eq!(DisconnectKind::Intentional, DisconnectKind::Intentional);
        assert_ne!(DisconnectKind::Intentional, DisconnectKind::Cancelled);
        assert!(!should_reconnect(DisconnectKind::AuthRejected));
        assert!(!should_reconnect(DisconnectKind::Intentional));
        assert!(!should_reconnect(DisconnectKind::Cancelled));
        assert!(should_reconnect(DisconnectKind::Error));
        assert!(should_reconnect(DisconnectKind::Normal));
    }

    #[test]
    fn parse_close_frame_payload() {
        match Message::Close(Some(tokio_tungstenite::tungstenite::protocol::CloseFrame {
            code: CloseCode::Policy,
            reason: std::borrow::Cow::Borrowed("invalid token"),
        })) {
            Message::Close(frame) => {
                let f = frame.unwrap();
                assert_eq!(f.code, CloseCode::Policy);
                assert_eq!(f.reason.as_ref(), "invalid token");
            }
            _ => panic!("应为 Close"),
        }
    }
}
