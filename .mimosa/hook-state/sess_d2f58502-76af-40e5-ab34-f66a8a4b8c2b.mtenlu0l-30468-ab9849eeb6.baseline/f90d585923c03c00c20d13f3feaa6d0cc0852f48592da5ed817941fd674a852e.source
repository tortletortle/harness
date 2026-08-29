/// EAS 2.0 Dispatcher
///
/// 职责：
/// 1. 格式检查和验证
/// 2. 路由请求到对应的Python AI服务
/// 3. 支持插件化扩展

use async_trait::async_trait;
use eas_common::*;
use serde_json::Value;

pub struct DefaultDispatcher {
    python_api_url: String,
}

impl DefaultDispatcher {
    pub fn new(python_api_url: String) -> Self {
        Self { python_api_url }
    }
}

#[async_trait]
impl Dispatcher for DefaultDispatcher {
    async fn validate_design(&self, design: &DesignDocument) -> EasResult<()> {
        // 验证设计书格式
        if design.user_request.is_empty() {
            return Err(EasError::Validation("User request is empty".to_string()));
        }

        if design.part1_design.architecture.components.is_empty() {
            return Err(EasError::Validation("No components in architecture".to_string()));
        }

        Ok(())
    }

    async fn validate_plan(&self, plan: &ExecutionPlan) -> EasResult<()> {
        // 验证执行计划格式
        if plan.segments.is_empty() {
            return Err(EasError::Validation("No segments in plan".to_string()));
        }

        for segment in &plan.segments {
            if segment.steps.is_empty() {
                return Err(EasError::Validation(
                    format!("Segment {} has no steps", segment.segment_id)
                ));
            }
        }

        Ok(())
    }

    async fn route(&self, service_name: &str, request: Value) -> EasResult<Value> {
        // 路由到Python AI服务
        let url = format!("{}/api/v1/{}", self.python_api_url, service_name);

        let client = reqwest::Client::new();
        let response = client
            .post(&url)
            .json(&request)
            .send()
            .await
            .map_err(|e| EasError::Api(format!("Failed to call {}: {}", service_name, e)))?;

        if !response.status().is_success() {
            return Err(EasError::Api(format!(
                "Service {} returned error: {}",
                service_name,
                response.status()
            )));
        }

        let result = response
            .json::<Value>()
            .await
            .map_err(|e| EasError::Api(format!("Failed to parse response: {}", e)))?;

        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use uuid::Uuid;
    use chrono::Utc;

    #[tokio::test]
    async fn test_validate_design() {
        let dispatcher = DefaultDispatcher::new("http://localhost:9000".to_string());

        let design = DesignDocument {
            design_id: Uuid::new_v4(),
            user_request: "Test request".to_string(),
            timestamp: Utc::now(),
            part1_design: DesignPart1 {
                architecture: Architecture {
                    components: vec!["component1".to_string()],
                    interactions: vec![],
                },
                difficulty: Difficulty {
                    overall: "medium".to_string(),
                    breakdown: serde_json::json!({}),
                },
            },
            part2_tool_assessment: ToolAssessment {
                required_tools: vec![],
                overall_tool_completeness: 1.0,
                risks: vec![],
            },
            user_approval: None,
            user_comments: None,
        };

        assert!(dispatcher.validate_design(&design).await.is_ok());
    }
}
