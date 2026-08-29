//! GUI 本地 DTO——精确对齐 `python/api/server.py` 的实际 REST 契约。
//!
//! 设计决策（docs/AGENT_HARNESS_DESIGN.md §20.4）：不复用 eas-common——
//! 其 PlanStatus/StepStatus 缺少 Approved/Retry/RolledBack，且 API DTO 对应
//! 已废弃的 /api/v1 形状。待 eas-common 与 Python 契约对齐后再评估回收。

use serde::Deserialize;

/// Python PlanStatus 全集（models/__init__.py）
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "PascalCase")]
pub enum PlanStatus {
    Draft,
    Pending,
    Approved,
    Running,
    Paused,
    Completed,
    Failed,
    RolledBack,
}

/// Python StepStatus 全集
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "PascalCase")]
pub enum StepStatus {
    Pending,
    Running,
    Success,
    Failed,
    Retry,
    Skipped,
    RolledBack,
}

/// 宽松反序列化：未知字符串不炸（向前兼容新状态），只丢精度。
pub fn parse_plan_status(s: &str) -> PlanStatus {
    match s {
        "Draft" => PlanStatus::Draft,
        "Approved" => PlanStatus::Approved,
        "Running" => PlanStatus::Running,
        "Paused" => PlanStatus::Paused,
        "Completed" => PlanStatus::Completed,
        "Failed" => PlanStatus::Failed,
        "RolledBack" => PlanStatus::RolledBack,
        _ => PlanStatus::Pending,
    }
}

pub fn parse_step_status(s: &str) -> StepStatus {
    match s {
        "Running" => StepStatus::Running,
        "Success" => StepStatus::Success,
        "Failed" => StepStatus::Failed,
        "Retry" => StepStatus::Retry,
        "Skipped" => StepStatus::Skipped,
        "RolledBack" => StepStatus::RolledBack,
        _ => StepStatus::Pending,
    }
}

/// GET /api/plans 列表行
#[derive(Debug, Clone, Deserialize)]
pub struct PlanSummary {
    pub plan_id: String,
    #[serde(default)]
    pub design_id: String,
    #[serde(default)]
    pub created_at: String,
}

/// GET /api/plan/{id} 的步骤（segments 内嵌与顶层 steps 同构）
#[derive(Debug, Clone, Deserialize)]
pub struct StepView {
    pub step_id: String,
    #[serde(default)]
    pub order: i64,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub tool: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub outputs: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SegmentView {
    #[serde(default)]
    pub segment_id: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub steps: Vec<StepView>,
}

/// GET /api/plan/{id} 响应体（status 为新增的计划级字段）
#[derive(Debug, Clone, Deserialize)]
pub struct PlanDetail {
    pub plan_id: String,
    #[serde(default)]
    pub design_id: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub segments: Vec<SegmentView>,
    #[serde(default)]
    pub steps: Vec<StepView>,
}

/// GET /api/status 响应
#[derive(Debug, Clone, Deserialize)]
pub struct ServiceStatus {
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub llm_providers: Vec<String>,
    #[serde(default)]
    pub tools_count: u32,
    #[serde(default)]
    pub current_execution: Option<String>,
}

/// WS plan_update 事件（后端带自增 version 围栏）
#[derive(Debug, Clone, Deserialize)]
pub struct PlanUpdateEvent {
    #[serde(default)]
    pub plan_id: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub steps: Vec<StepStatusRow>,
    #[serde(default)]
    pub version: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct StepStatusRow {
    #[serde(default)]
    pub step_id: String,
    #[serde(default)]
    pub status: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 真实 API 形状夹具：含全部扩展状态（Approved/Retry/RolledBack），
    /// eas-common 的枚举在此会反序列化失败——这正是 fork 的理由。
    #[test]
    fn parse_plan_detail_with_full_status_set() {
        let raw = r#"{
            "plan_id": "p1",
            "design_id": "d1",
            "status": "Approved",
            "segments": [{"segment_id": "s1", "description": "seg", "steps": [
                {"step_id": "st1", "order": 1, "description": "d1", "tool": "file_creator",
                 "status": "Retry", "outputs": null},
                {"step_id": "st2", "order": 2, "description": "d2", "tool": "manual",
                 "status": "RolledBack", "outputs": {"error": "x"}}
            ]}],
            "steps": [
                {"step_id": "st1", "order": 1, "description": "d1", "tool": "file_creator", "status": "Retry"},
                {"step_id": "st2", "order": 2, "description": "d2", "tool": "manual", "status": "RolledBack"}
            ],
            "created_at": "2026-08-29T00:00:00"
        }"#;
        let detail: PlanDetail = serde_json::from_str(raw).expect("解析必须成功");
        assert_eq!(detail.status, "Approved");
        assert_eq!(detail.steps.len(), 2);
        assert_eq!(detail.steps[0].status, "Retry");
        assert_eq!(detail.steps[1].status, "RolledBack");
        assert_eq!(parse_plan_status(&detail.status), PlanStatus::Approved);
        assert_eq!(parse_step_status(&detail.steps[0].status), StepStatus::Retry);
    }

    #[test]
    fn parse_service_status() {
        let raw = r#"{"status":"running","llm_providers":["bbapi"],"tools_count":7,"current_execution":null}"#;
        let st: ServiceStatus = serde_json::from_str(raw).unwrap();
        assert_eq!(st.tools_count, 7);
        assert!(st.current_execution.is_none());
    }

    #[test]
    fn parse_plan_update_event() {
        let raw = r#"{"type":"plan_update","plan_id":"p1","status":"Running",
            "steps":[{"step_id":"s1","status":"Running"}],"version":42}"#;
        let ev: PlanUpdateEvent = serde_json::from_str(raw).unwrap();
        assert_eq!(ev.version, 42);
        assert_eq!(ev.steps[0].status, "Running");
    }
}
