/// EAS 2.0 HTTP Server (Rust层)
///
/// 职责：
/// 1. 提供REST API
/// 2. 协调各个模块
/// 3. 处理GUI请求

use axum::{
    extract::State,
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use eas_common::*;
use eas_dispatcher::DefaultDispatcher;
use eas_logger::InMemoryLogger;
use eas_tool_manager::DefaultToolManager;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tower_http::cors::CorsLayer;

#[derive(Clone)]
struct AppState {
    dispatcher: Arc<DefaultDispatcher>,
    logger: Arc<InMemoryLogger>,
    tool_manager: Arc<DefaultToolManager>,
}

#[tokio::main]
async fn main() {
    // 初始化日志
    tracing_subscriber::fmt::init();

    // 创建应用状态
    let state = AppState {
        dispatcher: Arc::new(DefaultDispatcher::new("http://localhost:9000".to_string())),
        logger: Arc::new(InMemoryLogger::new()),
        tool_manager: Arc::new(DefaultToolManager::new()),
    };

    // 创建路由
    let app = Router::new()
        .route("/", get(root))
        .route("/health", get(health_check))
        .route("/api/v1/design/validate", post(validate_design))
        .route("/api/v1/plan/validate", post(validate_plan))
        .route("/api/v1/tools", get(list_tools))
        .route("/api/v1/tools/register", post(register_tool))
        .layer(CorsLayer::permissive())
        .with_state(state);

    // 启动服务器
    let addr = "0.0.0.0:8090";
    tracing::info!("Starting EAS Rust server on {}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
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
    State(state): State<AppState>,
    Json(req): Json<ValidateDesignRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state.dispatcher.validate_design(&req.design).await {
        Ok(_) => Ok(Json(serde_json::json!({
            "valid": true
        }))),
        Err(e) => {
            tracing::error!("Validation failed: {:?}", e);
            Err(StatusCode::BAD_REQUEST)
        }
    }
}

#[derive(Deserialize)]
struct ValidatePlanRequest {
    plan: ExecutionPlan,
}

async fn validate_plan(
    State(state): State<AppState>,
    Json(req): Json<ValidatePlanRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state.dispatcher.validate_plan(&req.plan).await {
        Ok(_) => Ok(Json(serde_json::json!({
            "valid": true
        }))),
        Err(e) => {
            tracing::error!("Validation failed: {:?}", e);
            Err(StatusCode::BAD_REQUEST)
        }
    }
}

async fn list_tools(
    State(state): State<AppState>,
) -> Result<Json<Vec<ToolSchema>>, StatusCode> {
    match state.tool_manager.list_tools().await {
        Ok(tools) => Ok(Json(tools)),
        Err(e) => {
            tracing::error!("Failed to list tools: {:?}", e);
            Err(StatusCode::INTERNAL_SERVER_ERROR)
        }
    }
}

#[derive(Deserialize)]
struct RegisterToolRequest {
    tool: ToolSchema,
}

async fn register_tool(
    State(state): State<AppState>,
    Json(req): Json<RegisterToolRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state.tool_manager.register_tool(req.tool).await {
        Ok(_) => Ok(Json(serde_json::json!({
            "success": true
        }))),
        Err(e) => {
            tracing::error!("Failed to register tool: {:?}", e);
            Err(StatusCode::INTERNAL_SERVER_ERROR)
        }
    }
}
