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
    /// 是否已经收到过该计划的 WS 运行态；用于避免 REST 刷新覆盖无版本事件。
    has_ws_status: bool,
}

impl SelectedPlan {
    pub fn step_status(&self, step_id: &str, fallback: &str) -> StepStatus {
        self.live_status
            .get(step_id)
            .copied()
            .unwrap_or_else(|| parse_step_status(fallback))
    }

    /// 步骤状态变更次数（Progress = 成功/跳过比例；回滚不算完成）
    pub fn progress(&self) -> (usize, usize) {
        let total = self.detail.steps.len();
        let done = self
            .detail
            .steps
            .iter()
            .filter(|s| {
                matches!(
                    self.step_status(&s.step_id, &s.status),
                    StepStatus::Success | StepStatus::Skipped
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
    /// 每个计划最后观察到的 WS 版本，计划切换后仍可防止旧 REST detail 清零版本。
    pub last_versions: HashMap<String, u64>,
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

    /// 应用 plan_update：仅当匹配计划且 version 严格递增。
    /// 返回 true 表示状态有变化（UI 需要重绘）。
    pub fn apply_plan_update(
        &mut self,
        plan_id: &str,
        status: &str,
        steps: &[(String, String)],
        version: u64,
    ) -> bool {
        let known_version = self.last_versions.get(plan_id).copied().unwrap_or(0);
        if version != 0 && version <= known_version {
            return false; // 版本围栏：过期推送丢弃
        }
        if version != 0 {
            self.last_versions.insert(plan_id.to_string(), version);
        }

        let Some(sel) = self.selected.as_mut() else {
            return false;
        };
        if sel.plan_id != plan_id {
            return false;
        }
        sel.last_version = sel.last_version.max(version);
        sel.status = parse_plan_status(status);
        sel.has_ws_status = true;
        for (step_id, st) in steps {
            sel.live_status
                .insert(step_id.clone(), parse_step_status(st));
        }
        true
    }

    /// REST 兜底全量拉取的权威校准。
    ///
    /// 对同一计划保留已收到的 WS live_status 和 last_version；REST detail 只更新
    /// 静态描述/步骤，不得清掉更新鲜的运行时信息。不同计划切换时从 last_versions
    /// 恢复版本围栏，避免旧版本事件倒灌。
    pub fn set_plan_detail(&mut self, detail: PlanDetail) {
        let plan_id = detail.plan_id.clone();
        if let Some(previous) = self.selected.take() {
            if previous.plan_id == plan_id {
                // REST detail 只更新静态详情；已有 live_status 由 WS 提供，
                // 不能把旧 REST 值伪装成更新鲜的运行态。
                let live_status = previous.live_status;
                let status = if previous.has_ws_status {
                    previous.status
                } else {
                    parse_plan_status(&detail.status)
                };
                let last_version = previous.last_version.max(
                    self.last_versions.get(&plan_id).copied().unwrap_or(0),
                );
                self.selected = Some(SelectedPlan {
                    plan_id,
                    status,
                    live_status,
                    detail,
                    last_version,
                    has_ws_status: previous.has_ws_status,
                });
                return;
            }
        }

        let last_version = self.last_versions.get(&plan_id).copied().unwrap_or(0);
        self.selected = Some(SelectedPlan {
            plan_id,
            status: parse_plan_status(&detail.status),
            live_status: HashMap::new(),
            detail,
            last_version,
            has_ws_status: false,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn detail_with(plan_id: &str, status: &str, steps: &[(&str, &str)]) -> PlanDetail {
        let raw_steps: Vec<serde_json::Value> = steps
            .iter()
            .map(|(id, st)| {
                serde_json::json!({"step_id": id, "order": 1, "description": "d", "tool": "t", "status": st})
            })
            .collect();
        let raw = serde_json::json!({
            "plan_id": plan_id, "design_id": "d", "status": status,
            "segments": [{"segment_id": "s", "description": "seg", "steps": raw_steps}],
            "steps": raw_steps,
        });
        serde_json::from_value(raw).unwrap()
    }

    #[test]
    fn plan_update_applies_and_fences_versions() {
        let mut st = GuiState::default();
        st.set_plan_detail(detail_with("p1", "Pending", &[("s1", "Pending")]));
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

        // 计划不匹配则忽略，但版本仍记账，切换后仍能围栏
        assert!(!st.apply_plan_update("other", "Failed", &[], 9));
        assert_eq!(st.last_versions.get("other"), Some(&9));
    }

    #[test]
    fn detail_refresh_preserves_live_status_and_version() {
        let mut st = GuiState::default();
        st.set_plan_detail(detail_with("p1", "Pending", &[("s1", "Pending")]));
        assert!(st.apply_plan_update("p1", "Running", &[("s1".into(), "Running".into())], 7));
        st.set_plan_detail(detail_with("p1", "Pending", &[("s1", "Pending")]));
        let selected = st.selected.as_ref().unwrap();
        assert_eq!(selected.status, PlanStatus::Running);
        assert_eq!(selected.last_version, 7);
        assert_eq!(selected.step_status("s1", "Pending"), StepStatus::Running);
    }

    #[test]
    fn progress_excludes_rolled_back() {
        let mut st = GuiState::default();
        st.set_plan_detail(detail_with("p1", "RolledBack", &[("s1", "RolledBack"), ("s2", "Success"), ("s3", "Skipped")]));
        assert_eq!(st.selected.as_ref().unwrap().progress(), (2, 3));
    }

    #[test]
    fn logs_are_bounded_and_keep_newest_lines() {
        let mut st = GuiState::default();
        for i in 0..1500 {
            st.push_log("info", format!("line {i}"));
        }
        assert_eq!(st.logs.len(), 1000);
        assert!(st.logs[0].message.ends_with("500"));
        assert!(st.logs[999].message.ends_with("1499"));
    }
}
