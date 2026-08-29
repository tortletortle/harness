//! Rust dispatcher测试

#[cfg(test)]
mod tests {
    use eas_common::*;
    use eas_dispatcher::DefaultDispatcher;
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

        let result = dispatcher.validate_design(&design).await;
        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn test_validate_empty_request() {
        let dispatcher = DefaultDispatcher::new("http://localhost:9000".to_string());

        let design = DesignDocument {
            design_id: Uuid::new_v4(),
            user_request: "".to_string(),  // 空请求
            timestamp: Utc::now(),
            part1_design: DesignPart1 {
                architecture: Architecture {
                    components: vec![],
                    interactions: vec![],
                },
                difficulty: Difficulty {
                    overall: "".to_string(),
                    breakdown: serde_json::json!({}),
                },
            },
            part2_tool_assessment: ToolAssessment {
                required_tools: vec![],
                overall_tool_completeness: 0.0,
                risks: vec![],
            },
            user_approval: None,
            user_comments: None,
        };

        let result = dispatcher.validate_design(&design).await;
        assert!(result.is_err());
    }
}
