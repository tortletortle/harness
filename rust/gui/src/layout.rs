//! Local layout preferences: never change execution mode, permissions, or backend state.
use crate::{theme, types::StepStatus};
use serde::{Deserialize, Serialize};

pub const STORAGE_KEY: &str = "eas.layout.v1";

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LayoutMode {
    Focus,
    Timeline,
    #[default]
    #[serde(other)]
    Workbench,
}

impl LayoutMode {
    pub const ALL: [Self; 3] = [Self::Focus, Self::Workbench, Self::Timeline];

    pub fn label(self) -> &'static str {
        match self {
            Self::Focus => "Focus · 专注",
            Self::Workbench => "Workbench · 工作台",
            Self::Timeline => "Timeline · 时间线",
        }
    }

    pub fn description(self) -> &'static str {
        match self {
            Self::Focus => "聚焦当前任务，默认收起项目、输出与助手；审批队列仍然可见。",
            Self::Workbench => "保留经典工作台，项目与输出常驻，适合开发和调试。",
            Self::Timeline => "按计划步骤顺序串联真实状态；不是持久化事件历史。",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct PanelVisibility {
    pub navigation: bool,
    pub logs: bool,
    pub assistant: bool,
}

impl Default for PanelVisibility {
    fn default() -> Self {
        Self {
            navigation: true,
            logs: true,
            assistant: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct LayoutPreferences {
    pub mode: LayoutMode,
    focus: PanelVisibility,
    workbench: PanelVisibility,
    timeline: PanelVisibility,
}

impl Default for LayoutPreferences {
    fn default() -> Self {
        Self {
            mode: LayoutMode::Workbench,
            focus: PanelVisibility {
                navigation: false,
                logs: false,
                assistant: false,
            },
            workbench: PanelVisibility::default(),
            timeline: PanelVisibility::default(),
        }
    }
}

impl LayoutPreferences {
    pub fn current(&self) -> PanelVisibility {
        match self.mode {
            LayoutMode::Focus => self.focus,
            LayoutMode::Workbench => self.workbench,
            LayoutMode::Timeline => self.timeline,
        }
    }

    pub fn remember(&mut self, panels: PanelVisibility) {
        match self.mode {
            LayoutMode::Focus => self.focus = panels,
            LayoutMode::Workbench => self.workbench = panels,
            LayoutMode::Timeline => self.timeline = panels,
        }
    }
}

/// Compact and timeline rows reuse the same controls and state guards as classic cards.
pub fn step_card<R>(
    ui: &mut egui::Ui,
    mode: LayoutMode,
    index: usize,
    total: usize,
    status: StepStatus,
    content: impl FnOnce(&mut egui::Ui) -> R,
) -> R {
    if mode == LayoutMode::Workbench {
        return theme::card(ui, content);
    }
    let frame = egui::Frame::default()
        .fill(theme::surface())
        .stroke(egui::Stroke::new(1.0, theme::border()))
        .corner_radius(egui::CornerRadius::same(6))
        .inner_margin(egui::Margin::same(if theme::is_compact() { 8 } else { 12 }));
    if mode == LayoutMode::Focus {
        return frame.show(ui, content).inner;
    }
    ui.horizontal_top(|ui| {
        let (rail, _) = ui.allocate_exact_size(egui::vec2(30.0, 28.0), egui::Sense::hover());
        let response = frame.show(ui, |ui| {
            ui.set_min_width(ui.available_width().max(0.0));
            content(ui)
        });
        let (_, color) = theme::step_marker(status);
        let center = egui::pos2(rail.center().x, rail.top() + 14.0);
        let painter = ui.painter();
        if index > 0 {
            painter.line_segment(
                [egui::pos2(center.x, rail.top() - 6.0), center],
                egui::Stroke::new(1.0, theme::border_strong()),
            );
        }
        if index + 1 < total {
            painter.line_segment(
                [
                    center,
                    egui::pos2(center.x, response.response.rect.bottom() + 6.0),
                ],
                egui::Stroke::new(1.0, theme::border_strong()),
            );
        }
        painter.circle_filled(center, 12.0, theme::surface());
        painter.circle_stroke(center, 12.0, egui::Stroke::new(1.5, color));
        painter.text(
            center,
            egui::Align2::CENTER_CENTER,
            index + 1,
            egui::FontId::monospace(11.0),
            color,
        );
        response.inner
    })
    .inner
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_preferences_preserve_classic_layout() {
        let prefs: LayoutPreferences = serde_json::from_str("{}").unwrap();
        assert_eq!(prefs, LayoutPreferences::default());
        assert_eq!(prefs.mode, LayoutMode::Workbench);
        assert_eq!(prefs.current(), PanelVisibility::default());
    }

    #[test]
    fn unknown_future_mode_falls_back_without_losing_other_preferences() {
        let prefs: LayoutPreferences =
            serde_json::from_str(r#"{"mode":"future_mode","workbench":{"logs":false}}"#).unwrap();
        assert_eq!(prefs.mode, LayoutMode::Workbench);
        assert!(!prefs.current().logs);
        assert!(prefs.current().navigation);
    }

    #[test]
    fn each_layout_round_trips_with_its_own_panels() {
        let mut prefs = LayoutPreferences::default();
        for mode in LayoutMode::ALL {
            prefs.mode = mode;
            prefs.remember(PanelVisibility {
                navigation: mode != LayoutMode::Focus,
                logs: mode != LayoutMode::Workbench,
                assistant: mode == LayoutMode::Timeline,
            });
        }
        let json = serde_json::to_string(&prefs).unwrap();
        assert_eq!(
            serde_json::from_str::<LayoutPreferences>(&json).unwrap(),
            prefs
        );
        prefs.mode = LayoutMode::Focus;
        assert!(!prefs.current().navigation);
        assert!(prefs.current().logs);
        prefs.mode = LayoutMode::Workbench;
        assert!(!prefs.current().logs);
        prefs.mode = LayoutMode::Timeline;
        assert!(prefs.current().assistant);
    }

    #[test]
    fn focus_defaults_do_not_leak_into_other_layouts() {
        let mut prefs = LayoutPreferences {
            mode: LayoutMode::Focus,
            ..Default::default()
        };
        assert_eq!(
            prefs.current(),
            PanelVisibility {
                navigation: false,
                logs: false,
                assistant: false
            }
        );
        prefs.remember(PanelVisibility {
            assistant: true,
            ..prefs.current()
        });
        prefs.mode = LayoutMode::Timeline;
        assert!(!prefs.current().assistant);
        assert!(prefs.current().logs);
    }

    #[test]
    fn step_rows_render_in_all_modes_without_a_backend() {
        let ctx = egui::Context::default();
        let mut rendered = 0;
        let mut output = ctx.run_ui(egui::RawInput::default(), |ui| {
            for mode in LayoutMode::ALL {
                for (index, status) in [
                    StepStatus::Success,
                    StepStatus::Running,
                    StepStatus::Failed,
                    StepStatus::Pending,
                ]
                .into_iter()
                .enumerate()
                {
                    step_card(ui, mode, index, 4, status, |ui| {
                        ui.label("真实步骤状态");
                    });
                    rendered += 1;
                }
            }
        });
        // Headless test: font textures are uploaded but never applied, so
        // discard the deltas explicitly instead of dropping them unhandled.
        output.textures_delta.clear();
        assert!(rendered >= 12);
    }
}
