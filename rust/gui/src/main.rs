/// EAS 2.0 GUI (egui)
///
/// Phase 4实现，现在是占位符
use eframe::egui;

struct EasApp {
    input_text: String,
}

impl Default for EasApp {
    fn default() -> Self {
        Self {
            input_text: String::new(),
        }
    }
}

impl eframe::App for EasApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        egui::CentralPanel::default().show(ctx, |ui| {
            ui.heading("EAS 2.0 - AI任务编排系统");

            ui.separator();

            ui.label("用户需求：");
            ui.text_edit_multiline(&mut self.input_text);

            ui.separator();

            if ui.button("生成设计书").clicked() {
                // TODO: 调用API
                println!("生成设计书: {}", self.input_text);
            }

            ui.separator();

            ui.label("状态：等待实现...");
        });
    }
}

fn main() -> Result<(), eframe::Error> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([800.0, 600.0]),
        ..Default::default()
    };

    eframe::run_native(
        "EAS 2.0",
        options,
        Box::new(|_cc| Ok(Box::new(EasApp::default()))),
    )
}
