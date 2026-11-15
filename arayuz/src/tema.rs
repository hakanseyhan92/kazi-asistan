use eframe::egui::{self, Color32, CornerRadius, FontId, Margin, Shadow, Stroke, TextStyle, Theme, ThemePreference};
use serde::{Deserialize, Serialize};

/// Kullanıcının tema tercihi (sistem temasını izle, açık veya koyu).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TemaTercihi {
    #[default]
    Sistem,
    Acik,
    Koyu,
}

impl TemaTercihi {
    pub fn etiket(self) -> &'static str {
        match self {
            Self::Sistem => "🌓 Sistem",
            Self::Acik => "☀ Açık",
            Self::Koyu => "🌙 Koyu",
        }
    }

    pub fn sonraki(self) -> Self {
        match self {
            Self::Sistem => Self::Acik,
            Self::Acik => Self::Koyu,
            Self::Koyu => Self::Sistem,
        }
    }

    pub fn egui_tercihi(self) -> ThemePreference {
        match self {
            Self::Sistem => ThemePreference::System,
            Self::Acik => ThemePreference::Light,
            Self::Koyu => ThemePreference::Dark,
        }
    }
}

/// Uygulama genelindeki tek renk paleti (koyu ve açık temada yüksek okunabilirlik).
#[derive(Debug, Clone, Copy)]
pub struct Palet {
    pub koyu: bool,
    pub vurgu: Color32,
    pub vurgu_dolgu: Color32,
    pub vurgu_yazi: Color32,
    pub basari: Color32,
    pub basari_dolgu: Color32,
    pub uyari: Color32,
    pub uyari_dolgu: Color32,
    pub hata: Color32,
    pub hata_dolgu: Color32,
    pub kapali: Color32,
    pub kart_arka: Color32,
    pub kart_kenar: Color32,
    pub bacak_arka: Color32,
    pub golge: Shadow,
}

pub const VURGU_RENGI: Color32 = Color32::from_rgb(79, 140, 255); // #4F8CFF

pub fn palet(visuals: &egui::Visuals) -> Palet {
    if visuals.dark_mode {
        Palet {
            koyu: true,
            vurgu: VURGU_RENGI,
            vurgu_dolgu: Color32::from_rgba_unmultiplied(79, 140, 255, 36),
            vurgu_yazi: Color32::from_rgb(138, 180, 255),
            basari: Color32::from_rgb(56, 211, 128),
            basari_dolgu: Color32::from_rgba_unmultiplied(46, 204, 113, 28),
            uyari: Color32::from_rgb(245, 190, 55),
            uyari_dolgu: Color32::from_rgba_unmultiplied(243, 156, 18, 28),
            hata: Color32::from_rgb(240, 92, 92),
            hata_dolgu: Color32::from_rgba_unmultiplied(231, 76, 60, 34),
            kapali: Color32::from_rgb(154, 164, 178),
            kart_arka: Color32::from_rgb(30, 34, 43),
            kart_kenar: Color32::from_rgb(56, 63, 78),
            bacak_arka: Color32::from_rgb(24, 28, 36),
            golge: Shadow {
                offset: [0, 2],
                blur: 8,
                spread: 0,
                color: Color32::from_black_alpha(65),
            },
        }
    } else {
        Palet {
            koyu: false,
            vurgu: Color32::from_rgb(47, 116, 245),
            vurgu_dolgu: Color32::from_rgba_unmultiplied(47, 116, 245, 26),
            vurgu_yazi: Color32::from_rgb(29, 89, 209),
            basari: Color32::from_rgb(21, 148, 71),
            basari_dolgu: Color32::from_rgba_unmultiplied(22, 163, 74, 24),
            uyari: Color32::from_rgb(176, 108, 0),
            uyari_dolgu: Color32::from_rgba_unmultiplied(217, 119, 6, 26),
            hata: Color32::from_rgb(204, 42, 42),
            hata_dolgu: Color32::from_rgba_unmultiplied(220, 38, 38, 22),
            kapali: Color32::from_rgb(100, 110, 125),
            kart_arka: Color32::from_rgb(255, 255, 255),
            kart_kenar: Color32::from_rgb(214, 220, 230),
            bacak_arka: Color32::from_rgb(243, 246, 251),
            golge: Shadow {
                offset: [0, 2],
                blur: 8,
                spread: 0,
                color: Color32::from_black_alpha(18),
            },
        }
    }
}

pub fn katman_rengi(visuals: &egui::Visuals, katman: &str) -> Color32 {
    let p = palet(visuals);
    match katman {
        "K0" => p.basari,
        "K1" => p.vurgu_yazi,
        "K2" => p.uyari,
        "K3" => p.hata,
        _ => p.kapali,
    }
}

fn stil_yapilandir(style: &mut egui::Style, koyu: bool) {
    // Hiyerarşik tipografi: ekran başlığı 22, bölüm 16, gövde 14
    style
        .text_styles
        .insert(TextStyle::Heading, FontId::proportional(22.0));
    style
        .text_styles
        .insert(TextStyle::Body, FontId::proportional(14.0));
    style
        .text_styles
        .insert(TextStyle::Button, FontId::proportional(14.0));
    style
        .text_styles
        .insert(TextStyle::Small, FontId::proportional(12.0));
    style
        .text_styles
        .insert(TextStyle::Monospace, FontId::monospace(13.0));

    style.spacing.item_spacing = egui::vec2(8.0, 6.0);
    style.spacing.button_padding = egui::vec2(10.0, 5.0);
    style.spacing.window_margin = Margin::same(14);

    let mut v = if koyu {
        let mut d = egui::Visuals::dark();
        d.panel_fill = Color32::from_rgb(20, 23, 30);
        d.window_fill = Color32::from_rgb(28, 32, 41);
        d.extreme_bg_color = Color32::from_rgb(15, 18, 24);
        d.faint_bg_color = Color32::from_rgba_unmultiplied(255, 255, 255, 7);
        d.widgets.inactive.bg_fill = Color32::from_rgb(38, 43, 54);
        d.widgets.hovered.bg_fill = Color32::from_rgb(48, 54, 68);
        d.widgets.active.bg_fill = Color32::from_rgb(56, 64, 80);
        d
    } else {
        let mut l = egui::Visuals::light();
        l.panel_fill = Color32::from_rgb(244, 246, 250);
        l.window_fill = Color32::from_rgb(255, 255, 255);
        l.extreme_bg_color = Color32::from_rgb(236, 240, 246);
        l.faint_bg_color = Color32::from_rgba_unmultiplied(15, 23, 42, 7);
        l.widgets.inactive.bg_fill = Color32::from_rgb(236, 240, 246);
        l.widgets.hovered.bg_fill = Color32::from_rgb(225, 231, 240);
        l.widgets.active.bg_fill = Color32::from_rgb(213, 221, 234);
        l
    };

    let p = palet(&v);
    v.selection.bg_fill = Color32::from_rgba_unmultiplied(p.vurgu.r(), p.vurgu.g(), p.vurgu.b(), 64);
    v.selection.stroke = Stroke::new(1.2, p.vurgu);
    v.hyperlink_color = p.vurgu_yazi;
    v.window_corner_radius = CornerRadius::same(10);
    v.menu_corner_radius = CornerRadius::same(8);
    v.widgets.noninteractive.corner_radius = CornerRadius::same(6);
    v.widgets.inactive.corner_radius = CornerRadius::same(6);
    v.widgets.hovered.corner_radius = CornerRadius::same(6);
    v.widgets.active.corner_radius = CornerRadius::same(6);
    v.widgets.open.corner_radius = CornerRadius::same(6);

    style.visuals = v;
}

/// Tema tercihini ve modern kart/tipografi stilini her iki tema için uygular.
pub fn tema_uygula(ctx: &egui::Context, tercih: TemaTercihi) {
    ctx.set_theme(tercih.egui_tercihi());
    ctx.style_mut_of(Theme::Dark, |s| stil_yapilandir(s, true));
    ctx.style_mut_of(Theme::Light, |s| stil_yapilandir(s, false));
}

/// Modern kart çerçevesi: yuvarlak köşe (10 px), 14 px iç boşluk, ince kenarlık, hafif gölge.
pub fn kart_cerceve(ui: &egui::Ui) -> egui::Frame {
    let p = palet(ui.visuals());
    egui::Frame::NONE
        .fill(p.kart_arka)
        .stroke(Stroke::new(1.0, p.kart_kenar))
        .corner_radius(CornerRadius::same(10))
        .inner_margin(Margin::same(14))
        .shadow(p.golge)
}

/// Tıklanabilir özet kartı çerçevesi.
pub fn ozet_kart_cerceve(ui: &egui::Ui, secili: bool, vurgu_renk: Option<Color32>) -> egui::Frame {
    let p = palet(ui.visuals());
    let kenar_renk = if secili {
        vurgu_renk.unwrap_or(p.vurgu)
    } else {
        p.kart_kenar
    };
    let dolgu = if secili {
        let r = vurgu_renk.unwrap_or(p.vurgu);
        Color32::from_rgba_unmultiplied(r.r(), r.g(), r.b(), if p.koyu { 30 } else { 20 })
    } else {
        p.kart_arka
    };
    egui::Frame::NONE
        .fill(dolgu)
        .stroke(Stroke::new(if secili { 1.5 } else { 1.0 }, kenar_renk))
        .corner_radius(CornerRadius::same(10))
        .inner_margin(Margin::symmetric(14, 10))
        .shadow(p.golge)
}
