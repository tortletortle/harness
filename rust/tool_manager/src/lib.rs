/// EAS 2.0 Tool Manager (Rust层)
///
/// 职责：
/// 1. 工具实例管理（复制、删除）
/// 2. 工具注册表维护
/// 3. 支持插件化扩展
use async_trait::async_trait;
use eas_common::*;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use uuid::Uuid;

pub struct DefaultToolManager {
    tools: Arc<RwLock<HashMap<String, ToolSchema>>>,
    instances: Arc<RwLock<HashMap<String, ToolInstance>>>,
}

#[derive(Debug, Clone)]
struct ToolInstance {
    tool_id: String,
}

impl DefaultToolManager {
    pub fn new() -> Self {
        Self {
            tools: Arc::new(RwLock::new(HashMap::new())),
            instances: Arc::new(RwLock::new(HashMap::new())),
        }
    }
}

impl Default for DefaultToolManager {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl ToolManager for DefaultToolManager {
    async fn register_tool(&self, tool: ToolSchema) -> EasResult<()> {
        if tool.tool_id.trim().is_empty()
            || tool.version.trim().is_empty()
            || tool.name.trim().is_empty()
        {
            return Err(EasError::Validation(
                "Tool id, version and name are required".to_string(),
            ));
        }
        if tool.license.max_instances < 1 {
            return Err(EasError::Validation(
                "Tool max_instances must be positive".to_string(),
            ));
        }
        let success_rate = tool.capabilities.performance.success_rate;
        if !success_rate.is_finite() || !(0.0..=1.0).contains(&success_rate) {
            return Err(EasError::Validation(
                "Tool success_rate must be between 0 and 1".to_string(),
            ));
        }
        let mut tools = self.tools.write().await;
        tools.insert(tool.tool_id.clone(), tool);
        Ok(())
    }

    async fn get_tool(&self, tool_id: &str) -> EasResult<Option<ToolSchema>> {
        let tools = self.tools.read().await;
        Ok(tools.get(tool_id).cloned())
    }

    async fn list_tools(&self) -> EasResult<Vec<ToolSchema>> {
        let tools = self.tools.read().await;
        let mut result: Vec<_> = tools.values().cloned().collect();
        result.sort_by(|left, right| left.tool_id.cmp(&right.tool_id));
        Ok(result)
    }

    async fn assess_tool(&self, tool_id: &str) -> EasResult<Value> {
        let tool = self
            .get_tool(tool_id)
            .await?
            .ok_or_else(|| EasError::Tool(format!("Tool {} not found", tool_id)))?;
        Ok(serde_json::json!({
            "tool_id": tool_id,
            "availability": "available",
            "quality": tool.capabilities.quality_level,
            "success_rate": tool.capabilities.performance.success_rate,
            "limitations": tool.capabilities.limitations
        }))
    }

    async fn create_instance(&self, tool_id: &str) -> EasResult<String> {
        let tool = self
            .get_tool(tool_id)
            .await?
            .ok_or_else(|| EasError::Tool(format!("Tool {} not found", tool_id)))?;

        let mut instances = self.instances.write().await;
        let active = instances
            .values()
            .filter(|instance| instance.tool_id == tool_id)
            .count();
        if active >= tool.license.max_instances as usize {
            return Err(EasError::Tool(format!(
                "Tool {} reached its instance limit ({})",
                tool_id, tool.license.max_instances
            )));
        }

        let instance_id = Uuid::new_v4().to_string();
        let instance = ToolInstance {
            tool_id: tool_id.to_string(),
        };

        instances.insert(instance_id.clone(), instance);

        Ok(instance_id)
    }

    async fn delete_instance(&self, instance_id: &str) -> EasResult<()> {
        let mut instances = self.instances.write().await;
        instances
            .remove(instance_id)
            .ok_or_else(|| EasError::Tool(format!("Instance {} not found", instance_id)))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_register_and_get_tool() {
        let manager = DefaultToolManager::new();

        let tool = ToolSchema {
            tool_id: "test-tool".to_string(),
            version: "1.0.0".to_string(),
            name: "Test Tool".to_string(),
            description: "A test tool".to_string(),
            capabilities: ToolCapabilities {
                instructions: vec![],
                input_format: serde_json::json!({}),
                output_format: serde_json::json!({}),
                quality_level: "high".to_string(),
                performance: Performance {
                    avg_execution_time: "1s".to_string(),
                    success_rate: 0.99,
                },
                limitations: vec![],
            },
            file_location: "/tools/test-tool".to_string(),
            index: ToolIndex {
                main: "main.py".to_string(),
                config: None,
                tests: None,
                docs: None,
            },
            license: ToolLicense {
                license_type: "MIT".to_string(),
                max_instances: 10,
                copy_strategy: "clone".to_string(),
            },
            dependencies: vec![],
            test_results: None,
        };

        manager.register_tool(tool.clone()).await.unwrap();

        let retrieved = manager.get_tool("test-tool").await.unwrap();
        assert!(retrieved.is_some());
        assert_eq!(retrieved.unwrap().name, "Test Tool");
    }
}
