//! 视觉主题：干净现代的浅色风格（白卡片 / 浅灰页面底 / 蓝主色 / 药丸徽章）。
//! 全部视觉令牌集中于此；只改观感，不改任何交互语义。

use crate::types::{AuditResult, PlanStatus, StepStatus};
use egui::{Color32, FontId, Margin, Response, RichText, Rounding, Stroke, Ui};

// ---------- 色板 ----------

pub const PAGE_BG: Color32 = Color32::from_rgb(246, 247, 249); // 页面底
pub const SURFACE: Color32 = Color32::WHITE; // 卡片/面板
pub const HOVER_BG: Color32 = Color32::from_rgb(243, 244, 246); // 悬停/斑马纹
pub const BORDER: Color32 = Color32::from_rgb(229, 231, 235); // 弱描边
pub const BORDER_STRONG: Color32 = Color32::from_rgb(209, 213, 219); // 强描边

pub const ACCENT: Color32 = Color32::from_rgb(37, 99, 235); // 主色
pub const ACCENT_DARK: Color32 = Color32::from_rgb(29, 78, 216); // 主色 hover
pub const ACCENT_SOFT: Color32 = Color32::from_rgb(219, 234, 254); // 主色浅底
pub const ACCENT_TEXT: Color32 = Color32::from_rgb(29, 78, 216); // 主色文字

pub const TEXT: Color32 = Color32::from_rgb(28, 35, 48); // 主文字
pub const TEXT_SEC: Color32 = Color32::from_rgb(107, 114, 128); // 次级文字
pub const TEXT_FAINT: Color32 = Color32::from_rgb(156, 163, 175); // 弱化文字

pub const OK: Color32 = Color32::from_rgb(22, 163, 74);
pub const OK_TEXT: Color32 = Color32::from_rgb(21, 128, 61);
pub const OK_SOFT: Color32 = Color32::from_rgb(220, 252, 231);
pub const WARN_TEXT: Color32 = Color32::from_rgb(180, 83, 9);
pub const WARN_SOFT: Color32 = Color32::from_rgb(254, 243, 199);
pub const DANGER: Color32 = Color32::from_rgb(220, 38, 38);
pub const DANGER_TEXT: Color32 = Color32::from_rgb(185, 28, 28);
pub const DANGER_SOFT: Color32 = Color32::from_rgb(254, 226, 226);
pub const DANGER_BORDER: Color32 = Color32::from_rgb(252, 165, 165);
pub const INFO_SOFT: Color32 = Color32::from_rgb(237, 233, 254);
pub const INFO_TEXT: Color32 = Color32::from_rgb(109, 40, 217);

pub const LOG_BG: Color32 = Color32::from_rgb(243, 244, 246); // 日志浅底（融入浅色主题）
pub const NOTICE_BG: Color32 = Color32::from_rgb(255, 251, 235); // 通知浅琥珀底

// ---------- 全局安装 ----------

/// 在字体加载后调用一次：覆盖 egui 默认样式为浅色现代风。
pub fn install(ctx: &egui::Context) {
    ctx.style_mut(|style| {
        let v = &mut style.visuals;
        v.dark_mode = false;
        v.override_text_color = Some(TEXT);
        v.panel_fill = PAGE_BG;
        v.window_fill = SURFACE;
        v.faint_bg_color = HOVER_BG;
        v.extreme_bg_color = SURFACE;
        v.code_bg_color = HOVER_BG;
        v.window_stroke = Stroke::new(1.0, BORDER);
        v.window_rounding = Rounding::same(10.0);
        v.hyperlink_color = ACCENT_TEXT;
        v.selection = egui::style::Selection {
            bg_fill: Color32::from_rgba_unmultiplied(37, 99, 235, 45),
            stroke: Stroke::new(1.0, ACCENT),
        };

        let w = &mut v.widgets;
        // 标签/文字类：不带边框盒子
        w.noninteractive.bg_fill = Color32::TRANSPARENT;
        w.noninteractive.weak_bg_fill = Color32::TRANSPARENT;
        w.noninteractive.bg_stroke = Stroke::NONE;
        w.noninteractive.fg_stroke = Stroke::new(1.0, TEXT);
        w.noninteractive.rounding = Rounding::same(6.0);
        // 按钮/输入类：白底 + 弱描边 + 圆角
        w.inactive.bg_fill = SURFACE;
        w.inactive.weak_bg_fill = SURFACE;
        w.inactive.bg_stroke = Stroke::new(1.0, BORDER);
        w.inactive.fg_stroke = Stroke::new(1.0, TEXT_SEC);
        w.inactive.rounding = Rounding::same(8.0);
        w.hovered.weak_bg_fill = HOVER_BG;
        w.hovered.bg_stroke = Stroke::new(1.0, BORDER_STRONG);
        w.hovered.fg_stroke = Stroke::new(1.0, TEXT);
        w.hovered.rounding = Rounding::same(8.0);
        w.active.weak_bg_fill = ACCENT_SOFT;
        w.active.bg_stroke = Stroke::new(1.0, ACCENT);
        w.active.fg_stroke = Stroke::new(1.0, ACCENT_DARK);
        w.active.rounding = Rounding::same(8.0);
        // 注：egui 0.28 无 widgets.disabled 态（0.29+ 才有），禁用样式由 inactive 派生。

        let sp = &mut style.spacing;
        sp.item_spacing = egui::vec2(10.0, 8.0);
        sp.button_padding = egui::vec2(12.0, 6.0);
        sp.interact_size = egui::vec2(60.0, 28.0);
        sp.indent = 18.0;
        sp.menu_margin = Margin::same(8.0);

        style.text_styles = [
            (egui::TextStyle::Heading, FontId::proportional(19.0)),
            (egui::TextStyle::Body, FontId::proportional(14.0)),
            (egui::TextStyle::Button, FontId::proportional(14.0)),
            (egui::TextStyle::Small, FontId::proportional(12.0)),
            (egui::TextStyle::Monospace, FontId::monospace(13.0)),
        ]
        .into();
    });
}

// ---------- 通用组件 ----------

/// 白底圆角卡片，承载一个内容块。
pub fn card<R>(ui: &mut Ui, content: impl FnOnce(&mut Ui) -> R) -> R {
    card_frame().show(ui, |ui| content(ui)).inner
}

fn card_frame() -> egui::Frame {
    egui::Frame::default()
        .fill(SURFACE)
        .stroke(Stroke::new(1.0, BORDER))
        .rounding(Rounding::same(10.0))
        .inner_margin(Margin::same(12.0))
}

/// 主操作按钮（填充主色）。
pub fn primary_button(text: impl Into<String>) -> egui::Button<'static> {
    egui::Button::new(RichText::new(text.into()).color(Color32::WHITE).strong())
        .fill(ACCENT)
        .rounding(Rounding::same(8.0))
        .min_size(egui::vec2(0.0, 30.0))
}

/// 危险主按钮（填充红）。
pub fn danger_button(text: impl Into<String>) -> egui::Button<'static> {
    egui::Button::new(RichText::new(text.into()).color(Color32::WHITE).strong())
        .fill(DANGER)
        .rounding(Rounding::same(8.0))
        .min_size(egui::vec2(0.0, 30.0))
}

/// 危险次要按钮（白底红描边）。
pub fn danger_outline_button(text: impl Into<String>) -> egui::Button<'static> {
    egui::Button::new(RichText::new(text.into()).color(DANGER_TEXT))
        .fill(SURFACE)
        .stroke(Stroke::new(1.0, DANGER_BORDER))
        .rounding(Rounding::same(8.0))
        .min_size(egui::vec2(0.0, 30.0))
}

/// 次要按钮（白底描边，随全局控件样式）。
pub fn secondary_button(text: impl Into<String>) -> egui::Button<'static> {
    egui::Button::new(RichText::new(text.into()))
        .fill(SURFACE)
        .rounding(Rounding::same(8.0))
        .min_size(egui::vec2(0.0, 30.0))
}

/// 药丸徽章：状态/计数的轻量标签。
pub fn badge(ui: &mut Ui, text: &str, fg: Color32, bg: Color32) -> Response {
    egui::Frame::default()
        .fill(bg)
        .rounding(Rounding::same(9.0))
        .inner_margin(Margin::symmetric(8.0, 2.0))
        .show(ui, |ui| {
            ui.set_min_height(18.0);
            ui.label(RichText::new(text).color(fg).size(12.0).strong());
        })
        .response
}

/// 行内错误提示（红底圆角条）。
pub fn error_inline(ui: &mut Ui, msg: &str) {
    egui::Frame::default()
        .fill(DANGER_SOFT)
        .rounding(Rounding::same(6.0))
        .inner_margin(Margin::symmetric(8.0, 4.0))
        .show(ui, |ui| {
            ui.label(RichText::new(msg).color(DANGER_TEXT).size(13.0));
        });
}

// ---------- 状态可视化 ----------

/// 计划状态 → (中文标签, 文字色, 徽章底色)
pub fn plan_status_visual(status: PlanStatus) -> (&'static str, Color32, Color32) {
    match status {
        PlanStatus::Draft => ("草稿", TEXT_SEC, HOVER_BG),
        PlanStatus::Pending => ("待执行", WARN_TEXT, WARN_SOFT),
        PlanStatus::Approved => ("已批准", ACCENT_TEXT, ACCENT_SOFT),
        PlanStatus::Running => ("运行中", ACCENT_TEXT, ACCENT_SOFT),
        PlanStatus::Paused => ("已暂停", WARN_TEXT, WARN_SOFT),
        PlanStatus::Completed => ("已完成", OK_TEXT, OK_SOFT),
        PlanStatus::Failed => ("失败", DANGER_TEXT, DANGER_SOFT),
        PlanStatus::RolledBack => ("已回滚", INFO_TEXT, INFO_SOFT),
    }
}

/// 步骤状态 → (图标, 颜色)
pub fn step_marker(live: StepStatus) -> (&'static str, Color32) {
    match live {
        StepStatus::Success => ("✓", OK),
        StepStatus::Failed => ("✗", DANGER),
        StepStatus::Running | StepStatus::Retry => ("◐", ACCENT),
        StepStatus::Skipped => ("⊖", TEXT_FAINT),
        StepStatus::RolledBack => ("↩", Color32::from_rgb(217, 119, 6)),
        StepStatus::Pending => ("·", TEXT_FAINT),
    }
}

/// 审计结果 → (中文标签, 颜色)
pub fn audit_result_visual(result: AuditResult) -> (&'static str, Color32) {
    match result {
        AuditResult::Success => ("成功", OK_TEXT),
        AuditResult::Failed => ("失败", DANGER_TEXT),
        AuditResult::Blocked => ("拦截", WARN_TEXT),
        AuditResult::Unknown => ("未知", TEXT_SEC),
    }
}

/// 浅色日志底上的日志级别配色。
pub fn log_color(level: &str) -> Color32 {
    match level {
        "error" => Color32::from_rgb(220, 38, 38),
        "warning" => Color32::from_rgb(180, 83, 9),
        "success" => Color32::from_rgb(21, 128, 61),
        _ => Color32::from_rgb(75, 85, 99),
    }
}
