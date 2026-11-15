//! "Kayıtlar" ekranı (15.11 kullanıcı isteği): hangi tarayıcı hangi siteye gitti, ne çekti, ne hata verdi —
//! çekirdeğin kayıt defteri (GET /kayitlar). Canlı izlemede 2 sn'de bir yalnız YENİ kayıtlar (sonra_id) çekilir.

use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use eframe::egui::{self, Color32, RichText, Sense};
use egui_extras::{Column, TableBuilder};

use crate::api::{Kayit, KayitOzeti, VeriKaynagi};
use crate::tema;

const AZAMI_KAYIT: usize = 3000;
const CANLI_ARALIK: Duration = Duration::from_secs(2);
const OZET_ARALIK: Duration = Duration::from_secs(15);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SeviyeSuzgeci {
    Tumu,
    UyariHata,
    Hata,
}

impl SeviyeSuzgeci {
    fn sorgu(self) -> &'static str {
        match self {
            SeviyeSuzgeci::Tumu => "",
            SeviyeSuzgeci::UyariHata => "uyari",
            SeviyeSuzgeci::Hata => "hata",
        }
    }
    fn ad(self) -> &'static str {
        match self {
            SeviyeSuzgeci::Tumu => "Tümü",
            SeviyeSuzgeci::UyariHata => "Uyarı + hata",
            SeviyeSuzgeci::Hata => "Yalnız hata",
        }
    }
}

#[derive(Default)]
struct Paylasilan {
    kayitlar: Vec<Kayit>,
    ozet: KayitOzeti,
    hata: Option<String>,
    imza: String,
    yukleniyor: bool,
}

pub struct KayitEkrani {
    ortak: Arc<Mutex<Paylasilan>>,
    pub seviye: SeviyeSuzgeci,
    pub kaynak: String,
    pub site: String,
    pub ara: String,
    pub canli: bool,
    son_istek: Option<Instant>,
    son_ozet: Option<Instant>,
    secili: Option<i64>,
}

impl Default for KayitEkrani {
    fn default() -> Self {
        Self {
            ortak: Arc::default(),
            seviye: SeviyeSuzgeci::Tumu,
            kaynak: String::new(),
            site: String::new(),
            ara: String::new(),
            canli: true,
            son_istek: None,
            son_ozet: None,
            secili: None,
        }
    }
}

/// Süzgeç imzası: değişince liste baştan yüklenir.
pub fn suzgec_imzasi(seviye: SeviyeSuzgeci, kaynak: &str, site: &str, ara: &str) -> String {
    format!("{}|{}|{}|{}", seviye.sorgu(), kaynak, site, ara.trim())
}

/// Yeni kayıtları (en yeni önce) eskilerin önüne ekle, tekrarı at, sınırı aşanı kes.
pub fn birlestir(mut yeni: Vec<Kayit>, eski: &[Kayit], sinir: usize) -> Vec<Kayit> {
    let en_buyuk = eski.first().map(|k| k.id).unwrap_or(0);
    yeni.retain(|k| k.id > en_buyuk);
    yeni.extend(eski.iter().cloned());
    yeni.truncate(sinir);
    yeni
}

/// "2026-11-15T09:42:27.123+03:00" → "09:42:27" (bugün değilse "15.11 09:42:27").
pub fn saat_metni(zaman: &str, bugun: &str) -> String {
    let (tarih, saat) = zaman.split_once('T').unwrap_or((zaman, ""));
    let saat = saat.get(..8).unwrap_or(saat);
    if tarih == bugun || tarih.is_empty() {
        saat.to_string()
    } else {
        let p: Vec<&str> = tarih.split('-').collect();
        if p.len() == 3 {
            format!("{}.{} {saat}", p[2], p[1])
        } else {
            format!("{tarih} {saat}")
        }
    }
}

fn kaynak_adi(k: &str) -> &str {
    match k {
        "tarama" => "Ürün tarama",
        "ucus" => "Uçuş",
        "tarayici" => "Tarayıcı",
        "zamanlayici" => "Zamanlayıcı",
        "alarm" => "Alarm",
        "python" => "Sistem",
        "cekirdek" => "Çekirdek",
        x => x,
    }
}

impl KayitEkrani {
    fn yukle(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context) {
        let imza = suzgec_imzasi(self.seviye, &self.kaynak, &self.site, &self.ara);
        let (bas_yukle, sonra_id) = {
            let o = self.ortak.lock().unwrap();
            if o.yukleniyor {
                return;
            }
            let bas = o.imza != imza;
            (bas, if bas { 0 } else { o.kayitlar.first().map(|k| k.id).unwrap_or(0) })
        };
        let simdi = Instant::now();
        if !bas_yukle {
            let zamani = self.son_istek.is_none_or(|t| simdi.duration_since(t) >= CANLI_ARALIK);
            if !(self.canli && zamani) {
                return;
            }
        }
        self.son_istek = Some(simdi);
        let ozet_zamani = self.son_ozet.is_none_or(|t| simdi.duration_since(t) >= OZET_ARALIK);
        if ozet_zamani {
            self.son_ozet = Some(simdi);
        }
        let mut sorgu: Vec<(String, String)> = vec![
            ("limit".into(), if bas_yukle { "800".into() } else { "500".into() }),
            ("seviye".into(), self.seviye.sorgu().into()),
            ("kaynak".into(), self.kaynak.clone()),
            ("site".into(), self.site.clone()),
            ("ara".into(), self.ara.trim().to_string()),
        ];
        if sonra_id > 0 {
            sorgu.push(("sonra_id".into(), sonra_id.to_string()));
        }
        let kaynak = kaynak.clone();
        let ortak = Arc::clone(&self.ortak);
        ortak.lock().unwrap().yukleniyor = true;
        let ctx = ctx.clone();
        thread::spawn(move || {
            let yanit = kaynak.kayitlar(&sorgu);
            let ozet = if ozet_zamani { kaynak.kayit_ozeti().ok() } else { None };
            let mut o = ortak.lock().unwrap();
            o.yukleniyor = false;
            match yanit {
                Ok(y) => {
                    o.kayitlar = if bas_yukle {
                        y.kayitlar
                    } else {
                        birlestir(y.kayitlar, &o.kayitlar, AZAMI_KAYIT)
                    };
                    o.imza = imza;
                    o.hata = None;
                }
                Err(e) => o.hata = Some(e),
            }
            if let Some(oz) = ozet {
                o.ozet = oz;
            }
            ctx.request_repaint();
        });
    }

    pub fn ciz(&mut self, ctx: &egui::Context, ui: &mut egui::Ui, kaynak: &VeriKaynagi) {
        self.yukle(kaynak, ctx);
        if self.canli {
            ctx.request_repaint_after(CANLI_ARALIK);
        }
        let p = tema::palet(ui.visuals());
        let (kayitlar, ozet, hata, yukleniyor) = {
            let o = self.ortak.lock().unwrap();
            (o.kayitlar.clone(), o.ozet.clone(), o.hata.clone(), o.yukleniyor)
        };

        ui.horizontal_wrapped(|ui| {
            ui.heading("📜 Kayıtlar");
            ui.add_space(8.0);
            let say = |s: &str| ozet.seviye.get(s).copied().unwrap_or(0);
            ui.label(RichText::new(format!("son 24 sa: {} hata", say("hata"))).color(p.hata));
            ui.label(RichText::new(format!("{} uyarı", say("uyari"))).color(p.uyari));
            ui.label(RichText::new(format!("{} bilgi", say("bilgi"))).weak());
            if yukleniyor {
                ui.spinner();
            }
        });
        ui.label(
            RichText::new(
                "Her tarayıcı gezinmesi, site/uçuş işi sonucu, HTTP hatası, bot koruması, Brave aç/kapa, zamanlayıcı \
                 adımı ve Telegram mesajı burada. 14 gün saklanır. Satıra tıkla → ayrıntı.",
            )
            .weak()
            .size(12.0),
        );
        ui.add_space(4.0);

        ui.horizontal_wrapped(|ui| {
            egui::ComboBox::from_id_salt("kayit_seviye")
                .selected_text(self.seviye.ad())
                .show_ui(ui, |ui| {
                    for s in [SeviyeSuzgeci::Tumu, SeviyeSuzgeci::UyariHata, SeviyeSuzgeci::Hata] {
                        ui.selectable_value(&mut self.seviye, s, s.ad());
                    }
                });
            egui::ComboBox::from_id_salt("kayit_kaynak")
                .selected_text(if self.kaynak.is_empty() { "Tüm kaynaklar".to_string() } else { kaynak_adi(&self.kaynak).to_string() })
                .show_ui(ui, |ui| {
                    ui.selectable_value(&mut self.kaynak, String::new(), "Tüm kaynaklar");
                    for k in &ozet.kaynaklar {
                        ui.selectable_value(&mut self.kaynak, k.clone(), kaynak_adi(k));
                    }
                });
            egui::ComboBox::from_id_salt("kayit_site")
                .selected_text(if self.site.is_empty() { "Tüm siteler".to_string() } else { self.site.clone() })
                .height(400.0)
                .show_ui(ui, |ui| {
                    ui.selectable_value(&mut self.site, String::new(), "Tüm siteler");
                    for s in &ozet.siteler {
                        ui.selectable_value(&mut self.site, s.clone(), s);
                    }
                });
            ui.add(egui::TextEdit::singleline(&mut self.ara).hint_text("ara: mesaj, adres, tarayıcı…").desired_width(220.0));
            let etiket = if self.canli { "⏸ Canlı" } else { "▶ Canlı" };
            ui.toggle_value(&mut self.canli, etiket)
                .on_hover_text("Açıkken yeni kayıtlar 2 sn'de bir gelir");
            if ui.button("↻ Yenile").clicked() {
                self.ortak.lock().unwrap().imza.clear();
                self.son_ozet = None;
            }
            if !self.kaynak.is_empty() || !self.site.is_empty() || !self.ara.is_empty() || self.seviye != SeviyeSuzgeci::Tumu {
                if ui.button("✕ Süzgeçleri temizle").clicked() {
                    self.seviye = SeviyeSuzgeci::Tumu;
                    self.kaynak.clear();
                    self.site.clear();
                    self.ara.clear();
                }
            }
        });
        if let Some(h) = &hata {
            ui.colored_label(p.hata, format!("Kayıtlar alınamadı: {h}"));
        }
        ui.add_space(4.0);

        let bugun = chrono::Local::now().format("%Y-%m-%d").to_string();
        let secili_kayit = self.secili.and_then(|id| kayitlar.iter().find(|k| k.id == id).cloned());
        let ayrinti_yuk = if secili_kayit.is_some() { 170.0 } else { 0.0 };
        let tablo_yuk = (ui.available_height() - ayrinti_yuk).max(160.0);

        ui.allocate_ui(egui::vec2(ui.available_width(), tablo_yuk), |ui| {
            TableBuilder::new(ui)
                .id_salt("kayit_tablosu")
                .striped(true)
                .resizable(true)
                .sense(Sense::click())
                .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                .column(Column::exact(18.0))
                .column(Column::initial(118.0).at_least(70.0).clip(true))
                .column(Column::initial(92.0).at_least(60.0).clip(true))
                .column(Column::initial(96.0).at_least(60.0).clip(true))
                .column(Column::initial(150.0).at_least(80.0).clip(true))
                .column(Column::initial(54.0).at_least(40.0).clip(true))
                .column(Column::initial(56.0).at_least(40.0).clip(true))
                .column(Column::remainder().at_least(200.0).clip(true))
                .header(24.0, |mut h| {
                    for b in ["", "Zaman", "Kaynak", "Site", "Tarayıcı", "Sonuç", "Süre", "Mesaj"] {
                        h.col(|ui| {
                            ui.label(RichText::new(b).strong());
                        });
                    }
                })
                .body(|govde| {
                    govde.rows(22.0, kayitlar.len(), |mut satir| {
                        let k = &kayitlar[satir.index()];
                        satir.set_selected(self.secili == Some(k.id));
                        let renk = match k.seviye.as_str() {
                            "hata" => p.hata,
                            "uyari" => p.uyari,
                            _ => p.basari,
                        };
                        satir.col(|ui| {
                            ui.label(RichText::new("●").color(renk));
                        });
                        satir.col(|ui| {
                            ui.label(RichText::new(saat_metni(&k.zaman, &bugun)).monospace().size(12.0));
                        });
                        satir.col(|ui| {
                            let mut m = kaynak_adi(&k.kaynak).to_string();
                            if let Some(kat) = &k.kategori {
                                m = format!("{m} · {kat}");
                            }
                            ui.label(m);
                        });
                        satir.col(|ui| {
                            ui.label(k.site.clone().unwrap_or_default());
                        });
                        satir.col(|ui| {
                            ui.label(RichText::new(k.tarayici.clone().unwrap_or_default()).weak());
                        });
                        satir.col(|ui| {
                            if let Some(a) = k.adet {
                                ui.label(a.to_string());
                            }
                        });
                        satir.col(|ui| {
                            if let Some(s) = k.sure_sn {
                                ui.label(format!("{s:.1} sn").replace('.', ","));
                            }
                        });
                        satir.col(|ui| {
                            let metin = RichText::new(&k.mesaj);
                            let metin = if k.seviye == "hata" { metin.color(p.hata) } else { metin };
                            let r = ui.label(metin);
                            if let Some(u) = &k.url {
                                r.on_hover_text(u);
                            }
                        });
                        if satir.response().clicked() {
                            self.secili = if self.secili == Some(k.id) { None } else { Some(k.id) };
                        }
                    });
                });
        });

        if let Some(k) = secili_kayit {
            ui.separator();
            egui::ScrollArea::vertical().id_salt("kayit_ayrinti").max_height(160.0).show(ui, |ui| {
                ui.horizontal_wrapped(|ui| {
                    ui.label(RichText::new(format!("#{} · {} · {}", k.id, k.zaman, k.seviye)).strong());
                    if let Some(o) = &k.olay {
                        ui.label(RichText::new(o).weak());
                    }
                    if ui.small_button("📋 Kopyala").clicked() {
                        let metin = format!(
                            "{} [{}] {} {} {} — {}\n{}\n{}",
                            k.zaman,
                            k.seviye,
                            k.kaynak,
                            k.site.clone().unwrap_or_default(),
                            k.tarayici.clone().unwrap_or_default(),
                            k.mesaj,
                            k.url.clone().unwrap_or_default(),
                            k.veri
                        );
                        ui.ctx().copy_text(metin);
                    }
                });
                ui.label(&k.mesaj);
                if let Some(u) = &k.url {
                    ui.hyperlink_to(u, u);
                }
                if !k.veri.is_null() && k.veri != serde_json::json!({}) {
                    let json = serde_json::to_string_pretty(&k.veri).unwrap_or_default();
                    ui.label(RichText::new(json).monospace().size(11.5).color(Color32::GRAY));
                }
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn k(id: i64) -> Kayit {
        Kayit { id, zaman: String::new(), seviye: "bilgi".into(), kaynak: "tarama".into(), mesaj: format!("m{id}"), ..Default::default() }
    }

    #[test]
    fn birlestir_yeniyi_one_ekler_tekrari_atar_keser() {
        let eski = vec![k(5), k(4), k(3)];
        let r = birlestir(vec![k(7), k(6), k(5)], &eski, 4);
        assert_eq!(r.iter().map(|x| x.id).collect::<Vec<_>>(), vec![7, 6, 5, 4]);
        assert_eq!(birlestir(vec![], &eski, 10).len(), 3);
    }

    #[test]
    fn saat_metni_bugun_ve_eski_gun() {
        assert_eq!(saat_metni("2026-11-15T09:42:27.123+03:00", "2026-11-15"), "09:42:27");
        assert_eq!(saat_metni("2026-11-14T23:05:01+03:00", "2026-11-15"), "14.11 23:05:01");
    }

    #[test]
    fn imza_suzgecle_degisir() {
        let a = suzgec_imzasi(SeviyeSuzgeci::Tumu, "", "", "");
        assert_ne!(a, suzgec_imzasi(SeviyeSuzgeci::Hata, "", "", ""));
        assert_ne!(a, suzgec_imzasi(SeviyeSuzgeci::Tumu, "ucus", "", ""));
        assert_eq!(a, suzgec_imzasi(SeviyeSuzgeci::Tumu, "", "", "  "));
    }
}
