//! Ayarlar → "Hızlı HTTP turu" kartı (15.11 kullanıcı isteği).
//!
//! Tarayıcı açmadan bot korumasına takılmayan siteler (akakce, vatan, pazarama…; uçuşta Google) kendi
//! sıklığıyla (varsayılan 10 dk) taranır. Tarayıcı isteyen siteler soldaki "Tarama sıklığı" kartında kalır.
//! Kart kendi arka plan kanalını taşır; main.rs yalnız `tazele_gerekirse` + `goster` çağırır.

use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::{Duration, Instant};

use eframe::egui::{self, RichText};

use crate::api::{HttpTuru, VeriKaynagi};
use crate::bicim;
use crate::tarama_alarm as ta;
use crate::tema;

/// Bekleme durumunda okuma aralığı; tur sürerken / vadesi gelmişken daha sık.
const OKUMA_SN: u64 = 30;
const OKUMA_CALISIRKEN_SN: u64 = 5;

enum Mesaj {
    Okuma(Result<Option<HttpTuru>, String>),
    Islem(Result<(Option<HttpTuru>, String), String>),
}

pub struct HttpTuruKarti {
    veri: Option<HttpTuru>,
    /// GET 404 → çekirdek bu özelliği bilmiyor
    yok: bool,
    hata: Option<String>,
    /// son başarılı okumanın anı (geri sayımı yerel saatle eritmek için)
    ani: Instant,
    son_istek: Option<Instant>,
    bekliyor: bool,
    islem_bekliyor: bool,
    tx: Sender<Mesaj>,
    rx: Receiver<Mesaj>,
}

impl Default for HttpTuruKarti {
    fn default() -> Self {
        let (tx, rx) = mpsc::channel();
        Self {
            veri: None,
            yok: false,
            hata: None,
            ani: Instant::now(),
            son_istek: None,
            bekliyor: false,
            islem_bekliyor: false,
            tx,
            rx,
        }
    }
}

/// Ekranda gösterilecek kalan süre: sunucunun söylediği − okumadan beri geçen.
pub fn kalan_sn(v: &HttpTuru, gecen: Duration) -> u64 {
    v.kalan_sn.saturating_sub(gecen.as_secs())
}

/// Son turun tek satırlık özeti.
pub fn sonuc_metni(v: &HttpTuru) -> String {
    match &v.son_sonuc {
        None => "henüz tur yok".to_string(),
        Some(r) => {
            let mut s = format!("{} teklif", r.teklif);
            if r.ucus > 0 {
                s.push_str(&format!(", {} uçuş", r.ucus));
            }
            s.push_str(&format!(" · {} sn", r.sure_sn));
            if r.hata > 0 {
                s.push_str(&format!(" · {} hata", r.hata));
            }
            s
        }
    }
}

impl HttpTuruKarti {
    /// Sekme başlığındaki kısa durum: "10 dk · 4:12" / "taranıyor…" / "kapalı".
    pub fn ozet_etiketi(&self) -> Option<String> {
        let v = self.veri.as_ref()?;
        Some(match v.durum.as_str() {
            "calisiyor" => "taranıyor…".to_string(),
            "kapali" => "kapalı".to_string(),
            _ => format!("{} dk · {}", v.aralik_dk, ta::geri_sayim(kalan_sn(v, self.ani.elapsed()))),
        })
    }

    /// Brave kategori taramaları otomatik mi? (bilinmiyorsa None)
    pub fn tarayici_otomatik(&self) -> Option<bool> {
        self.veri.as_ref().map(|v| v.tarayici_otomatik)
    }

    fn okuma_aralik(&self) -> u64 {
        match &self.veri {
            Some(v) if v.durum == "calisiyor" || (v.etkin && kalan_sn(v, self.ani.elapsed()) == 0) => OKUMA_CALISIRKEN_SN,
            _ => OKUMA_SN,
        }
    }

    /// Her karede çağrılır: gelen yanıtları işler, vadesi gelince arka planda okur.
    /// Dönüş: yeni biten tur mesajı (toast için) — `Some` ise ekran verisi tazelenmeli.
    pub fn tazele_gerekirse(&mut self, ctx: &egui::Context, kaynak: &VeriKaynagi) -> Option<String> {
        let mut biten = None;
        while let Ok(m) = self.rx.try_recv() {
            match m {
                Mesaj::Okuma(s) => {
                    self.bekliyor = false;
                    match s {
                        Ok(Some(yeni)) => {
                            if let Some(eski) = &self.veri {
                                if eski.durum == "calisiyor" && yeni.durum != "calisiyor" {
                                    biten = Some(format!("✓ Hızlı HTTP turu bitti: {}", sonuc_metni(&yeni)));
                                }
                            }
                            self.veri = Some(yeni);
                            self.yok = false;
                            self.hata = None;
                            self.ani = Instant::now();
                        }
                        Ok(None) => {
                            self.yok = true;
                            self.veri = None;
                        }
                        Err(e) => self.hata = Some(e),
                    }
                }
                Mesaj::Islem(s) => {
                    self.islem_bekliyor = false;
                    match s {
                        Ok((yeni, _bilgi)) => {
                            if let Some(y) = yeni {
                                self.veri = Some(y);
                                self.ani = Instant::now();
                            }
                            self.hata = None;
                            self.son_istek = None; // hemen yeniden oku
                        }
                        Err(e) => self.hata = Some(e),
                    }
                }
            }
        }
        let vade = self.son_istek.is_none_or(|t| t.elapsed() >= Duration::from_secs(self.okuma_aralik()));
        if vade && !self.bekliyor {
            self.bekliyor = true;
            self.son_istek = Some(Instant::now());
            let k = kaynak.clone();
            let tx = self.tx.clone();
            let c = ctx.clone();
            thread::spawn(move || {
                let _ = tx.send(Mesaj::Okuma(k.http_turu_getir()));
                c.request_repaint();
            });
        }
        biten
    }

    fn islem<F>(&mut self, ctx: &egui::Context, kaynak: &VeriKaynagi, f: F)
    where
        F: FnOnce(&VeriKaynagi) -> Result<(Option<HttpTuru>, String), String> + Send + 'static,
    {
        if self.islem_bekliyor {
            return;
        }
        self.islem_bekliyor = true;
        let k = kaynak.clone();
        let tx = self.tx.clone();
        let c = ctx.clone();
        thread::spawn(move || {
            let _ = tx.send(Mesaj::Islem(f(&k)));
            c.request_repaint();
        });
    }

    fn guncelle(&mut self, ctx: &egui::Context, kaynak: &VeriKaynagi, govde: serde_json::Value) {
        self.islem(ctx, kaynak, move |k| k.http_turu_guncelle(govde).map(|v| (Some(v), String::new())));
    }

    /// Tarayıcı turu sekmesinin üst kartı: aç/kapa + tarayıcı turundaki siteler (HTTP'ye taşıma düğmeleri).
    pub fn tarayici_turu_karti(&mut self, ctx: &egui::Context, ui: &mut egui::Ui, kaynak: &VeriKaynagi) {
        let p = tema::palet(ui.visuals());
        let bekliyor = self.islem_bekliyor;
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.label(RichText::new("Tarayıcı turu").strong().size(16.0));
                if bekliyor {
                    ui.spinner();
                }
            });
            ui.label(
                RichText::new(
                    "HTTP ile çekilemeyen siteler tarayıcıyla (şu an kazı Brave'i), aşağıdaki kategori sıklıklarıyla taranır. \
Her turda bu siteler + son Hızlı HTTP turunda başarısız olan ürün×site işleri taranır.",
                )
                .weak()
                .size(12.5),
            );
            ui.add_space(6.0);
            if let Some(h) = &self.hata {
                ui.label(RichText::new(format!("⚠ {h}")).color(p.hata).small());
            }
            let Some(v) = self.veri.clone() else {
                ui.label(RichText::new("Tur bilgisi okunuyor…").color(p.uyari));
                return;
            };
            let mut oto = v.tarayici_otomatik;
            let mut degisti = false;
            ui.add_enabled_ui(!bekliyor, |ui| {
                ui.horizontal(|ui| {
                    ui.label(RichText::new("Tarayıcı turu").strong());
                    let etiket = if oto { "açık" } else { "kapalı" };
                    degisti = ui
                        .checkbox(&mut oto, etiket)
                        .on_hover_text(
                            "Kapalıyken kategoriler süresi dolunca kendiliğinden taranmaz; yalnız 'Şimdi tara' ile taranır. \
Hızlı HTTP turu bundan etkilenmez.",
                        )
                        .changed();
                });
            });
            if degisti {
                self.guncelle(ctx, kaynak, serde_json::json!({ "tarayici_otomatik": oto }));
            }
            if !oto {
                ui.label(
                    RichText::new("⏸ Tarayıcı turu kapalı: kategoriler yalnız 'Şimdi tara' ile taranır.").color(p.uyari),
                );
            }
            ui.add_space(6.0);
            self.site_tablosu(ctx, ui, kaynak, &v, "tarayici");
        });
    }

    /// Bir turdaki siteler: ad, tür, (HTTP'de) hata sayacı, neden, diğer tura taşıma düğmesi.
    fn site_tablosu(&mut self, ctx: &egui::Context, ui: &mut egui::Ui, kaynak: &VeriKaynagi, v: &HttpTuru, tur: &str) {
        let p = tema::palet(ui.visuals());
        let bekliyor = self.islem_bekliyor;
        let liste: Vec<_> = v.atamalar.iter().filter(|a| a.tur == tur).cloned().collect();
        ui.label(RichText::new(format!("Bu turdaki siteler ({})", liste.len())).strong());
        if liste.is_empty() {
            ui.label(RichText::new("—").weak());
            return;
        }
        let (hedef, dugme, ipucu) = if tur == "http" {
            ("tarayici", "→ Tarayıcı turuna", "Bu site artık tarayıcıyla taranır, HTTP turunda taranmaz.")
        } else {
            ("http", "→ HTTP turuna", "Bu site artık 10 dk'lık Hızlı HTTP turunda taranır, Tarayıcı turunda taranmaz.")
        };
        let mut tasi: Option<String> = None;
        egui::Grid::new(("site_tur_grid", tur)).num_columns(4).striped(true).spacing([14.0, 4.0]).show(ui, |ui| {
            for baslik in ["Site", if tur == "http" { "HTTP hata" } else { "Tür" }, "Neden", ""] {
                ui.label(RichText::new(baslik).weak().strong().size(12.5));
            }
            ui.end_row();
            for a in &liste {
                let tip = if a.tip == "ucus" { "✈ " } else { "" };
                let mut ad = RichText::new(format!("{tip}{}", a.site));
                if !a.etkin {
                    ad = ad.weak().strikethrough();
                }
                ui.label(ad).on_hover_text(if a.etkin { "etkin" } else { "kapalı site (Aranan siteler'de işaretsiz)" });
                if tur == "http" {
                    let renk = if a.http_hata * 2 > v.hata_esigi.max(1) { p.uyari } else { ui.visuals().weak_text_color() };
                    ui.label(RichText::new(format!("{} / {}", a.http_hata, v.hata_esigi)).color(renk).monospace());
                } else {
                    ui.label(RichText::new(if a.tip == "ucus" { "uçuş" } else { "ürün" }).weak());
                }
                let neden = a.neden.clone().unwrap_or_default();
                ui.add_sized([230.0, 16.0], egui::Label::new(RichText::new(&neden).weak().small()).truncate())
                    .on_hover_text(match &a.zaman {
                        Some(z) => format!("{neden}\n{}", bicim::iso_tarih(z)),
                        None => neden.clone(),
                    });
                let mut uyari = String::new();
                if hedef == "http" && !a.http_arama {
                    uyari = "\n⚠ Bu sitenin araması tarayıcıdaki kutudan yapılıyor; HTTP turunda büyük olasılıkla \
hata verir ve hata eşiğini aşınca kendiliğinden geri döner."
                        .to_string();
                }
                let etiket = if hedef == "http" && !a.http_arama { format!("{dugme} ⚠") } else { dugme.to_string() };
                if ui
                    .add_enabled(!bekliyor, egui::Button::new(etiket).small())
                    .on_hover_text(format!("{ipucu}{uyari}"))
                    .clicked()
                {
                    tasi = Some(a.site.clone());
                }
                ui.end_row();
            }
        });
        if let Some(site) = tasi {
            let hedef = hedef.to_string();
            self.islem(ctx, kaynak, move |k| k.site_tur_degistir(&site, &hedef).map(|v| (Some(v), String::new())));
        }
    }

    pub fn goster(&mut self, ctx: &egui::Context, ui: &mut egui::Ui, kaynak: &VeriKaynagi) {
        let p = tema::palet(ui.visuals());
        let bekliyor = self.islem_bekliyor;
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.label(RichText::new("Hızlı HTTP turu (tarayıcısız siteler)").strong().size(16.0));
                if bekliyor {
                    ui.spinner();
                }
            });
            ui.label(
                RichText::new(
                    "Tarayıcı açmadan bot korumasına takılmayan siteler kendi sıklığıyla taranır; fiyatlar ve alarmlar \
çoğu zaman 10 dk içinde günceldir. Bu sitelere Tarayıcı turunda gidilmez. Burada başarısız olan ürün×site \
işleri bir sonraki Tarayıcı turunda taranır; bir sitenin hata sayısı eşiği aşarsa site kendiliğinden Tarayıcı turuna geçer.",
                )
                .weak()
                .size(12.5),
            );
            ui.add_space(6.0);
            if let Some(h) = &self.hata {
                ui.label(RichText::new(format!("⚠ {h}")).color(p.hata).small());
            }
            let Some(v) = self.veri.clone() else {
                ui.label(
                    RichText::new(if self.yok {
                        "Çekirdek bu özelliği henüz bilmiyor (/tarama/http yok) — çekirdeği yeniden başlatın."
                    } else {
                        "Hızlı tur okunuyor…"
                    })
                    .color(p.uyari),
                );
                return;
            };

            let mut govde: Option<serde_json::Value> = None;
            ui.add_enabled_ui(!bekliyor, |ui| {
                egui::Grid::new("http_turu_grid").num_columns(2).spacing([16.0, 8.0]).show(ui, |ui| {
                    ui.label(RichText::new("Hızlı tur").strong());
                    let mut etkin = v.etkin;
                    let etiket = if etkin { "açık" } else { "kapalı" };
                    if ui.checkbox(&mut etkin, etiket).changed() {
                        govde = Some(serde_json::json!({ "etkin": etkin }));
                    }
                    ui.end_row();

                    ui.label(RichText::new("Sıklık").strong());
                    let secenekler =
                        if v.secenekler_dk.is_empty() { vec![5, 10, 15, 20, 30, 45, 60] } else { v.secenekler_dk.clone() };
                    let mut secili = v.aralik_dk;
                    egui::ComboBox::from_id_salt("http_turu_aralik")
                        .width(86.0)
                        .selected_text(format!("{secili} dk"))
                        .show_ui(ui, |ui| {
                            for dk in &secenekler {
                                ui.selectable_value(&mut secili, *dk, format!("{dk} dk"));
                            }
                        });
                    if secili != v.aralik_dk {
                        govde = Some(serde_json::json!({ "aralik_dk": secili }));
                    }
                    ui.end_row();

                });
            });
            if let Some(g) = govde {
                self.guncelle(ctx, kaynak, g);
            }

            ui.add_space(6.0);
            ui.separator();
            let mut simdi = false;
            let mut sifirla = false;
            ui.horizontal_wrapped(|ui| {
                match v.durum.as_str() {
                    "calisiyor" => {
                        ui.spinner();
                        ui.label(RichText::new("taranıyor…").color(p.vurgu));
                    }
                    "kapali" => {
                        ui.label(RichText::new("kapalı").color(p.uyari));
                    }
                    _ => {
                        let kalan = kalan_sn(&v, self.ani.elapsed());
                        ui.label(RichText::new("Sonraki:").weak());
                        ui.label(RichText::new(if v.sonraki.is_none() { "hemen".to_string() } else { ta::geri_sayim(kalan) }).monospace());
                        if let Some(sn) = &v.sonraki {
                            ui.label(RichText::new(bicim::iso_tarih(sn)).weak().small());
                        }
                        if ui
                            .add_enabled(!bekliyor, egui::Button::new("↺ Sıfırla").small())
                            .on_hover_text(format!("Geri sayımı şimdiden başlat: {} dk sonra taranır.", v.aralik_dk))
                            .clicked()
                        {
                            sifirla = true;
                        }
                    }
                }
                if ui
                    .add_enabled(!bekliyor && v.durum != "calisiyor", egui::Button::new("Şimdi tara"))
                    .on_hover_text("Hızlı turu hemen başlat (zamanlayıcı 15 sn içinde alır; tarayıcı açılmaz).")
                    .clicked()
                {
                    simdi = true;
                }
            });
            ui.horizontal_wrapped(|ui| {
                ui.label(RichText::new("Son tur:").weak());
                ui.label(v.son_bitis.as_deref().map(bicim::iso_tarih).unwrap_or_else(|| "—".to_string()));
                ui.label(RichText::new(sonuc_metni(&v)).weak().small());
            });
            if let Some(r) = &v.son_sonuc {
                if !r.engelli.is_empty() {
                    ui.label(
                        RichText::new(format!("Engellenen (işleri Tarayıcı turuna bırakıldı): {}", r.engelli.join(", ")))
                            .color(p.uyari)
                            .small(),
                    );
                }
                if !r.tasinan.is_empty() {
                    ui.label(
                        RichText::new(format!("⇄ Hata eşiğini aşıp Tarayıcı turuna geçen: {}", r.tasinan.join(", ")))
                            .color(p.uyari)
                            .small(),
                    );
                }
            }
            ui.add_space(8.0);
            self.site_tablosu(ctx, ui, kaynak, &v, "http");
            if simdi {
                self.islem(ctx, kaynak, |k| k.http_turu_simdi().map(|b| (None, b)));
            }
            if sifirla {
                self.islem(ctx, kaynak, |k| k.http_turu_sifirla().map(|v| (Some(v), String::new())));
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::{http_turu_yaniti, HttpTurSonuc};

    fn ornek() -> HttpTuru {
        HttpTuru {
            etkin: true,
            aralik_dk: 10,
            ucus: true,
            tarayicida_atla: true,
            tarayici_otomatik: true,
            hata_esigi: 100,
            secenekler_dk: vec![5, 10, 15],
            urun_siteleri: vec!["akakce".into(), "vatan".into()],
            ucus_kaynaklari: vec!["google".into()],
            durum: "bekliyor".into(),
            kalan_sn: 120,
            ..Default::default()
        }
    }

    #[test]
    fn sozlesme_govdesi_ayristirilir() {
        let g = r#"{"etkin": true, "aralik_dk": 10, "ucus": true, "tarayicida_atla": true,
            "secenekler_dk": [5,10,15,20,30,45,60], "urun_siteleri": ["akakce","vatan"], "ucus_kaynaklari": ["google"],
            "durum": "bekliyor", "son_baslangic": "2026-11-15T17:13:57+03:00", "son_bitis": "2026-11-15T17:16:06+03:00",
            "son_sonuc": {"teklif": 294, "ucus": 127, "hata": 20, "sure_sn": 129, "siteler": ["akakce"], "engelli": ["itopya"]},
            "sonraki": "2026-11-15T17:26:06+03:00", "kalan_sn": 410}"#;
        let v = http_turu_yaniti(200, g).unwrap().unwrap();
        assert_eq!(v.aralik_dk, 10);
        assert_eq!(v.son_sonuc.as_ref().unwrap().engelli, vec!["itopya".to_string()]);
        assert_eq!(http_turu_yaniti(404, "").unwrap(), None);
        assert!(http_turu_yaniti(500, r#"{"hata":"x"}"#).is_err());
    }

    #[test]
    fn geri_sayim_yerel_saatle_erir() {
        let v = ornek();
        assert_eq!(kalan_sn(&v, Duration::from_secs(20)), 100);
        assert_eq!(kalan_sn(&v, Duration::from_secs(500)), 0);
    }

    #[test]
    fn ozet_metinleri() {
        let mut v = ornek();
        assert_eq!(sonuc_metni(&v), "henüz tur yok");
        v.son_sonuc = Some(HttpTurSonuc { teklif: 294, ucus: 127, hata: 2, sure_sn: 129, ..Default::default() });
        assert_eq!(sonuc_metni(&v), "294 teklif, 127 uçuş · 129 sn · 2 hata");
    }
}
