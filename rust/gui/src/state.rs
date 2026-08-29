//! UI 侧状态与 reducer。
//!
//! 权威数据路径（与 Web 端同构，设计稿 §20.3）：
//! - plan_update 推送只应用 version 严格递增的事件（版本围栏，§16.4）
//! - 30s REST 兜底全量拉取作为权威校准
//! - 日志环形缓冲，防止长时间运行撑爆内存

use crate::types::{parse_plan_status, parse_step_status, PlanDetail, PlanSummary, PlanStatus, StepStatus};
use std::collections::HashMap;

const LOG_CAP: usize = 1000;

#[derive(Debug, Clone, PartialEq)]
pub struct LogLine {
    pub level: String,
    pub message: String,
}

/// 选中计划的运行时视图：detail + 实时推送合并出的步骤状态
#[derive(Debug, Clone)]
pub struct SelectedPlan {
    pub plan_id: String,
    pub status: PlanStatus,
    /// step_id → 实时状态（优先于 detail 内嵌的静态状态）
    pub live_status: HashMap<String, StepStatus>,
    pub detail: PlanDetail,
    /// 已应用的 plan_update 版本（版本围栏）
    pub last_version: u64,
}

impl SelectedPlan {
    pub fn step_status(&self, step_id: &str, fallback: &str) -> StepStatus {
        self.live_status
            .get(step_id)
            .copied()
            .unwrap_or_else(|| parse_step_status(fallback))
    }

    /// 步骤状态变更次数（Progress = 非 Pending 比例）
    pub fn progress(&self) -> (usize, usize) {
        let total = self.detail.steps.len();
        let done = self
            .detail
            .steps
            .iter()
            .filter(|s| {
                matches!(
                    self.step_status(&s.step_id, &s.status),
                    StepStatus::Success | StepStatus::Skipped | StepStatus::RolledBack
                )
            })
            .count();
        (done, total)
    }
}

#[derive(Debug, Default)]
pub struct GuiState {
    pub token: String,
    pub connected: bool,
    pub last_ws_error: Option<String>,
    pub service_status: Option<crate::types::ServiceStatus>,
    pub plans: Vec<PlanSummary>,
    pub selected: Option<SelectedPlan>,
    pub logs: Vec<LogLine>,
    pub notice: Option<String>,
}

impl GuiState {
    pub fn push_log(&mut self, level: &str, message: String) {
        self.logs.push(LogLine {
            level: level.to_string(),
            message,
        });
        if self.logs.len() > LOG_CAP {
            let overflow = self.logs.len() - LOG_CAP;
            self.logs.drain(0..overflow);
        }
    }

    pub fn set_notice(&mut self, msg: impl Into<String>) {
        self.notice = Some(msg.into());
    }

    /// 应用 plan_update：仅当选中计划匹配且 version 严格递增。
    /// 返回 true 表示状态有变化（UI 需要重绘）。
    pub fn apply_plan_update(&mut self, plan_id: &str, status: &str, steps: &[(String, String)], version: u64) -> bool {
        let Some(sel) = self.selected.as_mut() else {
            return false;
        };
        if sel.plan_id != plan_id {
            return false;
        }
        if version != 0 && version <= sel.last_version {
            return false; // 版本围栏：过期推送丢弃
        }
        sel.last_version = version;
        sel.status = parse_plan_status(status);
        for (step_id, st) in steps {
            sel.live_status.insert(step_id.clone(), parse_step_status(st));
        }
        true
    }

    /// REST 兜底全量拉取的权威校准（整体替换选中计划）
    pub fn set_plan_detail(&mut self, detail: PlanDetail) {
        self.selected = Some(SelectedPlan {
            plan_id: detail.plan_id.clone(),
            status: parse_plan_status(&detail.status),
            live_status: HashMap::new(),
            detail,
            last_version: 0,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn detail_with(plan_id: &str, steps: &[(&str, &str)]) -> PlanDetail {
        let raw_steps: Vec<serde_json::Value> = steps
            .iter()
            .map(|(id, st)| {
                serde_json::json!({"step_id": id, "order": 1, "description": "d", "tool": "t", "status": st})
            })
            .collect();
        let raw = serde_json::json!({
            "plan_id": plan_id, "design_id": "d", "status": "Pending",
            "segments": [{"segment_id": "s", "description": "seg", "steps": raw_steps}],
            "steps": raw_steps,
        });
        serde_json::from_value(raw).unwrap()
    }

    #[test]
    fn plan_update_applies_and_fences_versions() {
        let mut st = GuiState::default();
        st.set_plan_detail(detail_with("p1", &[("s1", "Pending")]));
        assert_eq!(st.selected.as_ref().unwrap().status, PlanStatus::Pending);

        // 新版本应用
        assert!(st.apply_plan_update("p1", "Running", &[("s1".into(), "Running".into())], 5));
        assert_eq!(st.selected.as_ref().unwrap().status, PlanStatus::Running);
        assert_eq!(
            st.selected.as_ref().unwrap().step_status("s1", "Pending"),
            StepStatus::Running
        );

        // 旧版本被围栏丢弃
        assert!(!st.apply_plan_update("p1", "Failed", &[("s1".into(), "Failed".into())], 4));
        assert_eq!(
            st.selected.as_ref().unwrap().step_status("s1", "Pending"),
            StepStatus::Running
        );

        // 计划不匹配则忽略
        assert!(!st.apply_plan_update("other", "Failed", &[], 9));
    }

    #[test]
    fn progress_counts_done_steps() {
        let mut st = GuiState::default();
        st.set_plan_detail(detail_with("p1", &[("s1", "Success"), ("s2", "Skipped"), ("s3", "Pending")]));
        assert_eq!(st.selected.as_ref().unwrap().progress(), (2, 3));
    }

    #[test]
    fn logs_are_capped() {
        let mut st = GuiState::default();
        for i in 0..1500 {
            st.push_log("info", format!("line {i}"));
        }
        assert_eq!(st.logs.len(), 1000);
        assert!(st.logs[0].message.ends_with("500"));
        assert!(st.logs[999].message.ends_with("1499"));
    }
}
