use serde::{Deserialize, Serialize};
use uuid::Uuid;
use chrono::{DateTime, Utc};

/// 设计书
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DesignDocument {
    pub design_id: Uuid,
    pub user_request: String,
    pub timestamp: DateTime<Utc>,
    pub part1_design: DesignPart1,
    pub part2_tool_assessment: ToolAssessment,
    pub user_approval: Option<bool>,
    pub user_comments: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DesignPart1 {
    pub architecture: Architecture,
    pub difficulty: Difficulty,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Architecture {
    pub components: Vec<String>,
    pub interactions: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Difficulty {
    pub overall: String,
    pub breakdown: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolAssessment {
    pub required_tools: Vec<ToolRequirement>,
    pub overall_tool_completeness: f32,
    pub risks: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolRequirement {
    pub tool_id: String,
    pub availability: String,
    pub quality: Option<String>,
    pub limitations: Option<String>,
    pub impact: Option<String>,
}

/// 执行计划
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionPlan {
    pub plan_id: Uuid,
    pub design_id: Uuid,
    pub created_at: DateTime<Utc>,
    pub status: PlanStatus,
    pub segments: Vec<PlanSegment>,
    pub implementation_list: Vec<String>,
    pub dynamic_insertions: Vec<DynamicInsertion>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum PlanStatus {
    Pending,
    Running,
    Paused,
    Completed,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlanSegment {
    pub segment_id: Uuid,
    pub description: String,
    pub steps: Vec<Step>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Step {
    pub step_id: Uuid,
    pub order: i32,
    pub description: String,
    pub tool: String,
    pub inputs: serde_json::Value,
    pub check_criteria: String,
    pub outputs: Option<serde_json::Value>,
    pub status: StepStatus,
    pub executed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum StepStatus {
    Pending,
    Running,
    Success,
    Failed,
    Skipped,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DynamicInsertion {
    pub after_step_id: Uuid,
    pub new_steps: Vec<Step>,
    pub reason: String,
}

/// 工具Schema
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolSchema {
    pub tool_id: String,
    pub version: String,
    pub name: String,
    pub description: String,
    pub capabilities: ToolCapabilities,
    pub file_location: String,
    pub index: ToolIndex,
    pub license: ToolLicense,
    pub dependencies: Vec<String>,
    pub test_results: Option<TestResults>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolCapabilities {
    pub instructions: Vec<Instruction>,
    pub input_format: serde_json::Value,
    pub output_format: serde_json::Value,
    pub quality_level: String,
    pub performance: Performance,
    pub limitations: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Instruction {
    pub name: String,
    pub description: String,
    pub parameters: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Performance {
    pub avg_execution_time: String,
    pub success_rate: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolIndex {
    pub main: String,
    pub config: Option<String>,
    pub tests: Option<String>,
    pub docs: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolLicense {
    pub license_type: String,
    pub max_instances: i32,
    pub copy_strategy: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TestResults {
    pub last_test_date: DateTime<Utc>,
    pub passed: bool,
    pub test_cases: i32,
    pub passed_cases: i32,
}

/// API请求/响应类型
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GenerateDesignRequest {
    pub user_request: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GenerateDesignResponse {
    pub design: DesignDocument,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GeneratePlanRequest {
    pub design_id: Uuid,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GeneratePlanResponse {
    pub plan: ExecutionPlan,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecuteStepRequest {
    pub step: Step,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecuteStepResponse {
    pub success: bool,
    pub output: Option<serde_json::Value>,
    pub error: Option<String>,
}
