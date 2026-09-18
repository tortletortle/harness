/// EAS 2.0 HTTP Server (Rust层)
///
/// 职责：
/// 1. 提供REST API
/// 2. 协调各个模块
/// 3. 处理GUI请求
use axum::{
    extract::State,
    http::{header, HeaderMap, HeaderName, HeaderValue, Method, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use eas_common::*;
use eas_dispatcher::DefaultDispatcher;
use eas_tool_manager::DefaultToolManager;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tower_http::cors::{AllowOrigin, CorsLayer};

#[derive(Clone)]
struct AppState {
    dispatcher: Arc<DefaultDispatcher>,
    tool_manager: Arc<DefaultToolManager>,
    api_token: Arc<str>,
}

#[derive(Debug, Serialize)]
struct ErrorBody {
    code: &'static str,
    detail: String,
}

#[derive(Debug)]
struct AppError {
    status: StatusCode,
    code: &'static str,
    detail: String,
}

impl AppError {
    fn unauthorized() -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            code: "unauthorized",
            detail: "缺少或无效的 API token".to_string(),
        }
    }

    fn bad_request(detail: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            code: "validation_error",
            detail: detail.into(),
        }
    }

    fn internal(detail: impl Into<String>) -> Self {
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            code: "internal_error",
            detail: detail.into(),
        }
    }
}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(ErrorBody {
                code: self.code,
                detail: self.detail,
            }),
        )
            .into_response()
    }
}

fn load_token() -> std::io::Result<String> {
    if let Ok(value) = std::env::var("EAS_API_TOKEN") {
        let value = value.trim().to_string();
        if !value.is_empty() {
            return Ok(value);
        }
    }
    std::fs::read_to_string("config/api_token")
        .map(|value| value.trim().to_string())
        .map_err(|error| {
            std::io::Error::new(
                error.kind(),
                format!("EAS_API_TOKEN is unset and config/api_token could not be read: {error}"),
            )
        })
}

fn authorize(headers: &HeaderMap, state: &AppState) -> Result<(), AppError> {
    let bearer = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "));
    let legacy = headers
        .get("x-api-token")
        .and_then(|value| value.to_str().ok());
    if bearer
        .or(legacy)
        .is_some_and(|candidate| constant_time_eq(candidate.as_bytes(), state.api_token.as_bytes()))
    {
        Ok(())
    } else {
        Err(AppError::unauthorized())
    }
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    let mut difference = left.len() ^ right.len();
    for index in 0..left.len().max(right.len()) {
        difference |= usize::from(
            left.get(index).copied().unwrap_or_default()
                ^ right.get(index).copied().unwrap_or_default(),
        );
    }
    difference == 0
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // --version / -V：打印版本与 git 溯源信息后退出（docs/VERSIONING.md）。
    if std::env::args().any(|a| a == "--version" || a == "-V") {
        println!(
            "eas-server {} ({}, {})",
            env!("CARGO_PKG_VERSION"),
            env!("EAS_GIT_DESCRIBE"),
            env!("EAS_GIT_COMMIT_DATE"),
        );
        return Ok(());
    }
    // 初始化日志
    tracing_subscriber::fmt::init();

    // 创建应用状态
    let python_api = std::env::var("EAS_PYTHON_API_BASE")
        .unwrap_or_else(|_| "http://127.0.0.1:8000".to_string());
    let api_token = load_token()?;
    if api_token.is_empty() {
        return Err("API token must not be empty".into());
    }
    let state = AppState {
        dispatcher: Arc::new(DefaultDispatcher::new(python_api)),
        tool_manager: Arc::new(DefaultToolManager::new()),
        api_token: Arc::from(api_token),
    };

    // 创建路由
    let app = Router::new()
        .route("/", get(root))
        .route("/health", get(health_check))
        .route("/api/v1/design/validate", post(validate_design))
        .route("/api/v1/plan/validate", post(validate_plan))
        .route("/api/v1/tools", get(list_tools))
        .route("/api/v1/tools/register", post(register_tool))
        .layer(
            CorsLayer::new()
                .allow_origin(AllowOrigin::list([
                    HeaderValue::from_static("http://localhost:3000"),
                    HeaderValue::from_static("http://127.0.0.1:3000"),
                ]))
                .allow_methods([Method::GET, Method::POST])
                .allow_headers([
                    header::AUTHORIZATION,
                    header::CONTENT_TYPE,
                    HeaderName::from_static("x-api-token"),
                ]),
        )
        .with_state(state);

    // 启动服务器
    let addr = std::env::var("EAS_RUST_BIND").unwrap_or_else(|_| "127.0.0.1:8090".to_string());
    tracing::info!("Starting EAS Rust server on {}", addr);

    let listener = tokio::net::TcpListener::bind(&addr).await?;
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}

async fn root() -> &'static str {
    "EAS 2.0 Rust Server"
}

async fn health_check() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "ok",
        "service": "eas-rust-server",
        "version": "0.1.0"
    }))
}

#[derive(Deserialize)]
struct ValidateDesignRequest {
    design: DesignDocument,
}

async fn validate_design(
    headers: HeaderMap,
    State(state): State<AppState>,
    Json(req): Json<ValidateDesignRequest>,
) -> Result<Json<serde_json::Value>, AppError> {
    authorize(&headers, &state)?;
    state
        .dispatcher
        .validate_design(&req.design)
        .await
        .map_err(|error| AppError::bad_request(error.to_string()))?;
    Ok(Json(serde_json::json!({"valid": true})))
}

#[derive(Deserialize)]
struct ValidatePlanRequest {
    plan: ExecutionPlan,
}

async fn validate_plan(
    headers: HeaderMap,
    State(state): State<AppState>,
    Json(req): Json<ValidatePlanRequest>,
) -> Result<Json<serde_json::Value>, AppError> {
    authorize(&headers, &state)?;
    state
        .dispatcher
        .validate_plan(&req.plan)
        .await
        .map_err(|error| AppError::bad_request(error.to_string()))?;
    Ok(Json(serde_json::json!({"valid": true})))
}

async fn list_tools(
    headers: HeaderMap,
    State(state): State<AppState>,
) -> Result<Json<Vec<ToolSchema>>, AppError> {
    authorize(&headers, &state)?;
    state
        .tool_manager
        .list_tools()
        .await
        .map(Json)
        .map_err(|error| AppError::internal(error.to_string()))
}

#[derive(Deserialize)]
struct RegisterToolRequest {
    tool: ToolSchema,
}

async fn register_tool(
    headers: HeaderMap,
    State(state): State<AppState>,
    Json(req): Json<RegisterToolRequest>,
) -> Result<Json<serde_json::Value>, AppError> {
    authorize(&headers, &state)?;
    state
        .tool_manager
        .register_tool(req.tool)
        .await
        .map_err(|error| AppError::bad_request(error.to_string()))?;
    Ok(Json(serde_json::json!({"success": true})))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn constant_time_comparison_handles_length_and_content() {
        assert!(constant_time_eq(b"secret", b"secret"));
        assert!(!constant_time_eq(b"secret", b"Secret"));
        assert!(!constant_time_eq(b"secret", b"secret-longer"));
        assert!(!constant_time_eq(b"", b"secret"));
    }
}
