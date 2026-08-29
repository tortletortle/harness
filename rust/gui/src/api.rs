//! REST 客户端——Python FastAPI 后端（python/api/server.py，端口 8000）。
//!
//! 所有 /api/* 端点要求 Bearer token；失败时优先透传后端中文 detail
//! （FastAPI {"detail": ...} 格式），与 Web 端 client.ts 的行为一致。

use crate::types::{PlanDetail, PlanSummary, ServiceStatus};

#[derive(Debug)]
pub struct ApiError {
    pub status: u16,
    pub message: String,
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.status, self.message)
    }
}

impl From<reqwest::Error> for ApiError {
    fn from(e: reqwest::Error) -> Self {
        ApiError { status: 0, message: format!("请求/解析失败: {e}") }
    }
}

pub type ControlResult = Result<String, ApiError>;

/// 非 2xx 时读取 {"detail": ...} 并作为错误抛出（消费 Response）。
async fn check(resp: reqwest::Response, fallback: &str) -> Result<reqwest::Response, ApiError> {
    let status = resp.status().as_u16();
    if resp.status().is_success() {
        return Ok(resp);
    }
    let detail = resp
        .json::<serde_json::Value>()
        .await
        .ok()
        .and_then(|v| v.get("detail").and_then(|d| d.as_str()).map(|s| s.to_string()))
        .unwrap_or_else(|| fallback.to_string());
    Err(ApiError { status, message: detail })
}

#[derive(Clone)]
pub struct ApiClient {
    http: reqwest::Client,
    base: String,
    token: String,
}

impl ApiClient {
    pub fn new(base: String, token: String) -> Self {
        Self {
            http: reqwest::Client::new(),
            base: base.trim_end_matches('/').to_string(),
            token,
        }
    }

    pub fn set_token(&mut self, token: String) {
        self.token = token;
    }

    async fn get(&self, path: &str) -> Result<reqwest::Response, ApiError> {
        let resp = self
            .http
            .get(format!("{}{}", self.base, path))
            .bearer_auth(&self.token)
            .send()
            .await
            .map_err(|e| ApiError { status: 0, message: format!("连接失败: {e}") })?;
        check(resp, "请求失败").await
    }

    async fn post(&self, path: &str) -> Result<reqwest::Response, ApiError> {
        let resp = self
            .http
            .post(format!("{}{}", self.base, path))
            .bearer_auth(&self.token)
            .send()
            .await
            .map_err(|e| ApiError { status: 0, message: format!("连接失败: {e}") })?;
        check(resp, "请求失败").await
    }

    pub async fn get_status(&self) -> Result<ServiceStatus, ApiError> {
        let resp = self.get("/api/status").await?;
        Ok(resp.json().await?)
    }

    pub async fn get_plans(&self, limit: u32) -> Result<Vec<PlanSummary>, ApiError> {
        let resp = self.get(&format!("/api/plans?limit={limit}")).await?;
        #[derive(serde::Deserialize)]
        struct Wrap {
            #[serde(default)]
            plans: Vec<PlanSummary>,
        }
        let w: Wrap = resp.json().await?;
        Ok(w.plans)
    }

    pub async fn get_plan(&self, plan_id: &str) -> Result<PlanDetail, ApiError> {
        let resp = self.get(&format!("/api/plan/{plan_id}")).await?;
        #[derive(serde::Deserialize)]
        struct Wrap {
            plan: PlanDetail,
        }
        let w: Wrap = resp.json().await?;
        Ok(w.plan)
    }

    async fn control(&self, path: &str, ok_msg: &str, fallback: &str) -> ControlResult {
        let resp = self.post(path).await?;
        let v: serde_json::Value = resp.json().await?;
        let msg = v.get("message").and_then(|m| m.as_str()).unwrap_or(ok_msg);
        Ok(msg.to_string())
    }

    pub async fn execute_plan(&self, plan_id: &str) -> ControlResult {
        self.control(&format!("/api/plan/{plan_id}/execute"), "执行已开始", "执行失败").await
    }

    pub async fn pause_plan(&self, plan_id: &str) -> ControlResult {
        self.control(&format!("/api/plan/{plan_id}/pause"), "暂停请求已发出", "暂停失败").await
    }

    pub async fn resume_plan(&self, plan_id: &str) -> ControlResult {
        self.control(&format!("/api/plan/{plan_id}/resume"), "已从断点恢复执行", "恢复失败").await
    }

    pub async fn skip_step(&self, plan_id: &str, step_id: &str) -> ControlResult {
        self.control(
            &format!("/api/plan/{plan_id}/skip-step/{step_id}"),
            "步骤已标记为跳过",
            "跳过失败",
        )
        .await
    }
}
