/// EAS 2.0 Dispatcher
///
/// 职责：
/// 1. 格式检查和验证
/// 2. 路由请求到对应的Python AI服务
/// 3. 支持插件化扩展
use async_trait::async_trait;
use eas_common::*;
use serde_json::Value;
use std::collections::HashSet;
use std::time::Duration;

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
        if design.user_request.trim().is_empty() {
            return Err(EasError::Validation("User request is empty".to_string()));
        }

        if design.part1_design.architecture.components.is_empty() {
            return Err(EasError::Validation(
                "No components in architecture".to_string(),
            ));
        }
        let completeness = design.part2_tool_assessment.overall_tool_completeness;
        if !completeness.is_finite() || !(0.0..=1.0).contains(&completeness) {
            return Err(EasError::Validation(
                "Tool completeness must be between 0 and 1".to_string(),
            ));
        }

        Ok(())
    }

    async fn validate_plan(&self, plan: &ExecutionPlan) -> EasResult<()> {
        // 验证执行计划格式
        if plan.segments.is_empty() {
            return Err(EasError::Validation("No segments in plan".to_string()));
        }

        let mut segment_ids = HashSet::new();
        let mut step_ids = HashSet::new();
        for segment in &plan.segments {
            if !segment_ids.insert(segment.segment_id) {
                return Err(EasError::Validation(format!(
                    "Duplicate segment id {}",
                    segment.segment_id
                )));
            }
            if segment.steps.is_empty() {
                return Err(EasError::Validation(format!(
                    "Segment {} has no steps",
                    segment.segment_id
                )));
            }
            let mut orders = HashSet::new();
            for step in &segment.steps {
                if !step_ids.insert(step.step_id) {
                    return Err(EasError::Validation(format!(
                        "Duplicate step id {}",
                        step.step_id
                    )));
                }
                if step.order < 0 || !orders.insert(step.order) {
                    return Err(EasError::Validation(format!(
                        "Invalid or duplicate step order {} in segment {}",
                        step.order, segment.segment_id
                    )));
                }
                if step.description.trim().is_empty() || step.tool.trim().is_empty() {
                    return Err(EasError::Validation(format!(
                        "Step {} requires description and tool",
                        step.step_id
                    )));
                }
                if matches!(plan.status, PlanStatus::Completed)
                    && !matches!(step.status, StepStatus::Success | StepStatus::Skipped)
                {
                    return Err(EasError::Validation(
                        "Completed plan contains an active or failed step".to_string(),
                    ));
                }
            }
        }
        for insertion in &plan.dynamic_insertions {
            if !step_ids.contains(&insertion.after_step_id) {
                return Err(EasError::Validation(format!(
                    "Dynamic insertion references unknown step {}",
                    insertion.after_step_id
                )));
            }
        }

        Ok(())
    }

    async fn route(&self, service_name: &str, request: Value) -> EasResult<Value> {
        if service_name.is_empty()
            || !service_name
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '_')
        {
            return Err(EasError::Validation("Invalid service name".to_string()));
        }
        // 路由到Python AI服务
        let url = format!(
            "{}/api/v1/{}",
            self.python_api_url.trim_end_matches('/'),
            service_name
        );

        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(60))
            .build()
            .map_err(|e| EasError::Api(format!("Failed to build HTTP client: {}", e)))?;
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
    use chrono::Utc;
    use uuid::Uuid;

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
