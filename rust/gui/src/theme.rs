//! 视觉主题：浅色 / 暗色双主题，集中全部视觉令牌。
//! 调色板以函数形式暴露（内部读全局暗色标志），只改观感，不改交互语义。

use crate::types::{AuditResult, PlanStatus, StepStatus};
use egui::{Color32, CornerRadius, FontId, Margin, Response, RichText, Shadow, Stroke, Ui};
use std::sync::atomic::{AtomicBool, Ordering};

/// 全局暗色标志：UI 线程写、渲染读，Relaxed 足够。
static DARK: AtomicBool = AtomicBool::new(false);
/// 全局紧凑密度标志：高强度用户可切换到 compact 档。
static COMPACT: AtomicBool = AtomicBool::new(false);

pub fn is_dark() -> bool {
    DARK.load(Ordering::Relaxed)
}

pub fn set_dark(dark: bool) {
    DARK.store(dark, Ordering::Relaxed);
}

pub fn is_compact() -> bool {
    COMPACT.load(Ordering::Relaxed)
}

pub fn set_compact(compact: bool) {
    COMPACT.store(compact, Ordering::Relaxed);
}

// ---------- 色板（浅色 / 暗色成对） ----------

macro_rules! palette {
    ($name:ident, $light:expr, $dark:expr) => {
        #[inline]
        pub fn $name() -> Color32 {
            if is_dark() {
                $dark
            } else {
                $light
            }
        }
    };
}

palette!(
    page_bg,
    Color32::from_rgb(243, 247, 246),
    Color32::from_rgb(9, 13, 19)
);
palette!(surface, Color32::WHITE, Color32::from_rgb(17, 22, 30));
palette!(
    hover_bg,
    Color32::from_rgb(231, 240, 237),
    Color32::from_rgb(27, 35, 48)
);
palette!(
    border,
    Color32::from_rgb(219, 229, 226),
    Color32::from_rgb(34, 43, 54)
);
palette!(
    border_strong,
    Color32::from_rgb(196, 212, 207),
    Color32::from_rgb(51, 65, 79)
);

palette!(
    accent,
    Color32::from_rgb(8, 123, 105),
    Color32::from_rgb(86, 217, 184)
);
palette!(
    accent_dark,
    Color32::from_rgb(8, 123, 105),
    Color32::from_rgb(20, 134, 111)
);
palette!(
    accent_soft,
    Color32::from_rgb(225, 239, 237),
    Color32::from_rgb(25, 45, 48)
);
palette!(
    accent_text,
    Color32::from_rgb(8, 123, 105),
    Color32::from_rgb(117, 221, 193)
);

palette!(
    text,
    Color32::from_rgb(34, 49, 61),
    Color32::from_rgb(236, 244, 242)
);
palette!(
    text_sec,
    Color32::from_rgb(94, 109, 124),
    Color32::from_rgb(148, 164, 173)
);
palette!(
    text_faint,
    Color32::from_rgb(122, 138, 150),
    Color32::from_rgb(101, 114, 127)
);

palette!(
    ok,
    Color32::from_rgb(22, 163, 74),
    Color32::from_rgb(52, 199, 106)
);
palette!(
    ok_text,
    Color32::from_rgb(21, 128, 61),
    Color32::from_rgb(110, 218, 155)
);
palette!(
    ok_soft,
    Color32::from_rgb(220, 252, 231),
    Color32::from_rgb(22, 52, 38)
);
palette!(
    warn_text,
    Color32::from_rgb(180, 83, 9),
    Color32::from_rgb(240, 177, 90)
);
palette!(
    warn_soft,
    Color32::from_rgb(254, 243, 199),
    Color32::from_rgb(64, 50, 22)
);
palette!(
    danger,
    Color32::from_rgb(220, 38, 38),
    Color32::from_rgb(239, 88, 88)
);
palette!(
    danger_text,
    Color32::from_rgb(185, 28, 28),
    Color32::from_rgb(247, 141, 141)
);
palette!(
    danger_soft,
    Color32::from_rgb(254, 226, 226),
    Color32::from_rgb(64, 28, 30)
);
palette!(
    danger_border,
    Color32::from_rgb(252, 165, 165),
    Color32::from_rgb(120, 52, 56)
);
palette!(
    info_soft,
    Color32::from_rgb(225, 239, 237),
    Color32::from_rgb(21, 42, 40)
);
palette!(
    info_text,
    Color32::from_rgb(8, 123, 105),
    Color32::from_rgb(117, 221, 193)
);

palette!(
    log_bg,
    Color32::from_rgb(243, 247, 246),
    Color32::from_rgb(9, 13, 19)
);
palette!(
    notice_bg,
    Color32::from_rgb(255, 251, 235),
    Color32::from_rgb(56, 48, 24)
);

/// Activity Bar 底色在两种主题下都保持深色，维持工作台的锚点感。
pub fn activity_bg() -> Color32 {
    if is_dark() {
        Color32::from_rgb(14, 19, 27)
    } else {
        Color32::from_rgb(20, 28, 36)
    }
}

// ---------- 全局安装 ----------

/// 在字体加载后调用；主题切换时重复调用即可生效。
pub fn install(ctx: &egui::Context) {
    ctx.all_styles_mut(|style| {
        let v = &mut style.visuals;
        v.dark_mode = is_dark();
        v.override_text_color = Some(text());
        v.panel_fill = page_bg();
        v.window_fill = surface();
        v.faint_bg_color = hover_bg();
        v.extreme_bg_color = if is_dark() { log_bg() } else { surface() };
        v.code_bg_color = hover_bg();
        v.window_stroke = Stroke::new(1.0_f32, border());
        v.window_corner_radius = CornerRadius::same(10);
        v.window_shadow = Shadow {
            offset: [0, 6],
            blur: 24,
            spread: 0,
            color: Color32::from_black_alpha(if is_dark() { 96 } else { 28 }),
        };
        v.popup_shadow = Shadow {
            offset: [0, 3],
            blur: 12,
            spread: 0,
            color: Color32::from_black_alpha(if is_dark() { 80 } else { 20 }),
        };
        v.hyperlink_color = accent_text();
        v.selection = egui::style::Selection {
            bg_fill: Color32::from_rgba_unmultiplied(86, 217, 184, if is_dark() { 90 } else { 45 }),
            stroke: Stroke::new(1.0_f32, accent()),
        };

        let w = &mut v.widgets;
        // 标签/文字类：不带边框盒子
        w.noninteractive.bg_fill = Color32::TRANSPARENT;
        w.noninteractive.weak_bg_fill = Color32::TRANSPARENT;
        w.noninteractive.bg_stroke = Stroke::NONE;
        w.noninteractive.fg_stroke = Stroke::new(1.0_f32, text());
        w.noninteractive.corner_radius = CornerRadius::same(6);
        // 按钮/输入类：面底 + 弱描边 + 圆角
        w.inactive.bg_fill = surface();
        w.inactive.weak_bg_fill = surface();
        w.inactive.bg_stroke = Stroke::new(1.0_f32, border());
        w.inactive.fg_stroke = Stroke::new(1.0_f32, text_sec());
        w.inactive.corner_radius = CornerRadius::same(8);
        w.hovered.weak_bg_fill = hover_bg();
        w.hovered.bg_stroke = Stroke::new(1.0_f32, border_strong());
        w.hovered.fg_stroke = Stroke::new(1.0_f32, text());
        w.hovered.corner_radius = CornerRadius::same(8);
        w.active.weak_bg_fill = accent_soft();
        w.active.bg_stroke = Stroke::new(1.0_f32, accent());
        w.active.fg_stroke = Stroke::new(1.0_f32, accent_dark());
        w.active.corner_radius = CornerRadius::same(8);
        w.active.expansion = 1.0;

        let sp = &mut style.spacing;
        if is_compact() {
            sp.item_spacing = egui::vec2(8.0, 5.0);
            sp.button_padding = egui::vec2(10.0, 4.0);
            sp.interact_size = egui::vec2(52.0, 24.0);
            sp.indent = 14.0;
        } else {
            sp.item_spacing = egui::vec2(10.0, 8.0);
            sp.button_padding = egui::vec2(12.0, 6.0);
            sp.interact_size = egui::vec2(60.0, 28.0);
            sp.indent = 18.0;
        }
        sp.menu_margin = Margin::same(8);

        let (heading, body, small, mono) = if is_compact() {
            (16.0, 13.0, 11.5, 12.0)
        } else {
            (17.0, 14.0, 12.0, 13.0)
        };
        style.text_styles = [
            (egui::TextStyle::Heading, FontId::proportional(heading)),
            (egui::TextStyle::Body, FontId::proportional(body)),
            (egui::TextStyle::Button, FontId::proportional(body)),
            (egui::TextStyle::Small, FontId::proportional(small)),
            (egui::TextStyle::Monospace, FontId::monospace(mono)),
        ]
        .into();
    });
}

// ---------- 通用组件 ----------

/// 卡片 Frame：面底圆角 + 极浅投影（层次感的主要来源）。
pub fn card_frame() -> egui::Frame {
    egui::Frame::default()
        .fill(surface())
        .stroke(Stroke::new(1.0_f32, border()))
        .corner_radius(CornerRadius::same(10))
        .inner_margin(Margin::same(if is_compact() { 10 } else { 14 }))
        .shadow(Shadow {
            offset: [0, 1],
            blur: 3,
            spread: 0,
            color: Color32::from_black_alpha(if is_dark() { 60 } else { 10 }),
        })
}

/// 白底圆角卡片，承载一个内容块。
pub fn card<R>(ui: &mut Ui, content: impl FnOnce(&mut Ui) -> R) -> R {
    card_frame().show(ui, |ui| content(ui)).inner
}

/// 分区小标题：弱化大写风格标签。
pub fn section_label(ui: &mut Ui, label: &str) {
    ui.label(RichText::new(label).size(11.0).strong().color(text_faint()));
}

/// 空状态：大图标 + 引导文案，居中展示。
pub fn empty_state(ui: &mut Ui, icon: &str, title: &str, hint: &str) {
    ui.add_space(36.0);
    ui.vertical_centered(|ui| {
        ui.label(RichText::new(icon).size(44.0).color(text_faint()));
        ui.add_space(8.0);
        ui.label(RichText::new(title).size(15.0).strong().color(text_sec()));
        if !hint.is_empty() {
            ui.add_space(2.0);
            ui.label(RichText::new(hint).size(12.5).color(text_faint()));
        }
    });
    ui.add_space(24.0);
}

/// 选中行左侧 3px 主色竖条（VS Code 风格选中锚点）。
pub fn selection_bar(ui: &Ui, rect: egui::Rect) {
    let bar = egui::Rect::from_min_size(rect.min, egui::vec2(3.0, rect.height()));
    ui.painter().rect_filled(bar, 2.0, accent());
}

/// 卡片顶部 2px 不确定进度动画：一段亮色光带左右往返，标记"运行中"。
/// 内部触发 request_repaint 驱动动画帧。
pub fn running_indicator(ui: &Ui, rect: egui::Rect) {
    let t = ui.input(|i| i.time) as f32;
    // 三角波 0→1→0，周期 2.4s；光带宽约为卡片宽度的 28%。
    let phase = (t / 1.2) % 2.0;
    let pos = if phase < 1.0 { phase } else { 2.0 - phase };
    let track = egui::Rect::from_min_max(
        egui::pos2(rect.left() + 8.0, rect.top() + 1.0),
        egui::pos2(rect.right() - 8.0, rect.top() + 3.0),
    );
    let band_w = (track.width() * 0.28).max(24.0);
    let x0 = track.left() + (track.width() - band_w) * pos;
    let band = egui::Rect::from_min_max(
        egui::pos2(x0, track.top()),
        egui::pos2(x0 + band_w, track.bottom()),
    );
    let painter = ui.painter();
    painter.rect_filled(track, 1.0, accent().gamma_multiply(0.18));
    painter.rect_filled(band, 1.0, accent());
    ui.ctx().request_repaint();
}

/// 主操作按钮（填充主色）。
pub fn primary_button(text: impl Into<String>) -> egui::Button<'static> {
    egui::Button::new(RichText::new(text.into()).color(Color32::WHITE).strong())
        .fill(accent_dark())
        .corner_radius(CornerRadius::same(8))
        .min_size(egui::vec2(0.0, 30.0))
}

/// 危险主按钮（填充红）。
pub fn danger_button(text: impl Into<String>) -> egui::Button<'static> {
    egui::Button::new(RichText::new(text.into()).color(Color32::WHITE).strong())
        .fill(danger())
        .corner_radius(CornerRadius::same(8))
        .min_size(egui::vec2(0.0, 30.0))
}

/// 危险次要按钮（面底红描边）。
pub fn danger_outline_button(text: impl Into<String>) -> egui::Button<'static> {
    egui::Button::new(RichText::new(text.into()).color(danger_text()))
        .fill(surface())
        .stroke(Stroke::new(1.0_f32, danger_border()))
        .corner_radius(CornerRadius::same(8))
        .min_size(egui::vec2(0.0, 30.0))
}

/// 次要按钮（面底描边，随全局控件样式）。
pub fn secondary_button(text: impl Into<String>) -> egui::Button<'static> {
    egui::Button::new(RichText::new(text.into()))
        .fill(surface())
        .corner_radius(CornerRadius::same(8))
        .min_size(egui::vec2(0.0, 30.0))
}

/// Activity Bar 图标按钮：选中态保持可见，避免仅靠悬停猜测当前一级视图。
pub fn activity_button(text: impl Into<String>, active: bool) -> egui::Button<'static> {
    let foreground = if active {
        Color32::WHITE
    } else {
        Color32::from_rgb(190, 195, 205)
    };
    let background = if active {
        Color32::from_rgb(29, 69, 62)
    } else {
        Color32::TRANSPARENT
    };
    egui::Button::new(RichText::new(text.into()).color(foreground).size(17.0))
        .fill(background)
        .stroke(Stroke::NONE)
        .corner_radius(CornerRadius::same(6))
}

/// 药丸徽章：状态/计数的轻量标签。
pub fn badge(ui: &mut Ui, text: &str, fg: Color32, bg: Color32) -> Response {
    egui::Frame::default()
        .fill(bg)
        .corner_radius(CornerRadius::same(9))
        .inner_margin(Margin::symmetric(8, 2))
        .show(ui, |ui| {
            ui.set_min_height(18.0);
            ui.label(RichText::new(text).color(fg).size(12.0).strong());
        })
        .response
}

/// 行内错误提示（红底圆角条）。
pub fn error_inline(ui: &mut Ui, msg: &str) {
    egui::Frame::default()
        .fill(danger_soft())
        .corner_radius(CornerRadius::same(6))
        .inner_margin(Margin::symmetric(8, 4))
        .show(ui, |ui| {
            ui.label(RichText::new(msg).color(danger_text()).size(13.0));
        });
}

// ---------- 状态可视化 ----------

/// 计划状态 → (中文标签, 文字色, 徽章底色)
pub fn plan_status_visual(status: PlanStatus) -> (&'static str, Color32, Color32) {
    match status {
        PlanStatus::Draft => ("草稿", text_sec(), hover_bg()),
        PlanStatus::Pending => ("待执行", warn_text(), warn_soft()),
        PlanStatus::Approved => ("已批准", accent_text(), accent_soft()),
        PlanStatus::Running => ("运行中", accent_text(), accent_soft()),
        PlanStatus::Paused => ("已暂停", warn_text(), warn_soft()),
        PlanStatus::Completed => ("已完成", ok_text(), ok_soft()),
        PlanStatus::Failed => ("失败", danger_text(), danger_soft()),
        PlanStatus::RolledBack => ("已回滚", info_text(), info_soft()),
    }
}

/// 步骤状态 → (图标, 颜色)；图标来自 Phosphor 字体。
pub fn step_marker(live: StepStatus) -> (&'static str, Color32) {
    match live {
        StepStatus::Success => (egui_phosphor::regular::CHECK_CIRCLE, ok()),
        StepStatus::Failed => (egui_phosphor::regular::X_CIRCLE, danger()),
        StepStatus::Running | StepStatus::Retry => (egui_phosphor::regular::CIRCLE_NOTCH, accent()),
        StepStatus::Skipped => (egui_phosphor::regular::MINUS_CIRCLE, text_faint()),
        StepStatus::RolledBack => (
            egui_phosphor::regular::ARROW_COUNTER_CLOCKWISE,
            Color32::from_rgb(217, 119, 6),
        ),
        StepStatus::Pending => (egui_phosphor::regular::CIRCLE, text_faint()),
    }
}

/// 审计结果 → (中文标签, 颜色)
pub fn audit_result_visual(result: AuditResult) -> (&'static str, Color32) {
    match result {
        AuditResult::Success => ("成功", ok_text()),
        AuditResult::Failed => ("失败", danger_text()),
        AuditResult::Blocked => ("拦截", warn_text()),
        AuditResult::Unknown => ("未知", text_sec()),
    }
}

/// 日志底色上的日志级别配色。
pub fn log_color(level: &str) -> Color32 {
    match level {
        "error" => danger(),
        "warning" => warn_text(),
        "success" => ok_text(),
        _ => text_sec(),
    }
}
