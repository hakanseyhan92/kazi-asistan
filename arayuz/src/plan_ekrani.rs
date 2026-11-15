use std::collections::BTreeMap;
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::{Duration, Instant};

use chrono::{Datelike, Duration as ChronoDuration, NaiveDate};
use eframe::egui::{self, Color32, Key, Modifiers, RichText, Stroke};
use egui_extras::DatePickerButton;
use crate::tema;

use crate::api::{
    Durak, Hata, Izin, Onizleme, OnizlemeBacak, OnizlemeYanTur, Plan, PlanGetirYanit,
    PlanKaydetYanit, VeriKaynagi, YanTurAyar, Yer, YerNokta, Yolcu, YolcuDenetimi,
};
use crate::bicim;
use crate::plan_model::{
    arac_etiketi, bacaklari_yeniden_kur_eski_ile, donus_ekle, donus_var_mi, durak_etkin_tarihi,
    durak_kalis_gecesi, hata_alani_eslesir, izin_yolculukta_bitiyor_mu, nokta_turu_simgesi,
    plan_toplam_sure_metni, tarih_ayristir, turkce_katla, varsayilan_araclar, yolcu_cikar,
    yolcu_id_uret, yolcu_sil_etkisi,
};

const YER_ARAMA_GECIKME: Duration = Duration::from_millis(250);
const ONIZLEME_GECIKME: Duration = Duration::from_millis(400);

#[derive(Debug, Default, Clone, Copy)]
pub struct PlanEkraniEylemi {
    pub ucuslara_git: bool,
    pub seyahati_yenile: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum UlkeSeciciHedef {
    Uyruk(usize),
    Izin(usize, usize),
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum YerAramaHedefi {
    Durak(usize),
    YanTurHedef(usize),
}

enum PlanMesaji {
    PlanYuklendi(Result<PlanGetirYanit, String>),
    UlkelerYuklendi(Result<BTreeMap<String, String>, String>),
    OnizlemeSonucu {
        istek_no: u64,
        sonuc: Result<Onizleme, String>,
    },
    YerAramaSonucu {
        istek_no: u64,
        sonuc: Result<Vec<Yer>, String>,
    },
    KaydetSonucu(Result<PlanKaydetYanit, String>),
}

pub struct PlanEkraniDurumu {
    taslak: Plan,
    son_kaydedilen: Plan,
    son_onizlenen: Option<Plan>,
    onizleme: Onizleme,
    kayitli: bool,

    yuklendi: bool,
    yukleniyor: bool,
    onizleniyor: bool,
    kaydediliyor: bool,
    bagli: bool,
    baglanti_hatasi: Option<String>,

    son_duzenleme_ani: Option<Instant>,
    onizleme_istek_no: u64,
    son_onizleme_istek_no: u64,

    ulkeler: BTreeMap<String, String>,
    sehir_noktalari: BTreeMap<String, Vec<YerNokta>>,

    kayit_bildirimi: Option<String>,
    kayit_hatasi: Option<String>,

    // Yolcu ekleme / silme onayı / ülke seçici
    yeni_yolcu_acik: bool,
    yeni_yolcu_ad: String,
    silinecek_yolcu_onay: Option<(usize, String, usize, usize)>,
    acik_ulke_secici: Option<UlkeSeciciHedef>,
    ulke_arama: String,

    // Şehir otomatik tamamlama
    yer_hedefi: Option<YerAramaHedefi>,
    yer_sorgu: String,
    yer_son_yazma_ani: Option<Instant>,
    yer_aranan_sorgu: String,
    yer_sonuclari: Vec<Yer>,
    yer_secili_indeks: usize,
    yer_araniyor: bool,
    yer_istek_no: u64,
    son_yer_istek_no: u64,

    // Sürükle-bırak durak sıralama
    suruklenen_durak: Option<usize>,

    // Dönüş ekle penceresi
    donus_pencere_acik: bool,
    donus_tarihi: NaiveDate,
    donus_esneklik: u8,

    // Hata veya bacak odaklı kaydırma
    odaklanacak_alan: Option<String>,

    tx: Sender<PlanMesaji>,
    rx: Receiver<PlanMesaji>,
}

impl Default for PlanEkraniDurumu {
    fn default() -> Self {
        let (tx, rx) = mpsc::channel();
        Self {
            taslak: Plan::default(),
            son_kaydedilen: Plan::default(),
            son_onizlenen: None,
            onizleme: Onizleme::default(),
            kayitli: true,
            yuklendi: false,
            yukleniyor: false,
            onizleniyor: false,
            kaydediliyor: false,
            bagli: true,
            baglanti_hatasi: None,
            son_duzenleme_ani: None,
            onizleme_istek_no: 0,
            son_onizleme_istek_no: 0,
            ulkeler: BTreeMap::new(),
            sehir_noktalari: BTreeMap::new(),
            kayit_bildirimi: None,
            kayit_hatasi: None,
            yeni_yolcu_acik: false,
            yeni_yolcu_ad: String::new(),
            silinecek_yolcu_onay: None,
            acik_ulke_secici: None,
            ulke_arama: String::new(),
            yer_hedefi: None,
            yer_sorgu: String::new(),
            yer_son_yazma_ani: None,
            yer_aranan_sorgu: String::new(),
            yer_sonuclari: Vec::new(),
            yer_secili_indeks: 0,
            yer_araniyor: false,
            yer_istek_no: 0,
            son_yer_istek_no: 0,
            suruklenen_durak: None,
            donus_pencere_acik: false,
            donus_tarihi: NaiveDate::from_ymd_opt(2027, 1, 6).unwrap_or_default(),
            donus_esneklik: 2,
            odaklanacak_alan: None,
            tx,
            rx,
        }
    }
}

impl PlanEkraniDurumu {
    pub fn ilk_yuklemeyi_baslat(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context) {
        if self.yuklendi || self.yukleniyor {
            return;
        }
        self.plan_yukle(kaynak, ctx);
        self.ulkeleri_yukle(kaynak, ctx);
    }

    pub fn plan_yukle(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context) {
        self.yukleniyor = true;
        let k = kaynak.clone();
        let tx = self.tx.clone();
        let ctx_c = ctx.clone();
        thread::spawn(move || {
            let sonuc = k.plan_getir();
            let _ = tx.send(PlanMesaji::PlanYuklendi(sonuc));
            ctx_c.request_repaint();
        });
    }

    fn ulkeleri_yukle(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context) {
        let k = kaynak.clone();
        let tx = self.tx.clone();
        let ctx_c = ctx.clone();
        thread::spawn(move || {
            let sonuc = k.ulkeler();
            let _ = tx.send(PlanMesaji::UlkelerYuklendi(sonuc));
            ctx_c.request_repaint();
        });
    }

    fn onizleme_gonder(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context) {
        self.onizleme_istek_no += 1;
        let istek_no = self.onizleme_istek_no;
        self.onizleniyor = true;
        let plan = self.taslak.clone();
        let k = kaynak.clone();
        let tx = self.tx.clone();
        let ctx_c = ctx.clone();
        thread::spawn(move || {
            let sonuc = k.plan_onizle(&plan);
            let _ = tx.send(PlanMesaji::OnizlemeSonucu { istek_no, sonuc });
            ctx_c.request_repaint();
        });
    }

    fn yer_ara_gonder(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context, sorgu: String) {
        self.yer_istek_no += 1;
        let istek_no = self.yer_istek_no;
        self.yer_araniyor = true;
        self.yer_aranan_sorgu = sorgu.clone();
        let k = kaynak.clone();
        let tx = self.tx.clone();
        let ctx_c = ctx.clone();
        thread::spawn(move || {
            let sonuc = k.yer_ara(&sorgu);
            let _ = tx.send(PlanMesaji::YerAramaSonucu { istek_no, sonuc });
            ctx_c.request_repaint();
        });
    }

    fn kaydet_gonder(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context) {
        if self.kaydediliyor || !self.bagli || !self.onizleme.hatalar.is_empty() {
            return;
        }
        self.kaydediliyor = true;
        self.kayit_hatasi = None;
        self.kayit_bildirimi = None;
        let plan = self.taslak.clone();
        let k = kaynak.clone();
        let tx = self.tx.clone();
        let ctx_c = ctx.clone();
        thread::spawn(move || {
            let sonuc = k.plan_kaydet(&plan);
            let _ = tx.send(PlanMesaji::KaydetSonucu(sonuc));
            ctx_c.request_repaint();
        });
    }

    fn noktalar_onbellege_al(&mut self, onizleme: &Onizleme) {
        for b in &onizleme.bacaklar {
            if !b.kalkis_noktalari.is_empty() {
                self.sehir_noktalari
                    .insert(b.kalkis_sehir.clone(), b.kalkis_noktalari.clone());
            }
            if !b.varis_noktalari.is_empty() {
                self.sehir_noktalari
                    .insert(b.varis_sehir.clone(), b.varis_noktalari.clone());
            }
        }
    }

    fn mesajlari_isle(&mut self, eylem: &mut PlanEkraniEylemi) {
        while let Ok(msg) = self.rx.try_recv() {
            match msg {
                PlanMesaji::PlanYuklendi(sonuc) => {
                    self.yukleniyor = false;
                    match sonuc {
                        Ok(yanit) => {
                            self.bagli = true;
                            self.baglanti_hatasi = None;
                            self.yuklendi = true;
                            self.kayitli = yanit.kayitli;
                            self.taslak = yanit.plan.clone();
                            self.son_kaydedilen = yanit.plan.clone();
                            self.son_onizlenen = Some(yanit.plan);
                            self.noktalar_onbellege_al(&yanit.onizleme);
                            self.onizleme = yanit.onizleme;
                            self.son_duzenleme_ani = None;
                        }
                        Err(e) => {
                            self.bagli = false;
                            self.baglanti_hatasi = Some(e);
                        }
                    }
                }
                PlanMesaji::UlkelerYuklendi(sonuc) => {
                    if let Ok(harita) = sonuc {
                        self.ulkeler = harita;
                    }
                }
                PlanMesaji::OnizlemeSonucu { istek_no, sonuc } => {
                    if istek_no < self.son_onizleme_istek_no {
                        continue;
                    }
                    self.son_onizleme_istek_no = istek_no;
                    if istek_no == self.onizleme_istek_no {
                        self.onizleniyor = false;
                    }
                    match sonuc {
                        Ok(oniz) => {
                            self.bagli = true;
                            self.baglanti_hatasi = None;
                            self.noktalar_onbellege_al(&oniz);
                            self.onizleme = oniz;
                        }
                        Err(e) => {
                            self.bagli = false;
                            self.baglanti_hatasi = Some(e);
                        }
                    }
                }
                PlanMesaji::YerAramaSonucu { istek_no, sonuc } => {
                    if istek_no < self.son_yer_istek_no {
                        continue;
                    }
                    self.son_yer_istek_no = istek_no;
                    if istek_no == self.yer_istek_no {
                        self.yer_araniyor = false;
                    }
                    if let Ok(liste) = sonuc {
                        for y in &liste {
                            if !y.noktalar.is_empty() {
                                self.sehir_noktalari
                                    .insert(y.sehir.clone(), y.noktalar.clone());
                            }
                        }
                        self.yer_sonuclari = liste;
                        self.yer_secili_indeks = 0;
                    }
                }
                PlanMesaji::KaydetSonucu(sonuc) => {
                    self.kaydediliyor = false;
                    match sonuc {
                        Ok(yanit) => {
                            self.bagli = true;
                            self.baglanti_hatasi = None;
                            self.kayit_hatasi = None;
                            self.kayitli = true;
                            self.taslak = yanit.plan.clone();
                            self.son_kaydedilen = yanit.plan.clone();
                            self.son_onizlenen = Some(yanit.plan);
                            self.noktalar_onbellege_al(&yanit.onizleme);
                            self.onizleme = yanit.onizleme;
                            self.son_duzenleme_ani = None;

                            let bildirim = match yanit.eski_tarayici {
                                Some(et) => {
                                    if let Some(hata) = et.hata {
                                        format!("Kaydedildi · eski tarayıcı uyarısı: {}", hata)
                                    } else if et.atlanan.is_empty() {
                                        format!(
                                            "Kaydedildi · eski tarayıcıya {} bacak yazıldı",
                                            et.yazilan.len()
                                        )
                                    } else {
                                        format!(
                                            "Kaydedildi · eski tarayıcıya {} bacak yazıldı · atlanan: {}",
                                            et.yazilan.len(),
                                            et.atlanan.join(", ")
                                        )
                                    }
                                }
                                None => "Kaydedildi".to_string(),
                            };
                            self.kayit_bildirimi = Some(bildirim);
                            eylem.seyahati_yenile = true;
                        }
                        Err(e) => {
                            self.kayit_hatasi = Some(e);
                        }
                    }
                }
            }
        }
    }

    fn gecikmeli_istekleri_yurut(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context) {
        // 1. Taslak değiştiyse 400 ms önizleme zamanlayıcısını başlat/sıfırla
        if self.yuklendi && self.son_onizlenen.as_ref() != Some(&self.taslak) {
            self.son_onizlenen = Some(self.taslak.clone());
            self.son_duzenleme_ani = Some(Instant::now());
            self.kayit_bildirimi = None;
        }

        if let Some(t) = self.son_duzenleme_ani {
            if t.elapsed() >= ONIZLEME_GECIKME {
                self.son_duzenleme_ani = None;
                self.onizleme_gonder(kaynak, ctx);
            } else {
                ctx.request_repaint_after(Duration::from_millis(80));
            }
        }

        // 2. Şehir araması için 250 ms gecikmeli istek
        if self.yer_hedefi.is_some() {
            if let Some(t) = self.yer_son_yazma_ani {
                if t.elapsed() >= YER_ARAMA_GECIKME {
                    self.yer_son_yazma_ani = None;
                    let q = self.yer_sorgu.trim().to_string();
                    if q.is_empty() {
                        self.yer_sonuclari.clear();
                    } else if q != self.yer_aranan_sorgu {
                        self.yer_ara_gonder(kaynak, ctx, q);
                    }
                } else {
                    ctx.request_repaint_after(Duration::from_millis(60));
                }
            }
        }
    }

    fn degisiklik_var_mi(&self) -> bool {
        self.yuklendi && self.taslak != self.son_kaydedilen
    }

    fn ulke_adi_getir(&self, kod: &str) -> String {
        let buyuk = kod.trim().to_ascii_uppercase();
        self.ulkeler
            .get(&buyuk)
            .cloned()
            .unwrap_or_else(|| buyuk)
    }

    pub fn ciz(
        &mut self,
        ctx: &egui::Context,
        ui: &mut egui::Ui,
        kaynak: &VeriKaynagi,
    ) -> PlanEkraniEylemi {
        let mut eylem = PlanEkraniEylemi::default();
        self.ilk_yuklemeyi_baslat(kaynak, ctx);
        self.mesajlari_isle(&mut eylem);
        self.gecikmeli_istekleri_yurut(kaynak, ctx);

        if self.ulkeler.is_empty() && !self.yukleniyor {
            // Sahte mod veya erken yükleme boşsa gerektiğinde tekrar denenebilir
        }

        // A. Üst şerit — plan özeti ve eylemler
        self.ust_serit_ciz(ctx, ui, kaynak, &mut eylem);
        ui.add_space(6.0);

        if self.yukleniyor && !self.yuklendi {
            ui.horizontal(|ui| {
                ui.spinner();
                ui.label("Seyahat planı yükleniyor…");
            });
            return eylem;
        }

        if !self.yuklendi && self.baglanti_hatasi.is_some() {
            ui.horizontal(|ui| {
                ui.label("Plan henüz yüklenemedi.");
                if ui.button("🔄 Tekrar dene").clicked() {
                    self.plan_yukle(kaynak, ctx);
                    self.ulkeleri_yukle(kaynak, ctx);
                }
            });
            return eylem;
        }

        // Üç sütun hissi veren tek ekran (geniş pencerede yan yana, dar pencerede alt alta)
        let genislik = ui.available_width();
        let yukseklik = ui.available_height().max(320.0);

        if genislik >= 960.0 {
            let sol_gen = 275.0_f32;
            let sag_gen = 290.0_f32;
            let ara = 10.0_f32;
            let orta_gen = (genislik - sol_gen - sag_gen - ara * 2.0).max(320.0);

            ui.horizontal_top(|ui| {
                ui.spacing_mut().item_spacing.x = ara;

                // B. Yolcular sütunu (sol, dar)
                ui.allocate_ui_with_layout(
                    egui::vec2(sol_gen, yukseklik),
                    egui::Layout::top_down(egui::Align::Min),
                    |ui| {
                        ui.set_max_width(sol_gen);
                        egui::ScrollArea::vertical()
                            .id_salt("plan_yolcular_scroll")
                            .auto_shrink([false, false])
                            .show(ui, |ui| {
                                ui.set_max_width(sol_gen - 10.0);
                                self.yolcular_bolumu_ciz(ui);
                            });
                    },
                );

                // C. Güzergâh zaman çizelgesi (orta, ana alan)
                ui.allocate_ui_with_layout(
                    egui::vec2(orta_gen, yukseklik),
                    egui::Layout::top_down(egui::Align::Min),
                    |ui| {
                        ui.set_max_width(orta_gen);
                        egui::ScrollArea::vertical()
                            .id_salt("plan_guzergah_scroll")
                            .auto_shrink([false, false])
                            .show(ui, |ui| {
                                ui.set_max_width(orta_gen - 10.0);
                                self.guzergah_bolumu_ciz(ctx, ui);
                            });
                    },
                );

                // D. Yan turlar + F. Notlar (sağ)
                ui.allocate_ui_with_layout(
                    egui::vec2(sag_gen, yukseklik),
                    egui::Layout::top_down(egui::Align::Min),
                    |ui| {
                        ui.set_max_width(sag_gen);
                        egui::ScrollArea::vertical()
                            .id_salt("plan_yantur_scroll")
                            .auto_shrink([false, false])
                            .show(ui, |ui| {
                                ui.set_max_width(sag_gen - 10.0);
                                self.yan_turlar_bolumu_ciz(ui);
                            });
                    },
                );
            });
        } else {
            // Dar pencere (<960px içerik alanı, ör. 900px pencere): alt alta tek dikey kaydırma
            egui::ScrollArea::vertical()
                .id_salt("plan_dar_tek_scroll")
                .auto_shrink([false, false])
                .show(ui, |ui| {
                    self.yolcular_bolumu_ciz(ui);
                    ui.add_space(10.0);
                    self.guzergah_bolumu_ciz(ctx, ui);
                    ui.add_space(10.0);
                    self.yan_turlar_bolumu_ciz(ui);
                    ui.add_space(8.0);
                });
        }

        // Açılır pencereler (ülke seçici, dönüş ekle, yolcu silme onayı)
        self.ulke_secici_penceresi_ciz(ctx);
        self.donus_penceresi_ciz(ctx);
        self.yolcu_sil_onay_penceresi_ciz(ctx);

        eylem
    }

    // ======================= A. Üst Şerit =======================

    fn ust_serit_ciz(
        &mut self,
        ctx: &egui::Context,
        ui: &mut egui::Ui,
        kaynak: &VeriKaynagi,
        eylem: &mut PlanEkraniEylemi,
    ) {
        let kart_rengi = kart_arka_rengi(ui);
        egui::Frame::NONE
            .fill(kart_rengi)
            .stroke(Stroke::new(1.0_f32, kenar_rengi(ui)))
            .inner_margin(10.0)
            .corner_radius(6.0)
            .show(ui, |ui| {
                // Satır 1: Başlık + Plan adı + Sağda özet (süre, yolcu, bacak)
                ui.horizontal_wrapped(|ui| {
                    ui.label(RichText::new("🗺 Seyahat Planla").size(16.0).strong());
                    ui.separator();

                    ui.label(RichText::new("Plan adı:").small().weak());
                    ui.add(
                        egui::TextEdit::singleline(&mut self.taslak.ad)
                            .desired_width(210.0)
                            .hint_text("Ör. Tokyo 2026-27"),
                    );

                    ui.separator();
                    let bacak_sayisi = self.taslak.bacaklar.len();
                    let yolcu_sayisi = self.taslak.yolcular.len();
                    let sure = plan_toplam_sure_metni(&self.taslak);
                    ui.label(
                        RichText::new(sure)
                            .small()
                            .color(Color32::from_rgb(120, 190, 255))
                            .strong(),
                    );
                    ui.label(RichText::new("·").weak());
                    ui.label(
                        RichText::new(format!("{} yolcu", yolcu_sayisi))
                            .small()
                            .strong(),
                    );
                    ui.label(RichText::new("·").weak());
                    ui.label(
                        RichText::new(format!("{} bacak", bacak_sayisi))
                            .small()
                            .strong(),
                    );
                });

                ui.add_space(4.0);

                // Satır 2: Eylemler + durum rozetleri (dar pencerede bile çakışmaz)
                ui.horizontal_wrapped(|ui| {
                    if ui
                        .add_enabled(self.degisiklik_var_mi(), egui::Button::new("↩ Geri al"))
                        .on_hover_text("Son kaydedilen hâle dön")
                        .clicked()
                    {
                        self.taslak = self.son_kaydedilen.clone();
                        self.son_onizlenen = None;
                        self.kayit_bildirimi = None;
                        self.kayit_hatasi = None;
                    }

                    let hata_sayisi = self.onizleme.hatalar.len();
                    let kaydedilebilir =
                        hata_sayisi == 0 && self.bagli && !self.kaydediliyor && self.yuklendi;
                    let kaydet_metni = if self.kaydediliyor {
                        "Kaydediliyor…".to_string()
                    } else {
                        "✓ Kaydet ve taramaya gönder".to_string()
                    };
                    let kaydet_btn = egui::Button::new(
                        RichText::new(kaydet_metni)
                            .color(if kaydedilebilir {
                                Color32::WHITE
                            } else {
                                ui.visuals().weak_text_color()
                            })
                            .strong(),
                    )
                    .fill(if kaydedilebilir {
                        Color32::from_rgb(34, 150, 96)
                    } else {
                        ui.visuals().widgets.inactive.bg_fill
                    });

                    let btn_resp = ui.add_enabled(kaydedilebilir, kaydet_btn);
                    if hata_sayisi > 0 {
                        btn_resp.on_disabled_hover_text(format!(
                            "{} hata var — kaydetmeden önce düzeltin",
                            hata_sayisi
                        ));
                    } else if !self.bagli {
                        btn_resp.on_disabled_hover_text("Çekirdeğe ulaşılamıyor");
                    } else if btn_resp.clicked() {
                        self.kaydet_gonder(kaynak, ctx);
                    }

                    if hata_sayisi > 0 {
                        let ilk_alan = self.onizleme.hatalar[0].alan.clone();
                        let ilk_mesaj = self.onizleme.hatalar[0].mesaj.clone();
                        if ui
                            .button(
                                RichText::new(format!("⛔ {} hata var", hata_sayisi))
                                    .color(Color32::from_rgb(235, 90, 90))
                                    .strong(),
                            )
                            .on_hover_text(format!("İlk hata: {} (tıklayınca git)", ilk_mesaj))
                            .clicked()
                        {
                            self.odaklanacak_alan = Some(ilk_alan);
                        }
                    }

                    if self.degisiklik_var_mi() {
                        ui.label(
                            RichText::new("● Kaydedilmemiş değişiklik")
                                .color(Color32::from_rgb(243, 156, 18))
                                .strong(),
                        );
                    }

                    if self.onizleniyor || self.son_duzenleme_ani.is_some() {
                        ui.spinner();
                        ui.label(RichText::new("Doğrulanıyor…").small().weak());
                    }
                });

                // Bilgi / Uyarı / Kayıt bildirim şeritleri
                if !self.kayitli && self.yuklendi {
                    ui.add_space(6.0);
                    uyari_seridi_ciz(
                        ui,
                        Color32::from_rgb(52, 152, 219),
                        "ℹ Bu plan eski sistemden içe aktarıldı; kaydedince kazi planı olur.",
                    );
                }

                if !self.bagli {
                    ui.add_space(6.0);
                    let ek = self
                        .baglanti_hatasi
                        .as_deref()
                        .map(|e| format!(" ({})", e))
                        .unwrap_or_default();
                    uyari_seridi_ciz(
                        ui,
                        Color32::from_rgb(231, 76, 60),
                        &format!(
                            "⚠ Çekirdeğe ulaşılamıyor — değişiklikler yerelde tutuluyor{}",
                            ek
                        ),
                    );
                }

                if let Some(hata) = &self.kayit_hatasi {
                    ui.add_space(6.0);
                    uyari_seridi_ciz(
                        ui,
                        Color32::from_rgb(231, 76, 60),
                        &format!("⛔ Kayıt hatası: {}", hata),
                    );
                }

                if let Some(bildirim) = &self.kayit_bildirimi {
                    ui.add_space(6.0);
                    egui::Frame::NONE
                        .fill(Color32::from_rgba_unmultiplied(46, 204, 113, 35))
                        .stroke(Stroke::new(1.0_f32, Color32::from_rgb(46, 204, 113)))
                        .inner_margin(6.0)
                        .corner_radius(4.0)
                        .show(ui, |ui| {
                            ui.horizontal_wrapped(|ui| {
                                ui.label(
                                    RichText::new(format!("✓ {}", bildirim))
                                        .color(Color32::from_rgb(70, 220, 130))
                                        .strong(),
                                );
                                if ui.small_button("Uçuşlar'a git →").clicked() {
                                    eylem.ucuslara_git = true;
                                }
                            });
                        });
                }
            });
    }

    // ======================= B. Yolcular Kartı (Sol) =======================

    fn yolcular_bolumu_ciz(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.label(RichText::new("👥 Yolcular").size(15.0).strong());
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.small_button("+ Yolcu ekle").clicked() {
                    self.yeni_yolcu_acik = !self.yeni_yolcu_acik;
                    self.yeni_yolcu_ad.clear();
                }
            });
        });

        hata_metni_ciz(ui, &self.onizleme.hatalar, "yolcular");

        if self.yeni_yolcu_acik {
            ui.add_space(4.0);
            egui::Frame::group(ui.style()).show(ui, |ui| {
                ui.label(RichText::new("Yeni yolcu adı:").small().strong());
                let resp = ui.add(
                    egui::TextEdit::singleline(&mut self.yeni_yolcu_ad)
                        .desired_width(ui.available_width())
                        .hint_text("Ör. Ayşe"),
                );
                let enter_basildi =
                    resp.lost_focus() && ui.input(|i| i.key_pressed(Key::Enter));
                ui.horizontal(|ui| {
                    let gecerli = !self.yeni_yolcu_ad.trim().is_empty();
                    if (ui.add_enabled(gecerli, egui::Button::new("Ekle")).clicked()
                        || enter_basildi)
                        && gecerli
                    {
                        let mevcut_idler: Vec<String> =
                            self.taslak.yolcular.iter().map(|y| y.id.clone()).collect();
                        let ad = self.yeni_yolcu_ad.trim().to_string();
                        let id = yolcu_id_uret(&ad, &mevcut_idler);
                        self.taslak.yolcular.push(Yolcu {
                            id: id.clone(),
                            ad,
                            uyruklar: vec!["TR".to_string()],
                            izinler: Vec::new(),
                            ek: serde_json::Map::new(),
                        });
                        for b in &mut self.taslak.bacaklar {
                            if !b.yolcular.contains(&id) {
                                b.yolcular.push(id.clone());
                            }
                        }
                        self.yeni_yolcu_acik = false;
                        self.yeni_yolcu_ad.clear();
                    }
                    if ui.button("İptal").clicked() {
                        self.yeni_yolcu_acik = false;
                        self.yeni_yolcu_ad.clear();
                    }
                });
            });
        }

        ui.add_space(4.0);

        let mut kaldirilacak_onay: Option<(usize, String, usize, usize)> = None;
        let mut ulke_secici_ac: Option<UlkeSeciciHedef> = None;
        let mut odaklanacak_bacak: Option<String> = None;

        let yolcu_sayisi = self.taslak.yolcular.len();
        let onizleme_klon = self.onizleme.clone();
        let plan_klon = self.taslak.clone();

        for yi in 0..yolcu_sayisi {
            let y_id = self.taslak.yolcular[yi].id.clone();
            let ad_hatali = self
                .onizleme
                .hatalar
                .iter()
                .any(|h| hata_alani_eslesir(h, &format!("yolcular.{}.ad", yi)));
            let uyruk_hatali = self
                .onizleme
                .hatalar
                .iter()
                .any(|h| hata_alani_eslesir(h, &format!("yolcular.{}.uyruklar", yi)));

            let frame_resp = egui::Frame::NONE
                .fill(kart_arka_rengi(ui))
                .stroke(Stroke::new(
                    1.0_f32,
                    if ad_hatali || uyruk_hatali {
                        Color32::from_rgb(220, 80, 80)
                    } else {
                        kenar_rengi(ui)
                    },
                ))
                .inner_margin(8.0)
                .corner_radius(6.0)
                .show(ui, |ui| {
                    // Üst satır: ad + id + sil
                    ui.horizontal(|ui| {
                        ui.label("👤");
                        let ad_edit = egui::TextEdit::singleline(&mut self.taslak.yolcular[yi].ad)
                            .desired_width((ui.available_width() - 80.0).max(80.0))
                            .hint_text("Ad");
                        ui.add(ad_edit);
                        ui.label(RichText::new(format!("({})", y_id)).small().weak());

                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            let sil_btn = ui.small_button("🗑").on_hover_text("Yolcuyu kaldır");
                            if sil_btn.clicked() {
                                let (bs, yts) = yolcu_sil_etkisi(&plan_klon, &y_id);
                                kaldirilacak_onay = Some((
                                    yi,
                                    self.taslak.yolcular[yi].ad.clone(),
                                    bs,
                                    yts,
                                ));
                            }
                        });
                    });
                    hata_metni_ciz(ui, &onizleme_klon.hatalar, &format!("yolcular.{}.ad", yi));

                    ui.add_space(4.0);

                    // Uyruk çipleri
                    ui.label(RichText::new("Uyruklar").small().weak());
                    let mut silinecek_uyruk: Option<usize> = None;
                    let uyruklar_klon = self.taslak.yolcular[yi].uyruklar.clone();
                    ui.horizontal_wrapped(|ui| {
                        for (ui_idx, kod) in uyruklar_klon.iter().enumerate() {
                            let bayrak = bicim::bayrak_emoji(kod);
                            let ad = self.ulke_adi_getir(kod);
                            if ui
                                .small_button(format!("{} {} ×", bayrak, ad))
                                .on_hover_text("Uyruğu kaldır")
                                .clicked()
                            {
                                silinecek_uyruk = Some(ui_idx);
                            }
                        }
                        if ui.small_button("+ uyruk").clicked() {
                            ulke_secici_ac = Some(UlkeSeciciHedef::Uyruk(yi));
                        }
                    });
                    if let Some(u_idx) = silinecek_uyruk {
                        self.taslak.yolcular[yi].uyruklar.remove(u_idx);
                    }
                    hata_metni_ciz(
                        ui,
                        &onizleme_klon.hatalar,
                        &format!("yolcular.{}.uyruklar", yi),
                    );

                    ui.add_space(6.0);

                    // İzinler
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("Vize / İkamet İzinleri").small().weak());
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                            if ui.small_button("+ izin").clicked() {
                                self.taslak.yolcular[yi].izinler.push(Izin {
                                    ulke: "JP".to_string(),
                                    tur: "vize".to_string(),
                                    bitis: None,
                                    durum: Some("gercek".to_string()),
                                    not: None,
                                    ek: serde_json::Map::new(),
                                });
                            }
                        });
                    });

                    if self.taslak.yolcular[yi].izinler.is_empty() {
                        ui.label(RichText::new("Ek izin kaydı yok").small().weak());
                    }

                    let mut silinecek_izin: Option<usize> = None;
                    let izin_sayisi = self.taslak.yolcular[yi].izinler.len();
                    for ii in 0..izin_sayisi {
                        let izin_klon = self.taslak.yolcular[yi].izinler[ii].clone();
                        let varsayim_mi = izin_klon.durum.as_deref() == Some("varsayim");
                        let yolculukta_bitiyor = izin_yolculukta_bitiyor_mu(
                            &izin_klon,
                            &plan_klon,
                            Some(&onizleme_klon),
                            &y_id,
                        );
                        let ulke_ad = self.ulke_adi_getir(&izin_klon.ulke);
                        let bayrak = bicim::bayrak_emoji(&izin_klon.ulke);

                        egui::Frame::NONE
                            .fill(if varsayim_mi {
                                Color32::from_rgba_unmultiplied(243, 156, 18, 18)
                            } else {
                                Color32::TRANSPARENT
                            })
                            .stroke(Stroke::new(
                                1.0_f32,
                                if yolculukta_bitiyor {
                                    Color32::from_rgb(220, 80, 80)
                                } else if varsayim_mi {
                                    Color32::from_rgb(230, 145, 30)
                                } else {
                                    kenar_rengi(ui)
                                },
                            ))
                            .inner_margin(5.0)
                            .corner_radius(4.0)
                            .show(ui, |ui| {
                                ui.horizontal(|ui| {
                                    if ui
                                        .small_button(format!("{} {}", bayrak, ulke_ad))
                                        .on_hover_text("Ülke değiştir")
                                        .clicked()
                                    {
                                        ulke_secici_ac = Some(UlkeSeciciHedef::Izin(yi, ii));
                                    }

                                    let tur_etiket = match self.taslak.yolcular[yi].izinler[ii]
                                        .tur
                                        .as_str()
                                    {
                                        "ikamet" => "İkamet",
                                        "eta" => "e-Vize/ETA",
                                        _ => "Vize",
                                    };
                                    egui::ComboBox::from_id_salt(format!(
                                        "izin_tur_{}_{}",
                                        yi, ii
                                    ))
                                    .width(90.0)
                                    .selected_text(tur_etiket)
                                    .show_ui(ui, |ui| {
                                        ui.selectable_value(
                                            &mut self.taslak.yolcular[yi].izinler[ii].tur,
                                            "vize".to_string(),
                                            "Vize",
                                        );
                                        ui.selectable_value(
                                            &mut self.taslak.yolcular[yi].izinler[ii].tur,
                                            "ikamet".to_string(),
                                            "İkamet",
                                        );
                                        ui.selectable_value(
                                            &mut self.taslak.yolcular[yi].izinler[ii].tur,
                                            "eta".to_string(),
                                            "e-Vize/ETA",
                                        );
                                    });

                                    ui.with_layout(
                                        egui::Layout::right_to_left(egui::Align::Center),
                                        |ui| {
                                            if ui.small_button("×").clicked() {
                                                silinecek_izin = Some(ii);
                                            }
                                        },
                                    );
                                });

                                // Bitiş tarihi + Süresiz kutusu
                                ui.horizontal(|ui| {
                                    let mut suresiz =
                                        self.taslak.yolcular[yi].izinler[ii].bitis.is_none();
                                    if ui.checkbox(&mut suresiz, "Süresiz").changed() {
                                        if suresiz {
                                            self.taslak.yolcular[yi].izinler[ii].bitis = None;
                                        } else {
                                            self.taslak.yolcular[yi].izinler[ii].bitis =
                                                Some("2027-06-01".to_string());
                                        }
                                    }
                                    if !suresiz {
                                        let mut tarih = self.taslak.yolcular[yi].izinler[ii]
                                            .bitis
                                            .as_deref()
                                            .and_then(tarih_ayristir)
                                            .unwrap_or_else(|| {
                                                NaiveDate::from_ymd_opt(2027, 6, 1)
                                                    .unwrap_or_default()
                                            });
                                        let id_str = format!("izin_bitis_{}_{}", yi, ii);
                                        if tarih_secici_ciz(ui, &mut tarih, &id_str) {
                                            self.taslak.yolcular[yi].izinler[ii].bitis =
                                                Some(tarih.format("%Y-%m-%d").to_string());
                                        }
                                        let iso = tarih.format("%Y-%m-%d").to_string();
                                        ui.label(
                                            RichText::new(bicim::turkce_gun_ay(&iso))
                                                .small()
                                                .weak(),
                                        );
                                    }
                                });

                                // Durum anahtarı: [Gerçek | Varsayım]
                                ui.horizontal(|ui| {
                                    let durum = self.taslak.yolcular[yi].izinler[ii]
                                        .durum
                                        .as_deref()
                                        .unwrap_or("gercek")
                                        == "varsayim";
                                    if ui.selectable_label(!durum, "Gerçek").clicked() {
                                        self.taslak.yolcular[yi].izinler[ii].durum =
                                            Some("gercek".to_string());
                                    }
                                    if ui.selectable_label(durum, "Varsayım").clicked() {
                                        self.taslak.yolcular[yi].izinler[ii].durum =
                                            Some("varsayim".to_string());
                                    }
                                });

                                if varsayim_mi {
                                    let rozet = ui.label(
                                        RichText::new("⚠ varsayım — henüz alınmadı")
                                            .small()
                                            .color(Color32::from_rgb(255, 165, 0))
                                            .strong(),
                                    );
                                    if let Some(not) = &izin_klon.not {
                                        rozet.on_hover_text(not);
                                    }
                                }
                                if yolculukta_bitiyor {
                                    ui.label(
                                        RichText::new("⛔ yolculukta bitiyor")
                                            .small()
                                            .color(Color32::from_rgb(235, 85, 85))
                                            .strong(),
                                    );
                                }
                                hata_metni_ciz(
                                    ui,
                                    &onizleme_klon.hatalar,
                                    &format!("yolcular.{}.izinler.{}", yi, ii),
                                );
                            });
                        ui.add_space(3.0);
                    }
                    if let Some(i_idx) = silinecek_izin {
                        self.taslak.yolcular[yi].izinler.remove(i_idx);
                    }

                    // Bu yolcunun girişi sorunlu bacaklar özeti
                    let sorunlu_bacaklar: Vec<(usize, &OnizlemeBacak, &YolcuDenetimi)> =
                        onizleme_klon
                            .bacaklar
                            .iter()
                            .enumerate()
                            .filter_map(|(bi, b)| {
                                b.yolcu_denetimi
                                    .iter()
                                    .find(|yd| yd.yolcu == y_id && yd.durum != "ok")
                                    .map(|yd| (bi, b, yd))
                            })
                            .collect();
                    let sorunlu_yt: Vec<(usize, &OnizlemeYanTur, &YolcuDenetimi)> = onizleme_klon
                        .yan_turlar
                        .iter()
                        .enumerate()
                        .filter_map(|(yti, yt)| {
                            yt.yolcu_denetimi
                                .iter()
                                .find(|yd| yd.yolcu == y_id && yd.durum != "ok")
                                .map(|yd| (yti, yt, yd))
                        })
                        .collect();

                    if !sorunlu_bacaklar.is_empty() || !sorunlu_yt.is_empty() {
                        ui.add_space(4.0);
                        ui.label(
                            RichText::new("Girişi sorunlu bacaklar:")
                                .small()
                                .strong(),
                        );
                        for (bi, b, yd) in sorunlu_bacaklar {
                            let engel_mi = yd.durum == "engel";
                            let renk = if engel_mi {
                                Color32::from_rgb(235, 85, 85)
                            } else {
                                Color32::from_rgb(255, 165, 0)
                            };
                            let simge = if engel_mi { "⛔" } else { "⚠" };
                            if ui
                                .small_button(
                                    RichText::new(format!("{} {}", simge, b.baslik)).color(renk),
                                )
                                .on_hover_text(&yd.mesaj)
                                .clicked()
                            {
                                odaklanacak_bacak = Some(format!("bacaklar.{}", bi));
                            }
                        }
                        for (yti, yt, yd) in sorunlu_yt {
                            let engel_mi = yd.durum == "engel";
                            let renk = if engel_mi {
                                Color32::from_rgb(235, 85, 85)
                            } else {
                                Color32::from_rgb(255, 165, 0)
                            };
                            let simge = if engel_mi { "⛔" } else { "⚠" };
                            if ui
                                .small_button(
                                    RichText::new(format!("{} {} (yan tur)", simge, yt.baslik))
                                        .color(renk),
                                )
                                .on_hover_text(&yd.mesaj)
                                .clicked()
                            {
                                odaklanacak_bacak = Some(format!("yan_turlar.{}", yti));
                            }
                        }
                    }
                });

            if self
                .odaklanacak_alan
                .as_deref()
                .is_some_and(|a| a.starts_with(&format!("yolcular.{}", yi)) || a == "yolcular")
            {
                ui.scroll_to_rect(frame_resp.response.rect, Some(egui::Align::Center));
                self.odaklanacak_alan = None;
            }
            ui.add_space(6.0);
        }

        if let Some(onay) = kaldirilacak_onay {
            self.silinecek_yolcu_onay = Some(onay);
        }
        if let Some(hedef) = ulke_secici_ac {
            self.acik_ulke_secici = Some(hedef);
            self.ulke_arama.clear();
        }
        if let Some(alan) = odaklanacak_bacak {
            self.odaklanacak_alan = Some(alan);
        }

        // F. Kısa notlar
        ui.add_space(4.0);
        ui.label(
            RichText::new("ℹ Varsayım izinleri: Hesaplarda vizesi varmış gibi davranılır; seçeneklerde ⚠ yazar.")
                .small()
                .weak(),
        );
        ui.add_space(2.0);
        ui.label(
            RichText::new("ℹ Kurallar resmî kaynak değildir — IATA Travel Centre ile teyit et.")
                .small()
                .weak(),
        );
    }

    // ======================= C. Güzergâh (Orta, Dikey Zaman Çizelgesi) =======================

    fn guzergah_bolumu_ciz(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.label(RichText::new("🗺 Güzergâh").size(15.0).strong());
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                let donus_var = donus_var_mi(&self.taslak);
                let etiket = if donus_var {
                    "Dönüş var ✓"
                } else {
                    "↩ Dönüş ekle"
                };
                let resp = ui.add_enabled(
                    !donus_var && self.taslak.duraklar.len() >= 2,
                    egui::Button::new(etiket),
                );
                if resp.clicked() {
                    let son_tarih = (0..self.taslak.duraklar.len())
                        .rev()
                        .find_map(|idx| durak_etkin_tarihi(&self.taslak.duraklar, idx))
                        .unwrap_or_else(|| {
                            NaiveDate::from_ymd_opt(2026, 12, 27).unwrap_or_default()
                        });
                    self.donus_tarihi = son_tarih + ChronoDuration::days(7);
                    self.donus_esneklik = 2;
                    self.donus_pencere_acik = true;
                }
            });
        });

        hata_metni_ciz(ui, &self.onizleme.hatalar, "duraklar");
        hata_metni_ciz(ui, &self.onizleme.hatalar, "bacaklar");
        ui.add_space(6.0);

        let mut tasima_istegi: Option<(usize, usize)> = None;
        let mut araya_ekle_indeks: Option<usize> = None;
        let mut silinecek_durak: Option<usize> = None;

        let durak_sayisi = self.taslak.duraklar.len();
        let onizleme_klon = self.onizleme.clone();
        let mut durak_merkezleri: Vec<(egui::Pos2, egui::Rect)> = Vec::new();
        let pointer_pos = ctx.input(|i| i.pointer.interact_pos());
        let pointer_birakildi = ctx.input(|i| i.pointer.any_released());
        let mut birakma_hedefi: Option<usize> = None;

        for i in 0..durak_sayisi {
            let ilk_mi = i == 0;
            let son_mu = i + 1 == durak_sayisi;

            let durak_hatali = onizleme_klon.hatalar.iter().any(|h| {
                hata_alani_eslesir(h, &format!("duraklar.{}", i))
                    || hata_alani_eslesir(h, &format!("duraklar.{}.tarih", i))
                    || hata_alani_eslesir(h, &format!("duraklar.{}.sehir", i))
                    || hata_alani_eslesir(h, &format!("duraklar.{}.esneklik", i))
            });
            let surukleme_hedefi = self
                .suruklenen_durak
                .is_some_and(|s| s != i)
                && pointer_pos.is_some();

            let mut satir_rect = egui::Rect::NOTHING;
            let mut nokta_merkez = egui::Pos2::ZERO;

            let durak_resp = ui.horizontal_top(|ui| {
                // Sol zaman çizelgesi simgesi için alan
                let (gutter_rect, _) =
                    ui.allocate_exact_size(egui::vec2(22.0, 28.0), egui::Sense::hover());
                nokta_merkez = gutter_rect.center();

                let kart_kenar = if durak_hatali {
                    Color32::from_rgb(220, 80, 80)
                } else {
                    kenar_rengi(ui)
                };

                let frame_resp = egui::Frame::NONE
                    .fill(kart_arka_rengi(ui))
                    .stroke(Stroke::new(1.0_f32, kart_kenar))
                    .inner_margin(8.0)
                    .corner_radius(6.0)
                    .show(ui, |ui| {
                        ui.vertical(|ui| {
                        ui.set_width((ui.available_width() - 4.0).max(180.0));

                        // Üst satır: sıra etiketi + şehir seçici + sürükle / ↑↓ / sil
                        ui.horizontal(|ui| {
                            let sira_etiket = if ilk_mi {
                                "Başlangıç".to_string()
                            } else if son_mu {
                                "Varış".to_string()
                            } else {
                                format!("{}. Durak", i + 1)
                            };
                            ui.label(
                                RichText::new(sira_etiket)
                                    .small()
                                    .color(if ilk_mi {
                                        Color32::from_rgb(46, 204, 113)
                                    } else if son_mu {
                                        Color32::from_rgb(52, 152, 219)
                                    } else {
                                        Color32::from_rgb(180, 190, 205)
                                    })
                                    .strong(),
                            );

                            // Şehir arama TextEdit
                            let sehir_edit_id = egui::Id::unique(("durak_sehir_edit", i));
                            let edit_genislik = (ui.available_width() - 120.0).clamp(80.0, 220.0);
                            let sehir_hatali = onizleme_klon
                                .hatalar
                                .iter()
                                .any(|h| hata_alani_eslesir(h, &format!("duraklar.{}.sehir", i)));

                            let edit_resp = ui.add(
                                egui::TextEdit::singleline(&mut self.taslak.duraklar[i].sehir)
                                    .id(sehir_edit_id)
                                    .desired_width(edit_genislik)
                                    .hint_text("Şehir ara (ör. Tokyo, Ankara)…")
                                    .text_color_opt(if sehir_hatali {
                                        Some(Color32::from_rgb(240, 100, 100))
                                    } else {
                                        None
                                    }),
                            );
                            self.yer_secici_etkilesim(
                                ctx,
                                &edit_resp,
                                YerAramaHedefi::Durak(i),
                                self.taslak.duraklar[i].sehir.clone(),
                            );

                            ui.with_layout(
                                egui::Layout::right_to_left(egui::Align::Center),
                                |ui| {
                                    if ui
                                        .add_enabled(durak_sayisi > 2, egui::Button::new("🗑").small())
                                        .on_hover_text("Bu durağı sil")
                                        .clicked()
                                    {
                                        silinecek_durak = Some(i);
                                    }
                                    if ui
                                        .add_enabled(!son_mu, egui::Button::new("↓").small())
                                        .on_hover_text("Aşağı taşı")
                                        .clicked()
                                    {
                                        tasima_istegi = Some((i, i + 1));
                                    }
                                    if ui
                                        .add_enabled(!ilk_mi, egui::Button::new("↑").small())
                                        .on_hover_text("Yukarı taşı")
                                        .clicked()
                                    {
                                        tasima_istegi = Some((i, i - 1));
                                    }
                                    let tutamac = ui
                                        .add(
                                            egui::Label::new(RichText::new("⠿").strong())
                                                .sense(egui::Sense::drag()),
                                        )
                                        .on_hover_text("Sürükleyerek sırayı değiştir");
                                    if tutamac.drag_started() {
                                        self.suruklenen_durak = Some(i);
                                    }
                                },
                            );
                        });

                        hata_metni_ciz(
                            ui,
                            &onizleme_klon.hatalar,
                            &format!("duraklar.{}.sehir", i),
                        );

                        // Seçilen şehrin noktaları (küçük tıklanabilir çipler)
                        let sehir_adi = self.taslak.duraklar[i].sehir.clone();
                        if let Some(noktalar) = self.sehir_noktalarini_bul(&sehir_adi) {
                            if !noktalar.is_empty() {
                                ui.add_space(4.0);
                                let tum_kodlar: Vec<String> =
                                    noktalar.iter().map(|n| n.kod.clone()).collect();
                                let mut secili_kodlar = self.taslak.duraklar[i]
                                    .noktalar
                                    .clone()
                                    .unwrap_or_else(|| tum_kodlar.clone());
                                let mut nokta_degisti = false;

                                ui.horizontal_wrapped(|ui| {
                                    for n in &noktalar {
                                        let aktif = secili_kodlar.contains(&n.kod);
                                        let simge = nokta_turu_simgesi(&n.tur);
                                        let kisa_ad = if n.kod.len() <= 5 {
                                            n.kod.clone()
                                        } else {
                                            n.ad.clone()
                                        };
                                        let chip = egui::Button::selectable(
                                            aktif,
                                            RichText::new(format!("{} {}", simge, kisa_ad))
                                                .small(),
                                        );
                                        if ui
                                            .add(chip)
                                            .on_hover_text(format!(
                                                "{} ({}) — tıklayarak dahil/hariç et",
                                                n.ad, n.kod
                                            ))
                                            .clicked()
                                        {
                                            if aktif {
                                                if secili_kodlar.len() > 1 {
                                                    secili_kodlar.retain(|k| k != &n.kod);
                                                    nokta_degisti = true;
                                                }
                                            } else {
                                                secili_kodlar.push(n.kod.clone());
                                                nokta_degisti = true;
                                            }
                                        }
                                    }
                                });

                                if nokta_degisti {
                                    let hepsi_secili =
                                        tum_kodlar.iter().all(|k| secili_kodlar.contains(k));
                                    self.taslak.duraklar[i].noktalar = if hepsi_secili {
                                        None
                                    } else {
                                        Some(secili_kodlar)
                                    };
                                }
                            }
                        }

                        ui.add_space(4.0);

                        // Tarih / Esneklik / Kalış (son durakta tarih yok -> "Varış")
                        if son_mu {
                            ui.label(
                                RichText::new("⚑ Varış noktası (kalkış tarihi yok)")
                                    .small()
                                    .weak(),
                            );
                        } else {
                            let gecis_mi = !ilk_mi && self.taslak.duraklar[i].gecis_noktasi;

                            if !ilk_mi {
                                ui.horizontal_wrapped(|ui| {
                                    let mut gecis = self.taslak.duraklar[i].gecis_noktasi;
                                    if ui
                                        .checkbox(&mut gecis, "Geçiş noktası (kalmadan aktarma)")
                                        .changed()
                                    {
                                        self.taslak.duraklar[i].gecis_noktasi = gecis;
                                        if gecis {
                                            self.taslak.duraklar[i].tarih = None;
                                            self.taslak.duraklar[i].kalis_gece = None;
                                        } else if self.taslak.duraklar[i].tarih.is_none() {
                                            let varsayilan =
                                                durak_etkin_tarihi(&self.taslak.duraklar, i - 1)
                                                    .map(|d| d + ChronoDuration::days(3))
                                                    .unwrap_or_else(|| {
                                                        NaiveDate::from_ymd_opt(2027, 1, 2)
                                                            .unwrap_or_default()
                                                    });
                                            self.taslak.duraklar[i].tarih =
                                                Some(varsayilan.format("%Y-%m-%d").to_string());
                                        }
                                    }
                                });
                            }

                            if gecis_mi {
                                ui.label(
                                    RichText::new("🔄 Kalmadan aktarma · tarih önceki bacaktan gelir")
                                        .small()
                                        .color(Color32::from_rgb(130, 190, 240)),
                                );
                            } else {
                                ui.horizontal_wrapped(|ui| {
                                    let etiket = if ilk_mi {
                                        "Yolculuk başlangıcı:"
                                    } else {
                                        "Kalkış:"
                                    };
                                    ui.label(RichText::new(etiket).small().strong());

                                    let mevcut_tarih = self.taslak.duraklar[i]
                                        .tarih
                                        .as_deref()
                                        .and_then(tarih_ayristir);

                                    if let Some(mut t) = mevcut_tarih {
                                        let id_str = format!("durak_tarih_{}", i);
                                        if tarih_secici_ciz(ui, &mut t, &id_str) {
                                            self.taslak.duraklar[i].tarih =
                                                Some(t.format("%Y-%m-%d").to_string());
                                            self.taslak.duraklar[i].kalis_gece = None;
                                        }
                                        let iso = t.format("%Y-%m-%d").to_string();
                                        ui.label(
                                            RichText::new(bicim::turkce_gun_ay(&iso))
                                                .small()
                                                .weak(),
                                        );
                                        if !ilk_mi {
                                            if let Some(gece) =
                                                durak_kalis_gecesi(&self.taslak.duraklar, i)
                                            {
                                                ui.label(
                                                    RichText::new(format!("· {} gece", gece))
                                                        .small()
                                                        .color(Color32::from_rgb(120, 200, 170))
                                                        .strong(),
                                                );
                                            }
                                        }
                                    } else {
                                        if ui.small_button("Tarih seç").clicked() {
                                            let varsayilan = if ilk_mi {
                                                NaiveDate::from_ymd_opt(2026, 12, 26)
                                                    .unwrap_or_default()
                                            } else {
                                                durak_etkin_tarihi(&self.taslak.duraklar, i - 1)
                                                    .map(|d| d + ChronoDuration::days(3))
                                                    .unwrap_or_else(|| {
                                                        NaiveDate::from_ymd_opt(2027, 1, 2)
                                                            .unwrap_or_default()
                                                    })
                                            };
                                            self.taslak.duraklar[i].tarih =
                                                Some(varsayilan.format("%Y-%m-%d").to_string());
                                        }
                                        if !ilk_mi {
                                            let mut gece =
                                                self.taslak.duraklar[i].kalis_gece.unwrap_or(2);
                                            ui.label(RichText::new("veya").small().weak());
                                            if ui
                                                .add(
                                                    egui::DragValue::new(&mut gece)
                                                        .range(0..=60)
                                                        .suffix(" gece"),
                                                )
                                                .changed()
                                            {
                                                self.taslak.duraklar[i].kalis_gece = Some(gece);
                                            }
                                        }
                                    }

                                    ui.separator();
                                    ui.label(RichText::new("Esneklik:").small().weak());
                                    for gun in 0u8..=3 {
                                        let secili = self.taslak.duraklar[i].esneklik == gun;
                                        if ui
                                            .selectable_label(secili, format!("±{}", gun))
                                            .clicked()
                                        {
                                            self.taslak.duraklar[i].esneklik = gun;
                                        }
                                    }
                                });
                            }

                            hata_metni_ciz(
                                ui,
                                &onizleme_klon.hatalar,
                                &format!("duraklar.{}.tarih", i),
                            );
                            hata_metni_ciz(
                                ui,
                                &onizleme_klon.hatalar,
                                &format!("duraklar.{}.esneklik", i),
                            );
                        }
                        });
                    });

                satir_rect = frame_resp.response.rect;
                if surukleme_hedefi
                    && pointer_pos.is_some_and(|p| satir_rect.contains(p))
                {
                    ui.painter().rect_stroke(
                        satir_rect,
                        6.0,
                        Stroke::new(2.0_f32, Color32::from_rgb(52, 152, 219)),
                        egui::StrokeKind::Outside,
                    );
                    if pointer_birakildi {
                        birakma_hedefi = Some(i);
                    }
                }

                if self
                    .odaklanacak_alan
                    .as_deref()
                    .is_some_and(|a| a.starts_with(&format!("duraklar.{}", i)))
                {
                    ui.scroll_to_rect(satir_rect, Some(egui::Align::Center));
                    self.odaklanacak_alan = None;
                }
            });
            let _ = durak_resp;
            durak_merkezleri.push((nokta_merkez, satir_rect));

            // Bacak kartı (iki durak arası çizgi = bacak)
            if i < self.taslak.bacaklar.len() && i + 1 < durak_sayisi {
                ui.add_space(4.0);
                let bacak_oniz = onizleme_klon.bacaklar.get(i);
                let yolcular_klon = self.taslak.yolcular.clone();
                let durak_a = self.taslak.duraklar[i].sehir.clone();
                let durak_b = self.taslak.duraklar[i + 1].sehir.clone();

                let bacak_hatali = onizleme_klon.hatalar.iter().any(|h| {
                    hata_alani_eslesir(h, &format!("bacaklar.{}", i))
                        || hata_alani_eslesir(h, &format!("bacaklar.{}.yolcular", i))
                        || hata_alani_eslesir(h, &format!("bacaklar.{}.araclar", i))
                });

                let bacak_resp = ui.horizontal_top(|ui| {
                    ui.add_space(22.0);
                    let frame_resp = egui::Frame::NONE
                        .fill(bacak_arka_rengi(ui))
                        .stroke(Stroke::new(
                            1.0_f32,
                            if bacak_hatali {
                                Color32::from_rgb(220, 80, 80)
                            } else {
                                kenar_rengi(ui)
                            },
                        ))
                        .inner_margin(8.0)
                        .corner_radius(6.0)
                        .show(ui, |ui| {
                            ui.vertical(|ui| {
                            ui.set_width((ui.available_width() - 4.0).max(170.0));

                            // Bacak başlığı + tarih penceresi
                            ui.horizontal_wrapped(|ui| {
                                let baslik = bacak_oniz
                                    .map(|b| b.baslik.clone())
                                    .unwrap_or_else(|| format!("{} → {}", durak_a, durak_b));
                                ui.label(RichText::new(baslik).strong());

                                if let Some(b) = bacak_oniz {
                                    if let (Some(e1), Some(e2)) = (&b.en_erken, &b.en_gec) {
                                        ui.label(
                                            RichText::new(format!(
                                                "· {}–{} arası aranacak",
                                                bicim::kisa_tarih(e1),
                                                bicim::kisa_tarih(e2)
                                            ))
                                            .small()
                                            .color(Color32::from_rgb(120, 190, 255)),
                                        );
                                    }
                                    if b.gecis_sonrasi {
                                        ui.label(
                                            RichText::new("aktarma sonrası")
                                                .small()
                                                .weak(),
                                        );
                                    }
                                }
                            });

                            ui.add_space(4.0);

                            // Araç düğmeleri: ✈ 🚄 🚌
                            let olasi_araclar = bacak_oniz
                                .map(|b| b.olasi_araclar.clone())
                                .filter(|v| !v.is_empty())
                                .unwrap_or_else(|| varsayilan_araclar(&durak_a, &durak_b));

                            ui.horizontal_wrapped(|ui| {
                                ui.label(RichText::new("Araç:").small().weak());
                                for kod in ["ucak", "yht", "otobus"] {
                                    let (simge, etiket) = arac_etiketi(kod);
                                    let olasi = olasi_araclar.iter().any(|x| x == kod);
                                    let secili = self.taslak.bacaklar[i]
                                        .araclar
                                        .iter()
                                        .any(|x| x == kod);

                                    let btn = egui::Button::selectable(
                                        secili && olasi,
                                        format!("{} {}", simge, etiket),
                                    );
                                    let r = ui.add_enabled(olasi, btn);
                                    if !olasi {
                                        r.on_disabled_hover_text("Bu iki şehir arasında yok");
                                    } else if r.clicked() {
                                        let araclar = &mut self.taslak.bacaklar[i].araclar;
                                        if secili {
                                            araclar.retain(|x| x != kod);
                                        } else {
                                            araclar.push(kod.to_string());
                                        }
                                    }
                                }

                                ui.separator();
                                // + Araya şehir ekle düğmesi
                                if ui
                                    .small_button("+ Araya şehir ekle")
                                    .on_hover_text("Bu iki şehrin arasına yeni durak ekle")
                                    .clicked()
                                {
                                    araya_ekle_indeks = Some(i + 1);
                                }
                            });

                            if self.taslak.bacaklar[i]
                                .araclar
                                .iter()
                                .any(|a| a == "yht" || a == "otobus")
                            {
                                ui.label(
                                    RichText::new(
                                        "🚄🚌 tarama v0.0.2'de — şimdilik yalnız uçak aranır",
                                    )
                                    .small()
                                    .color(Color32::from_rgb(200, 180, 110)),
                                );
                            }
                            hata_metni_ciz(
                                ui,
                                &onizleme_klon.hatalar,
                                &format!("bacaklar.{}.araclar", i),
                            );

                            ui.add_space(3.0);

                            // Bu bacakta kim gidiyor (yolcu çipleri)
                            ui.horizontal_wrapped(|ui| {
                                ui.label(RichText::new("Yolcular:").small().weak());
                                for y in &yolcular_klon {
                                    let gidiyor = self.taslak.bacaklar[i]
                                        .yolcular
                                        .iter()
                                        .any(|id| id == &y.id);
                                    if ui
                                        .add(egui::Button::selectable(
                                            gidiyor,
                                            RichText::new(format!("👤 {}", y.ad)).small(),
                                        ))
                                        .clicked()
                                    {
                                        let list = &mut self.taslak.bacaklar[i].yolcular;
                                        if gidiyor {
                                            list.retain(|id| id != &y.id);
                                        } else {
                                            list.push(y.id.clone());
                                        }
                                    }
                                }
                            });
                            hata_metni_ciz(
                                ui,
                                &onizleme_klon.hatalar,
                                &format!("bacaklar.{}.yolcular", i),
                            );

                            // Yolcu denetimi satırları (ok = gizli, uyari = turuncu ⚠, engel = kırmızı ⛔ + çıkar)
                            if let Some(b) = bacak_oniz {
                                for yd in &b.yolcu_denetimi {
                                    if yd.durum == "ok" {
                                        continue;
                                    }
                                    ui.horizontal_wrapped(|ui| {
                                        if yd.durum == "engel" {
                                            ui.label(
                                                RichText::new(format!("⛔ {}", yd.mesaj))
                                                    .small()
                                                    .color(Color32::from_rgb(235, 85, 85))
                                                    .strong(),
                                            );
                                            if ui.small_button("Yolcuyu çıkar").clicked() {
                                                self.taslak.bacaklar[i]
                                                    .yolcular
                                                    .retain(|id| id != &yd.yolcu);
                                            }
                                        } else {
                                            ui.label(
                                                RichText::new(format!("⚠ {}", yd.mesaj))
                                                    .small()
                                                    .color(Color32::from_rgb(255, 165, 0)),
                                            );
                                        }
                                    });
                                }
                            }
                            });
                        });

                    if self
                        .odaklanacak_alan
                        .as_deref()
                        .is_some_and(|a| a.starts_with(&format!("bacaklar.{}", i)))
                    {
                        ui.scroll_to_rect(frame_resp.response.rect, Some(egui::Align::Center));
                        self.odaklanacak_alan = None;
                    }
                });
                let _ = bacak_resp;
                ui.add_space(4.0);
            }
        }

        // Zaman çizelgesi dikey çizgisi ve durak noktalarını çiz
        if !durak_merkezleri.is_empty() {
            let painter = ui.painter();
            let cizgi_rengi = if ui.visuals().dark_mode {
                Color32::from_rgb(80, 95, 120)
            } else {
                Color32::from_rgb(160, 175, 195)
            };
            if durak_merkezleri.len() >= 2 {
                let x = durak_merkezleri[0].0.x;
                let y_bas = durak_merkezleri[0].0.y;
                let y_son = durak_merkezleri.last().map(|m| m.0.y).unwrap_or(y_bas);
                painter.line_segment(
                    [egui::pos2(x, y_bas), egui::pos2(x, y_son)],
                    Stroke::new(2.0_f32, cizgi_rengi),
                );
            }
            for (idx, (merkez, _)) in durak_merkezleri.iter().enumerate() {
                let renk = if idx == 0 {
                    Color32::from_rgb(46, 204, 113)
                } else if idx + 1 == durak_merkezleri.len() {
                    Color32::from_rgb(52, 152, 219)
                } else if self.taslak.duraklar[idx].gecis_noktasi {
                    Color32::from_rgb(243, 156, 18)
                } else {
                    Color32::from_rgb(150, 170, 200)
                };
                painter.circle_filled(*merkez, 5.5, renk);
            }
        }

        // Sürükleme bırakıldıysa sıralamayı uygula
        if pointer_birakildi {
            if let (Some(kaynak_idx), Some(hedef_idx)) = (self.suruklenen_durak, birakma_hedefi) {
                if kaynak_idx != hedef_idx {
                    tasima_istegi = Some((kaynak_idx, hedef_idx));
                }
            }
            self.suruklenen_durak = None;
        }

        // Durak mutasyonlarını uygula ve bacakları yeniden kur
        if let Some((eski_idx, yeni_idx)) = tasima_istegi {
            if eski_idx < self.taslak.duraklar.len() && yeni_idx < self.taslak.duraklar.len() {
                let eski_duraklar = self.taslak.duraklar.clone();
                let oge = self.taslak.duraklar.remove(eski_idx);
                self.taslak.duraklar.insert(yeni_idx, oge);
                bacaklari_yeniden_kur_eski_ile(&mut self.taslak, &eski_duraklar);
            }
        } else if let Some(sil_idx) = silinecek_durak {
            if self.taslak.duraklar.len() > 2 && sil_idx < self.taslak.duraklar.len() {
                let eski_duraklar = self.taslak.duraklar.clone();
                self.taslak.duraklar.remove(sil_idx);
                bacaklari_yeniden_kur_eski_ile(&mut self.taslak, &eski_duraklar);
            }
        } else if let Some(ekle_idx) = araya_ekle_indeks {
            let eski_duraklar = self.taslak.duraklar.clone();
            self.taslak.duraklar.insert(
                ekle_idx,
                Durak {
                    sehir: String::new(),
                    noktalar: None,
                    tarih: None,
                    esneklik: 0,
                    kalis_gece: None,
                    gecis_noktasi: true,
                    ek: serde_json::Map::new(),
                },
            );
            bacaklari_yeniden_kur_eski_ile(&mut self.taslak, &eski_duraklar);
        }

        ui.add_space(6.0);
        if ui.button("➕ Şehir ekle").clicked() {
            let eski_duraklar = self.taslak.duraklar.clone();
            // Önceki son durak artık ara durak oluyorsa tarih yoksa makul tarih verilebilir (kullanıcı da düzenler)
            self.taslak.duraklar.push(Durak {
                sehir: String::new(),
                noktalar: None,
                tarih: None,
                esneklik: 0,
                kalis_gece: None,
                gecis_noktasi: false,
                ek: serde_json::Map::new(),
            });
            bacaklari_yeniden_kur_eski_ile(&mut self.taslak, &eski_duraklar);
        }
    }

    // ======================= D. Yan Turlar Kartı (Sağ) =======================

    fn yan_turlar_bolumu_ciz(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.label(RichText::new("↪ Yan Turlar").size(15.0).strong());
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.small_button("+ Yan tur ekle").clicked() {
                    let ana_sehir = self
                        .taslak
                        .duraklar
                        .iter()
                        .find(|d| !d.gecis_noktasi && !d.sehir.trim().is_empty())
                        .or_else(|| self.taslak.duraklar.first())
                        .map(|d| d.sehir.clone())
                        .unwrap_or_default();
                    let ilk_yolcu = self
                        .taslak
                        .yolcular
                        .first()
                        .map(|y| vec![y.id.clone()])
                        .unwrap_or_default();
                    let (t1, t2) = crate::plan_model::plan_tarih_araligi(&self.taslak)
                        .map(|(a, b)| {
                            (
                                (a + ChronoDuration::days(1)).format("%Y-%m-%d").to_string(),
                                (b - ChronoDuration::days(1))
                                    .max(a + ChronoDuration::days(3))
                                    .format("%Y-%m-%d")
                                    .to_string(),
                            )
                        })
                        .unwrap_or_else(|| {
                            ("2026-12-28".to_string(), "2027-01-05".to_string())
                        });

                    self.taslak.yan_turlar.push(YanTurAyar {
                        ana_sehir,
                        hedef_sehir: String::new(),
                        yolcular: ilk_yolcu,
                        kalis_gece: 2,
                        kalis_esneklik: 0,
                        en_erken_gidis: Some(t1),
                        en_gec_donus: Some(t2),
                        gidis_id: None,
                        donus_id: None,
                        ek: serde_json::Map::new(),
                    });
                }
            });
        });

        ui.add_space(4.0);

        if self.taslak.yan_turlar.is_empty() {
            ui.label(
                RichText::new("Ana rotadaki bir şehirden esnek kısa gidiş-dönüş (ör. Tokyo ↔ Seul) ekleyebilirsiniz.")
                    .small()
                    .weak(),
            );
            return;
        }

        // Rotadaki tekil şehirler (ana_sehir açılır listesi için)
        let mut rota_sehirleri: Vec<String> = Vec::new();
        for d in &self.taslak.duraklar {
            let s = d.sehir.trim();
            if !s.is_empty() && !rota_sehirleri.iter().any(|x| x == s) {
                rota_sehirleri.push(s.to_string());
            }
        }

        let yolcular_klon = self.taslak.yolcular.clone();
        let onizleme_klon = self.onizleme.clone();
        let mut silinecek_yt: Option<usize> = None;

        for idx in 0..self.taslak.yan_turlar.len() {
            let yt = &self.taslak.yan_turlar[idx];
            let kimler_metni = if yt.yolcular.len() == 1 {
                let ad = yolcular_klon
                    .iter()
                    .find(|y| y.id == yt.yolcular[0])
                    .map(|y| y.ad.as_str())
                    .unwrap_or(&yt.yolcular[0]);
                format!("yalnız {}", ad)
            } else {
                format!("{} kişi", yt.yolcular.len())
            };
            let pencere_metni = match (&yt.en_erken_gidis, &yt.en_gec_donus) {
                (Some(a), Some(b)) => format!("{}–{}", bicim::kisa_tarih(a), bicim::kisa_tarih(b)),
                _ => "pencere yok".to_string(),
            };
            let baslik = format!(
                "{} ↔ {} · {} · {} gece · {}",
                if yt.ana_sehir.is_empty() {
                    "?"
                } else {
                    &yt.ana_sehir
                },
                if yt.hedef_sehir.is_empty() {
                    "?"
                } else {
                    &yt.hedef_sehir
                },
                kimler_metni,
                yt.kalis_gece,
                pencere_metni
            );

            let yt_hatali = onizleme_klon
                .hatalar
                .iter()
                .any(|h| hata_alani_eslesir(h, &format!("yan_turlar.{}", idx)));

            let frame_resp = egui::Frame::NONE
                .fill(kart_arka_rengi(ui))
                .stroke(Stroke::new(
                    1.0_f32,
                    if yt_hatali {
                        Color32::from_rgb(220, 80, 80)
                    } else {
                        kenar_rengi(ui)
                    },
                ))
                .inner_margin(8.0)
                .corner_radius(6.0)
                .show(ui, |ui| {
                    ui.set_width((ui.available_width() - 4.0).max(200.0));
                    ui.horizontal_top(|ui| {
                        let metin_gen = (ui.available_width() - 30.0).max(120.0);
                        ui.allocate_ui_with_layout(
                            egui::vec2(metin_gen, 0.0),
                            egui::Layout::top_down(egui::Align::Min),
                            |ui| {
                                ui.add(egui::Label::new(RichText::new(baslik).strong()).wrap());
                            },
                        );
                        ui.with_layout(egui::Layout::right_to_left(egui::Align::Min), |ui| {
                            if ui.small_button("🗑").on_hover_text("Yan turu sil").clicked() {
                                silinecek_yt = Some(idx);
                            }
                        });
                    });

                    ui.add_space(4.0);

                    // Ana şehir (yalnız rotadaki şehirler)
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("Ana şehir:").small().weak());
                        egui::ComboBox::from_id_salt(format!("yt_ana_sehir_{}", idx))
                            .width(150.0)
                            .selected_text(if self.taslak.yan_turlar[idx].ana_sehir.is_empty() {
                                "Seç…"
                            } else {
                                &self.taslak.yan_turlar[idx].ana_sehir
                            })
                            .show_ui(ui, |ui| {
                                for s in &rota_sehirleri {
                                    ui.selectable_value(
                                        &mut self.taslak.yan_turlar[idx].ana_sehir,
                                        s.clone(),
                                        s,
                                    );
                                }
                            });
                    });

                    // Hedef şehir (yer arama)
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("Hedef:").small().weak());
                        let hedef_edit_id = egui::Id::unique(("yt_hedef_edit", idx));
                        let resp = ui.add(
                            egui::TextEdit::singleline(
                                &mut self.taslak.yan_turlar[idx].hedef_sehir,
                            )
                            .id(hedef_edit_id)
                            .desired_width((ui.available_width() - 10.0).max(120.0))
                            .hint_text("Şehir ara (ör. Seul)…"),
                        );
                        let ctx_yer = ui.ctx().clone();
                        self.yer_secici_etkilesim(
                            &ctx_yer,
                            &resp,
                            YerAramaHedefi::YanTurHedef(idx),
                            self.taslak.yan_turlar[idx].hedef_sehir.clone(),
                        );
                    });

                    // Yolcular çipleri
                    ui.horizontal_wrapped(|ui| {
                        ui.label(RichText::new("Yolcular:").small().weak());
                        for y in &yolcular_klon {
                            let secili = self.taslak.yan_turlar[idx]
                                .yolcular
                                .iter()
                                .any(|id| id == &y.id);
                            if ui
                                .add(egui::Button::selectable(
                                    secili,
                                    RichText::new(format!("👤 {}", y.ad)).small(),
                                ))
                                .clicked()
                            {
                                let list = &mut self.taslak.yan_turlar[idx].yolcular;
                                if secili {
                                    list.retain(|id| id != &y.id);
                                } else {
                                    list.push(y.id.clone());
                                }
                            }
                        }
                    });

                    // Kalış gece + esneklik
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("Kalış:").small().weak());
                        let mut gece = self.taslak.yan_turlar[idx].kalis_gece;
                        if ui
                            .add(egui::DragValue::new(&mut gece).range(1..=30).suffix(" gece"))
                            .changed()
                        {
                            self.taslak.yan_turlar[idx].kalis_gece = gece;
                        }
                        ui.label(RichText::new("±").small().weak());
                        let mut esnek = self.taslak.yan_turlar[idx].kalis_esneklik;
                        if ui
                            .add(egui::DragValue::new(&mut esnek).range(0..=3).suffix(" gün"))
                            .changed()
                        {
                            self.taslak.yan_turlar[idx].kalis_esneklik = esnek;
                        }
                    });

                    // Pencere (iki takvim)
                    ui.horizontal_wrapped(|ui| {
                        ui.label(RichText::new("Pencere:").small().weak());
                        let mut t_erken = self.taslak.yan_turlar[idx]
                            .en_erken_gidis
                            .as_deref()
                            .and_then(tarih_ayristir)
                            .unwrap_or_else(|| {
                                NaiveDate::from_ymd_opt(2026, 12, 28).unwrap_or_default()
                            });
                        let id1 = format!("yt_erken_{}", idx);
                        if tarih_secici_ciz(ui, &mut t_erken, &id1) {
                            self.taslak.yan_turlar[idx].en_erken_gidis =
                                Some(t_erken.format("%Y-%m-%d").to_string());
                        }
                        ui.label("→");
                        let mut t_gec = self.taslak.yan_turlar[idx]
                            .en_gec_donus
                            .as_deref()
                            .and_then(tarih_ayristir)
                            .unwrap_or_else(|| {
                                NaiveDate::from_ymd_opt(2027, 1, 5).unwrap_or_default()
                            });
                        let id2 = format!("yt_gec_{}", idx);
                        if tarih_secici_ciz(ui, &mut t_gec, &id2) {
                            self.taslak.yan_turlar[idx].en_gec_donus =
                                Some(t_gec.format("%Y-%m-%d").to_string());
                        }
                    });

                    // Ön izlemeden yan tur yolcu denetimi (engel / uyarı)
                    if let Some(yt_oniz) = onizleme_klon.yan_turlar.get(idx) {
                        for yd in &yt_oniz.yolcu_denetimi {
                            if yd.durum == "ok" {
                                continue;
                            }
                            ui.horizontal_wrapped(|ui| {
                                if yd.durum == "engel" {
                                    ui.label(
                                        RichText::new(format!("⛔ {}", yd.mesaj))
                                            .small()
                                            .color(Color32::from_rgb(235, 85, 85))
                                            .strong(),
                                    );
                                    if ui.small_button("Yolcuyu çıkar").clicked() {
                                        self.taslak.yan_turlar[idx]
                                            .yolcular
                                            .retain(|id| id != &yd.yolcu);
                                    }
                                } else {
                                    ui.label(
                                        RichText::new(format!("⚠ {}", yd.mesaj))
                                            .small()
                                            .color(Color32::from_rgb(255, 165, 0)),
                                    );
                                }
                            });
                        }
                    }

                    hata_metni_ciz(ui, &onizleme_klon.hatalar, &format!("yan_turlar.{}", idx));
                });

            if self
                .odaklanacak_alan
                .as_deref()
                .is_some_and(|a| a.starts_with(&format!("yan_turlar.{}", idx)))
            {
                ui.scroll_to_rect(frame_resp.response.rect, Some(egui::Align::Center));
                self.odaklanacak_alan = None;
            }
            ui.add_space(6.0);
        }

        if let Some(idx) = silinecek_yt {
            self.taslak.yan_turlar.remove(idx);
        }
    }

    // ======================= Yardımcı Etkileşimler ve Açılır Pencereler =======================

    fn sehir_noktalarini_bul(&self, sehir: &str) -> Option<Vec<YerNokta>> {
        if let Some(n) = self.sehir_noktalari.get(sehir) {
            return Some(n.clone());
        }
        for b in &self.onizleme.bacaklar {
            if b.kalkis_sehir == sehir && !b.kalkis_noktalari.is_empty() {
                return Some(b.kalkis_noktalari.clone());
            }
            if b.varis_sehir == sehir && !b.varis_noktalari.is_empty() {
                return Some(b.varis_noktalari.clone());
            }
        }
        None
    }

    fn yer_secici_etkilesim(
        &mut self,
        ctx: &egui::Context,
        edit_resp: &egui::Response,
        hedef: YerAramaHedefi,
        guncel_metin: String,
    ) {
        if edit_resp.gained_focus() {
            self.yer_hedefi = Some(hedef.clone());
            self.yer_sorgu = guncel_metin.clone();
            self.yer_son_yazma_ani = Some(Instant::now());
            self.yer_secili_indeks = 0;
        }
        if edit_resp.changed() {
            self.yer_hedefi = Some(hedef.clone());
            self.yer_sorgu = guncel_metin.clone();
            self.yer_son_yazma_ani = Some(Instant::now());
            self.yer_secili_indeks = 0;
        }

        if self.yer_hedefi.as_ref() != Some(&hedef) {
            return;
        }

        // Klavye gezinimi (oklar + Enter + Esc)
        let mut secilen_yer: Option<Yer> = None;
        let mut kapat = false;

        if !self.yer_sonuclari.is_empty() {
            let asagi = ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::ArrowDown));
            let yukari = ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::ArrowUp));
            let enter = ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Enter));
            if asagi {
                self.yer_secili_indeks =
                    (self.yer_secili_indeks + 1).min(self.yer_sonuclari.len() - 1);
            }
            if yukari {
                self.yer_secili_indeks = self.yer_secili_indeks.saturating_sub(1);
            }
            if enter {
                secilen_yer = self.yer_sonuclari.get(self.yer_secili_indeks).cloned();
            }
        }
        if ctx.input_mut(|i| i.consume_key(Modifiers::NONE, Key::Escape)) {
            kapat = true;
        }

        let popup_pos = edit_resp.rect.left_bottom() + egui::vec2(0.0, 2.0);
        let popup_genislik = edit_resp.rect.width().max(290.0);

        let area_resp = egui::Area::new(egui::Id::unique("yer_oneri_listesi_popup"))
            .order(egui::Order::Foreground)
            .fixed_pos(popup_pos)
            .show(ctx, |ui| {
                egui::Frame::popup(ui.style()).show(ui, |ui| {
                    ui.set_width(popup_genislik);
                    if self.yer_araniyor {
                        ui.horizontal(|ui| {
                            ui.spinner();
                            ui.label(RichText::new("Şehir aranıyor…").small());
                        });
                    } else if self.yer_sorgu.trim().is_empty() {
                        ui.label(
                            RichText::new("Şehir adı veya havalimanı kodu yazın (ör. Tokyo, ank)")
                                .small()
                                .weak(),
                        );
                    } else if self.yer_sonuclari.is_empty() {
                        ui.label(RichText::new("Eşleşen şehir bulunamadı").small().weak());
                    } else {
                        for (idx, yer) in self.yer_sonuclari.iter().enumerate() {
                            let noktalar_ozet: Vec<String> = yer
                                .noktalar
                                .iter()
                                .take(4)
                                .map(|n| match n.kod.as_str() {
                                    "ANKARA_YHT" => "YHT".to_string(),
                                    "ASTI" => "AŞTİ".to_string(),
                                    _ => n.kod.clone(),
                                })
                                .collect();
                            let satir = format!(
                                "{} — {} · {}",
                                yer.sehir,
                                yer.ulke_ad,
                                noktalar_ozet.join(", ")
                            );
                            let secili = idx == self.yer_secili_indeks;
                            if ui
                                .add_sized(
                                    [ui.available_width(), 22.0],
                                    egui::Button::selectable(secili, RichText::new(satir).small()),
                                )
                                .clicked()
                            {
                                secilen_yer = Some(yer.clone());
                            }
                        }
                    }
                });
            });

        if let Some(yer) = secilen_yer {
            if !yer.noktalar.is_empty() {
                self.sehir_noktalari
                    .insert(yer.sehir.clone(), yer.noktalar.clone());
            }
            match &hedef {
                YerAramaHedefi::Durak(i) => {
                    if *i < self.taslak.duraklar.len() {
                        let eski_duraklar = self.taslak.duraklar.clone();
                        self.taslak.duraklar[*i].sehir = yer.sehir;
                        self.taslak.duraklar[*i].noktalar = None;
                        bacaklari_yeniden_kur_eski_ile(&mut self.taslak, &eski_duraklar);
                    }
                }
                YerAramaHedefi::YanTurHedef(i) => {
                    if *i < self.taslak.yan_turlar.len() {
                        self.taslak.yan_turlar[*i].hedef_sehir = yer.sehir;
                    }
                }
            }
            kapat = true;
        } else if !edit_resp.has_focus()
            && ctx.input(|i| i.pointer.any_pressed())
            && ctx
                .input(|i| i.pointer.interact_pos())
                .is_some_and(|p| !area_resp.response.rect.contains(p) && !edit_resp.rect.contains(p))
        {
            kapat = true;
        }

        if kapat {
            self.yer_hedefi = None;
            self.yer_son_yazma_ani = None;
        }
    }

    fn ulke_secici_penceresi_ciz(&mut self, ctx: &egui::Context) {
        let Some(hedef) = self.acik_ulke_secici.clone() else {
            return;
        };
        let mut acik = true;
        let mut secilen_kod: Option<String> = None;

        egui::Window::new("Ülke Seç")
            .open(&mut acik)
            .collapsible(false)
            .resizable(true)
            .default_size([320.0, 360.0])
            .show(ctx, |ui| {
                ui.add(
                    egui::TextEdit::singleline(&mut self.ulke_arama)
                        .desired_width(ui.available_width())
                        .hint_text("Ülke adı veya ISO kodu ara (ör. Japonya, JP)…"),
                );
                ui.add_space(4.0);

                let sorgu = turkce_katla(self.ulke_arama.trim());
                let mut ogeler: Vec<(&String, &String)> = self
                    .ulkeler
                    .iter()
                    .filter(|(kod, ad)| {
                        sorgu.is_empty()
                            || turkce_katla(ad).contains(&sorgu)
                            || kod.to_lowercase().contains(&sorgu)
                    })
                    .collect();
                ogeler.sort_by(|a, b| a.1.cmp(b.1));

                egui::ScrollArea::vertical()
                    .max_height(280.0)
                    .show(ui, |ui| {
                        for (kod, ad) in ogeler.into_iter().take(60) {
                            let bayrak = bicim::bayrak_emoji(kod);
                            if ui
                                .button(format!("{} {} ({})", bayrak, ad, kod))
                                .clicked()
                            {
                                secilen_kod = Some(kod.clone());
                            }
                        }
                    });
            });

        if let Some(kod) = secilen_kod {
            match hedef {
                UlkeSeciciHedef::Uyruk(yi) => {
                    if let Some(y) = self.taslak.yolcular.get_mut(yi) {
                        if !y.uyruklar.contains(&kod) {
                            y.uyruklar.push(kod);
                        }
                    }
                }
                UlkeSeciciHedef::Izin(yi, ii) => {
                    if let Some(izin) = self
                        .taslak
                        .yolcular
                        .get_mut(yi)
                        .and_then(|y| y.izinler.get_mut(ii))
                    {
                        izin.ulke = kod;
                    }
                }
            }
            self.acik_ulke_secici = None;
        } else if !acik {
            self.acik_ulke_secici = None;
        }
    }

    fn donus_penceresi_ciz(&mut self, ctx: &egui::Context) {
        if !self.donus_pencere_acik {
            return;
        }
        let mut acik = true;
        let mut onayla = false;

        egui::Window::new("↩ Dönüş Ekle")
            .open(&mut acik)
            .collapsible(false)
            .resizable(false)
            .show(ctx, |ui| {
                ui.label("Rotanın tersi (ara duraklar geçiş noktası olarak) sona eklenecek.");
                ui.add_space(6.0);
                ui.horizontal(|ui| {
                    ui.label("Dönüş tarihi:");
                    tarih_secici_ciz(ui, &mut self.donus_tarihi, "donus_ekle_tarih");
                    let iso = self.donus_tarihi.format("%Y-%m-%d").to_string();
                    ui.label(RichText::new(bicim::turkce_gun_ay(&iso)).small().weak());
                });
                ui.horizontal(|ui| {
                    ui.label("Esneklik:");
                    for gun in 0u8..=3 {
                        let secili = self.donus_esneklik == gun;
                        if ui.selectable_label(secili, format!("±{}", gun)).clicked() {
                            self.donus_esneklik = gun;
                        }
                    }
                });
                ui.add_space(8.0);
                ui.horizontal(|ui| {
                    if ui.button("Dönüşü ekle").clicked() {
                        onayla = true;
                    }
                    if ui.button("Vazgeç").clicked() {
                        self.donus_pencere_acik = false;
                    }
                });
            });

        if onayla {
            let tarih_str = self.donus_tarihi.format("%Y-%m-%d").to_string();
            donus_ekle(&mut self.taslak, &tarih_str, self.donus_esneklik);
            self.donus_pencere_acik = false;
        } else if !acik {
            self.donus_pencere_acik = false;
        }
    }

    fn yolcu_sil_onay_penceresi_ciz(&mut self, ctx: &egui::Context) {
        let Some((yi, ad, bacak_sayisi, yan_tur_sayisi)) = self.silinecek_yolcu_onay.clone()
        else {
            return;
        };
        let mut acik = true;
        let mut onayla = false;

        egui::Window::new("Yolcuyu kaldır?")
            .open(&mut acik)
            .collapsible(false)
            .resizable(false)
            .show(ctx, |ui| {
                let ozet = if yan_tur_sayisi > 0 {
                    format!(
                        "{} şu an {} bacakta ve {} yan turda yer alıyor, kaldırılsın mı?",
                        ad, bacak_sayisi, yan_tur_sayisi
                    )
                } else {
                    format!("{} şu an {} bacakta, kaldırılsın mı?", ad, bacak_sayisi)
                };
                ui.label(ozet);
                ui.add_space(8.0);
                ui.horizontal(|ui| {
                    if ui
                        .button(
                            RichText::new("Evet, kaldır")
                                .color(Color32::from_rgb(235, 90, 90))
                                .strong(),
                        )
                        .clicked()
                    {
                        onayla = true;
                    }
                    if ui.button("Vazgeç").clicked() {
                        self.silinecek_yolcu_onay = None;
                    }
                });
            });

        if onayla {
            yolcu_cikar(&mut self.taslak, yi);
            self.silinecek_yolcu_onay = None;
        } else if !acik {
            self.silinecek_yolcu_onay = None;
        }
    }
}

fn hata_metni_ciz(ui: &mut egui::Ui, hatalar: &[Hata], alan_yolu: &str) {
    for h in hatalar {
        if hata_alani_eslesir(h, alan_yolu) {
            ui.label(
                RichText::new(format!("⛔ {}", h.mesaj))
                    .small()
                    .color(Color32::from_rgb(235, 90, 90)),
            );
        }
    }
}

fn uyari_seridi_ciz(ui: &mut egui::Ui, renk: Color32, metin: &str) {
    egui::Frame::NONE
        .fill(Color32::from_rgba_unmultiplied(
            renk.r(),
            renk.g(),
            renk.b(),
            28,
        ))
        .stroke(Stroke::new(1.0_f32, renk))
        .inner_margin(6.0)
        .corner_radius(4.0)
        .show(ui, |ui| {
            ui.label(RichText::new(metin).small().color(renk));
        });
}

fn tarih_secici_ciz(ui: &mut egui::Ui, tarih: &mut NaiveDate, id_salt: &str) -> bool {
    let Ok(mut jdate) = jiff::civil::Date::new(
        tarih.year() as i16,
        tarih.month() as i8,
        tarih.day() as i8,
    ) else {
        return false;
    };
    let degisti = ui
        .add(
            DatePickerButton::new(&mut jdate)
                .id_salt(id_salt)
                .format("%d.%m.%Y"),
        )
        .changed();
    if degisti {
        if let Some(yeni) = NaiveDate::from_ymd_opt(
            jdate.year() as i32,
            jdate.month() as u32,
            jdate.day() as u32,
        ) {
            *tarih = yeni;
        }
    }
    degisti
}

fn kart_arka_rengi(ui: &egui::Ui) -> Color32 {
    tema::palet(ui.visuals()).kart_arka
}

fn bacak_arka_rengi(ui: &egui::Ui) -> Color32 {
    tema::palet(ui.visuals()).bacak_arka
}

fn kenar_rengi(ui: &egui::Ui) -> Color32 {
    tema::palet(ui.visuals()).kart_kenar
}
