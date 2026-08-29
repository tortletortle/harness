//! REST 客户端——Python FastAPI 后端（python/api/server.py，端口 8000）。
//!
//! 所有 /api/* 端点要求 Bearer token；失败时优先透传后端中文 detail
//! （FastAPI {"detail": ...} 格式），与 Web 端 client.ts 的行为一致。

use crate::types::{PlanDetail, PlanSummary, ServiceStatus};
use std::sync::{Arc, RwLock};
use std::time::Duration;
use tokio::sync::watch;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug, Clone, PartialEq, Eq)]
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
        ApiError {
            status: 0,
            message: format!("请求/解析失败: {e}"),
        }
    }
}

pub type ControlResult = Result<String, ApiError>;

/// 运行时 API 配置的不可变快照。
///
/// `generation` 在 base 或 token 真正改变时递增，WS 订阅据此取消旧连接并重启。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeConfig {
    pub base: String,
    pub token: String,
    pub generation: u64,
}

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
        .and_then(|v| v.get("detail").and_then(|d| d.as_str()).map(str::to_string))
        .unwrap_or_else(|| fallback.to_string());
    Err(ApiError {
        status,
        message: detail,
    })
}

fn normalize_base(base: &str) -> Result<String, ApiError> {
    let base = base.trim().trim_end_matches('/');
    if base.is_empty() {
        return Err(ApiError {
            status: 0,
            message: "API base 不能为空".into(),
        });
    }
    let url = reqwest::Url::parse(base).map_err(|e| ApiError {
        status: 0,
        message: format!("API base 无效: {e}"),
    })?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none() {
        return Err(ApiError {
            status: 0,
            message: "API base 必须是带主机的 http(s) URL".into(),
        });
    }
    Ok(base.to_string())
}

#[derive(Clone)]
pub struct ApiClient {
    http: reqwest::Client,
    config: Arc<RwLock<RuntimeConfig>>,
    config_changes: watch::Sender<u64>,
}

impl ApiClient {
    pub fn new(base: String, token: String) -> Result<Self, ApiError> {
        let base = normalize_base(&base)?;
        let http = reqwest::Client::builder()
            .timeout(REQUEST_TIMEOUT)
            .build()
            .map_err(|e| ApiError {
                status: 0,
                message: format!("HTTP 客户端初始化失败: {e}"),
            })?;
        let (config_changes, _) = watch::channel(0);
        Ok(Self {
            http,
            config: Arc::new(RwLock::new(RuntimeConfig {
                base,
                token,
                generation: 0,
            })),
            config_changes,
        })
    }

    pub fn config(&self) -> RuntimeConfig {
        self.config
            .read()
            .expect("API 配置锁被污染")
            .clone()
    }

    /// 订阅 base/token 改动；WS 用它来取消当前连接并重启。
    pub fn subscribe_config_changes(&self) -> watch::Receiver<u64> {
        self.config_changes.subscribe()
    }

    /// 安全更新 API base。空值、非 http(s) 或无主机 URL 都不会覆盖旧配置。
    pub fn set_base(&self, base: String) -> Result<RuntimeConfig, ApiError> {
        let base = normalize_base(&base)?;
        let mut config = self.config.write().expect("API 配置锁被污染");
        if config.base != base {
            config.base = base;
            config.generation = config.generation.saturating_add(1);
            let _ = self.config_changes.send(config.generation);
        }
        Ok(config.clone())
    }

    /// 验证候选 token 必须使用当前 API base 成功访问 /api/status，
    /// 成功后才原子替换 REST token；失败时旧 token 完全不变。
    pub async fn validate_and_set_token(&self, token: String) -> Result<RuntimeConfig, ApiError> {
        let token = token.trim().to_string();
        if token.is_empty() {
            return Err(ApiError {
                status: 0,
                message: "token 不能为空".into(),
            });
        }

        // base 可能由另一个运行时设置调用改变。验证后比较 base，避免把 token
        // 写入一个已经不是验证目标的配置；在这种极窄竞态下重新验证。
        loop {
            let before = self.config();
            self.get_status_with(&before.base, &token).await?;

            let mut config = self.config.write().expect("API 配置锁被污染");
            if config.base != before.base || config.generation != before.generation {
                return Err(ApiError {
                    status: 0,
                    message: "API 配置在 token 校验期间已变化，请重试".into(),
                });
            }
            if config.token != token {
                config.token = token;
                config.generation = config.generation.saturating_add(1);
                let _ = self.config_changes.send(config.generation);
            }
            return Ok(config.clone());
        }
    }

    async fn request(
        &self,
        method: reqwest::Method,
        base: &str,
        token: &str,
        path: &str,
    ) -> Result<reqwest::Response, ApiError> {
        let builder = self
            .http
            .request(method, format!("{base}{path}"))
            .bearer_auth(token);
        let resp = builder
            .send()
            .await
            .map_err(|e| ApiError {
                status: 0,
                message: format!("连接失败: {e}"),
            })?;
        check(resp, "请求失败").await
    }

    async fn get_with(
        &self,
        base: &str,
        token: &str,
        path: &str,
    ) -> Result<reqwest::Response, ApiError> {
        self.request(reqwest::Method::GET, base, token, path).await
    }

    async fn get_status_with(&self, base: &str, token: &str) -> Result<ServiceStatus, ApiError> {
        let resp = self.get_with(base, token, "/api/status").await?;
        Ok(resp.json().await?)
    }

    async fn get(&self, path: &str) -> Result<reqwest::Response, ApiError> {
        let config = self.config();
        self.get_with(&config.base, &config.token, path).await
    }

    async fn post(&self, path: &str) -> Result<reqwest::Response, ApiError> {
        let config = self.config();
        self.request(reqwest::Method::POST, &config.base, &config.token, path)
            .await
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

    async fn control(&self, path: &str, ok_msg: &str) -> ControlResult {
        let resp = self.post(path).await?;
        let v: serde_json::Value = resp.json().await?;
        let msg = v.get("message").and_then(|m| m.as_str()).unwrap_or(ok_msg);
        Ok(msg.to_string())
    }

    pub async fn execute_plan(&self, plan_id: &str) -> ControlResult {
        self.control(&format!("/api/plan/{plan_id}/execute"), "执行已开始")
            .await
    }

    pub async fn pause_plan(&self, plan_id: &str) -> ControlResult {
        self.control(&format!("/api/plan/{plan_id}/pause"), "暂停请求已发出")
            .await
    }

    pub async fn resume_plan(&self, plan_id: &str) -> ControlResult {
        self.control(&format!("/api/plan/{plan_id}/resume"), "已从断点恢复执行")
            .await
    }

    pub async fn skip_step(&self, plan_id: &str, step_id: &str) -> ControlResult {
        self.control(
            &format!("/api/plan/{plan_id}/skip-step/{step_id}"),
            "步骤已标记为跳过",
        )
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_empty_or_invalid_base_without_claiming_success() {
        assert!(ApiClient::new(String::new(), "old".into()).is_err());
        assert!(ApiClient::new("ftp://localhost".into(), "old".into()).is_err());

        let api = ApiClient::new("http://127.0.0.1:8000/".into(), "old".into()).unwrap();
        let before = api.config();
        assert!(api.set_base(String::new()).is_err());
        assert_eq!(api.config(), before);
    }

    #[tokio::test]
    async fn failed_token_validation_keeps_old_token_and_generation() {
        let api = ApiClient::new("http://127.0.0.1:1".into(), "old".into()).unwrap();
        let before = api.config();
        assert!(api.validate_and_set_token(String::new()).await.is_err());
        assert_eq!(api.config(), before);
    }
}
