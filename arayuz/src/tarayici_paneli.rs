//! Sağ "Tarayıcılar" paneli (15.11 kullanıcı isteği): hangi tarayıcı motoru çalışıyor, ne iş yapıyor,
//! bot korumasına takıldı mı (SARI) — canlı küçük görüntüler + elle doğrulama penceresi.
//! Çekirdek: GET /tarayicilar (2 sn), GET /tarayicilar/{ad}/goruntu/{sekme} (3 sn), POST/DELETE /tarayicilar/dogrulama.
//! KURAL: CAPTCHA otomatik geçilmez; yalnız kazı tarayıcısının penceresi öne getirilir, doğrulamayı kullanıcı yapar.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use eframe::egui::{self, RichText, Sense};

use crate::api::{TarayiciDurumu, TarayiciMotoru, VeriKaynagi};
use crate::tema;

const DURUM_ARALIK: Duration = Duration::from_secs(2);
const GORUNTU_ARALIK: Duration = Duration::from_secs(3);
const KART_GORUNTU_GENISLIK: f32 = 280.0;
const PENCERE_GORUNTU_GENISLIK: f32 = 640.0;
const DOGRULAMA_SURE_SN: i64 = 300;
const BILGI_SURESI: Duration = Duration::from_secs(8);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Renk {
    Yesil,
    Sari,
    Gri,
}

/// Çekirdeğin `renk` alanı → arayüz rengi (bilinmeyen = gri).
pub fn renk_sec(renk: &str) -> Renk {
    match renk.trim().to_lowercase().as_str() {
        "yesil" | "yeşil" => Renk::Yesil,
        "sari" | "sarı" => Renk::Sari,
        _ => Renk::Gri,
    }
}

/// Kartta gösterilen durum metni.
pub fn durum_metni(m: &TarayiciMotoru) -> String {
    if !m.kurulu {
        return "Kurulu değil".to_string();
    }
    if !m.destekli && m.durum != "aktif" && m.durum != "bot" {
        return "Tarama motoru değil".to_string();
    }
    match m.durum.as_str() {
        "aktif" => "Çalışıyor",
        "bot" => "Bot koruması",
        "kurulu_degil" => "Kurulu değil",
        _ => "Boşta",
    }
    .to_string()
}

/// Motor sarı (bot) ise "ad|site|zaman" anahtarı; bot olayını ayırt etmek için.
pub fn bot_anahtari(m: &TarayiciMotoru) -> Option<String> {
    if renk_sec(&m.renk) != Renk::Sari && m.durum != "bot" {
        return None;
    }
    let b = m.bot.as_ref()?;
    Some(format!("{}|{}|{}", m.ad, b.site, b.zaman))
}

/// Daha önce görülmemiş bot anahtarları (sırayla).
pub fn yeni_botlar(onceki: &HashSet<String>, motorlar: &[TarayiciMotoru]) -> Vec<String> {
    let mut gorulen = HashSet::new();
    motorlar
        .iter()
        .filter_map(bot_anahtari)
        .filter(|a| !onceki.contains(a) && gorulen.insert(a.clone()))
        .collect()
}

/// 245 → "4 dk 05 sn", 18 → "18 sn", None → "?".
pub fn kalan_metni(kalan: Option<i64>) -> String {
    match kalan {
        None => "?".to_string(),
        Some(k) => {
            let k = k.max(0);
            if k >= 60 {
                format!("{} dk {:02} sn", k / 60, k % 60)
            } else {
                format!("{k} sn")
            }
        }
    }
}

/// Tek satıra sığacak şekilde kırp (karakter sayısıyla).
fn kirp(s: &str, azami: usize) -> String {
    let s = s.trim();
    if s.chars().count() <= azami {
        s.to_string()
    } else {
        let mut k: String = s.chars().take(azami.saturating_sub(1)).collect();
        k.push('…');
        k
    }
}

/// "2026-11-15T10:20:00+03:00" → "10:20:00".
fn saat_kisa(zaman: &str) -> String {
    zaman
        .split_once('T')
        .map(|(_, s)| s.get(..8).unwrap_or(s).to_string())
        .unwrap_or_else(|| zaman.to_string())
}

/// JPEG baytları → egui görüntüsü.
fn jpeg_coz(bayt: &[u8]) -> Result<egui::ColorImage, String> {
    let img = image::load_from_memory_with_format(bayt, image::ImageFormat::Jpeg)
        .map_err(|e| format!("görüntü çözülemedi: {e}"))?
        .to_rgba8();
    let boyut = [img.width() as usize, img.height() as usize];
    Ok(egui::ColorImage::from_rgba_unmultiplied(boyut, img.as_raw()))
}

#[derive(Default)]
struct Paylasilan {
    durum: Option<TarayiciDurumu>,
    hata: Option<String>,
    durum_yukleniyor: bool,
    /// (motor ad, sekme id) → son çözülen görüntü (ana iş parçacığı dokuya çevirir)
    yeni_goruntuler: HashMap<(String, String), egui::ColorImage>,
    goruntu_yukleniyor: HashSet<(String, String)>,
    /// motor ad → son hata / bilgi (doğrulama istekleri)
    islem_mesaji: Option<(String, bool, Instant)>,
    /// POST başarısız olan motorlar: geri sayım kaldırılır, düğme yeniden basılabilir (inceleme 15.11)
    basarisiz_istekler: Vec<String>,
}

struct BotPenceresi {
    anahtar: String,
    motor_ad: String,
}

pub struct TarayiciPaneli {
    ortak: Arc<Mutex<Paylasilan>>,
    son_durum_istegi: Option<Instant>,
    son_goruntu: HashMap<(String, String), Instant>,
    dokular: HashMap<(String, String), egui::TextureHandle>,
    /// kullanıcı kart başlığına tıklayınca: motor ad → görüntüler açık mı
    goruntu_acik: HashMap<String, bool>,
    gorulen_botlar: HashSet<String>,
    kapatilan_botlar: HashSet<String>,
    pencere: Option<BotPenceresi>,
    /// motor ad → doğrulama istendi (bitiş anı)
    dogrulama_bitis: HashMap<String, Instant>,
    /// "✓ site doğrulaması geçildi" bilgisi
    bilgi: Option<(String, Instant)>,
    pub gorunur: bool,
}

impl Default for TarayiciPaneli {
    fn default() -> Self {
        Self {
            ortak: Arc::default(),
            son_durum_istegi: None,
            son_goruntu: HashMap::new(),
            dokular: HashMap::new(),
            goruntu_acik: HashMap::new(),
            gorulen_botlar: HashSet::new(),
            kapatilan_botlar: HashSet::new(),
            pencere: None,
            dogrulama_bitis: HashMap::new(),
            bilgi: None,
            gorunur: true,
        }
    }
}

enum Eylem {
    DogrulamaIste { ad: String, site: String, sekme_id: Option<String> },
    Gectim { ad: String, site: String },
}

impl TarayiciPaneli {
    /// Durumu arka planda 2 sn'de bir çek (aynı anda tek istek).
    fn durum_yukle(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context) {
        let simdi = Instant::now();
        if self.son_durum_istegi.is_some_and(|t| simdi.duration_since(t) < DURUM_ARALIK) {
            return;
        }
        {
            let mut o = self.ortak.lock().unwrap();
            if o.durum_yukleniyor {
                return;
            }
            o.durum_yukleniyor = true;
        }
        self.son_durum_istegi = Some(simdi);
        let kaynak = kaynak.clone();
        let ortak = Arc::clone(&self.ortak);
        let ctx = ctx.clone();
        thread::spawn(move || {
            let yanit = kaynak.tarayicilar();
            let mut o = ortak.lock().unwrap();
            o.durum_yukleniyor = false;
            match yanit {
                Ok(d) => {
                    o.durum = Some(d);
                    o.hata = None;
                }
                Err(e) => o.hata = Some(e),
            }
            ctx.request_repaint();
        });
    }

    /// Bir sekmenin görüntüsünü arka planda çek (aralık dolduysa ve önceki istek bittiyse).
    fn goruntu_iste(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context, ad: &str, sekme_id: &str, genislik: u32) {
        let anahtar = (ad.to_string(), sekme_id.to_string());
        let simdi = Instant::now();
        if self.son_goruntu.get(&anahtar).is_some_and(|t| simdi.duration_since(*t) < GORUNTU_ARALIK) {
            return;
        }
        {
            let mut o = self.ortak.lock().unwrap();
            if !o.goruntu_yukleniyor.insert(anahtar.clone()) {
                return;
            }
        }
        self.son_goruntu.insert(anahtar.clone(), simdi);
        let kaynak = kaynak.clone();
        let ortak = Arc::clone(&self.ortak);
        let ctx = ctx.clone();
        let (ad, sekme_id) = (ad.to_string(), sekme_id.to_string());
        thread::spawn(move || {
            let sonuc = kaynak.tarayici_goruntu(&ad, &sekme_id, genislik).and_then(|b| jpeg_coz(&b));
            let mut o = ortak.lock().unwrap();
            o.goruntu_yukleniyor.remove(&anahtar);
            if let Ok(img) = sonuc {
                o.yeni_goruntuler.insert(anahtar, img);
                ctx.request_repaint();
            }
        });
    }

    /// Arka planda çözülen görüntüleri dokuya çevir; kaybolan sekmelerin dokularını at.
    fn dokulari_guncelle(&mut self, ctx: &egui::Context, motorlar: &[TarayiciMotoru]) {
        let yeniler: Vec<_> = {
            let mut o = self.ortak.lock().unwrap();
            o.yeni_goruntuler.drain().collect()
        };
        for (anahtar, img) in yeniler {
            match self.dokular.get_mut(&anahtar) {
                Some(d) => d.set(img, egui::TextureOptions::LINEAR),
                None => {
                    let ad = format!("tarayici_{}_{}", anahtar.0, anahtar.1);
                    let d = ctx.load_texture(ad, img, egui::TextureOptions::LINEAR);
                    self.dokular.insert(anahtar, d);
                }
            }
        }
        let var: HashSet<(String, String)> = motorlar
            .iter()
            .flat_map(|m| m.sekmeler.iter().map(|s| (m.ad.clone(), s.id.clone())))
            .collect();
        self.dokular.retain(|k, _| var.contains(k));
        self.son_goruntu.retain(|k, _| var.contains(k));
    }

    fn eylem_yap(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context, eylem: Eylem) {
        let kaynak = kaynak.clone();
        let ortak = Arc::clone(&self.ortak);
        let ctx = ctx.clone();
        match eylem {
            Eylem::DogrulamaIste { ad, site, sekme_id } => {
                self.dogrulama_bitis
                    .insert(ad.clone(), Instant::now() + Duration::from_secs(DOGRULAMA_SURE_SN as u64));
                thread::spawn(move || {
                    let sonuc = kaynak.dogrulama_iste(&site, DOGRULAMA_SURE_SN, &ad, sekme_id.as_deref());
                    let mut o = ortak.lock().unwrap();
                    o.islem_mesaji = Some(match sonuc {
                        Ok(v) if v.get("gosterildi").and_then(|g| g.as_bool()) == Some(false) => (
                            "Tarayıcı penceresi öne getirilemedi — tarayıcıyı kendin aç; tarama süre tanıyor".to_string(),
                            false,
                            Instant::now(),
                        ),
                        Ok(_) => ("Tarayıcı penceresi öne getirildi".to_string(), true, Instant::now()),
                        Err(e) => {
                            o.basarisiz_istekler.push(ad.clone());
                            (format!("Doğrulama isteği gönderilemedi: {e}"), false, Instant::now())
                        }
                    });
                    ctx.request_repaint();
                });
            }
            Eylem::Gectim { ad, site } => {
                self.dogrulama_bitis.remove(&ad);
                thread::spawn(move || {
                    if let Err(e) = kaynak.dogrulama_iptal(&site) {
                        ortak.lock().unwrap().islem_mesaji =
                            Some((format!("Doğrulama iptali gönderilemedi: {e}"), false, Instant::now()));
                    }
                    ctx.request_repaint();
                });
            }
        }
    }

    /// Her kare: durum çek, dokuları güncelle, yeni bot olaylarını işle.
    /// `gorunur_mu`: panel açıkken görüntüler de çekilir.
    fn guncelle(&mut self, kaynak: &VeriKaynagi, ctx: &egui::Context) -> (Option<TarayiciDurumu>, Option<String>) {
        self.durum_yukle(kaynak, ctx);
        ctx.request_repaint_after(DURUM_ARALIK);
        let (durum, hata, basarisiz) = {
            let mut o = self.ortak.lock().unwrap();
            (o.durum.clone(), o.hata.clone(), std::mem::take(&mut o.basarisiz_istekler))
        };
        for ad in basarisiz {
            self.dogrulama_bitis.remove(&ad);
        }
        let Some(d) = &durum else {
            return (None, hata);
        };
        // Hata varsa (çekirdek yanıt vermiyor) eski durum gösterilmez.
        if hata.is_some() {
            return (None, hata);
        }
        self.dokulari_guncelle(ctx, &d.motorlar);

        // Yeni bot olayı → pencereyi aç (kapatılmış olaylar tekrar açılmaz).
        for a in yeni_botlar(&self.gorulen_botlar, &d.motorlar) {
            self.gorulen_botlar.insert(a.clone());
            if self.kapatilan_botlar.contains(&a) {
                continue;
            }
            if let Some(m) = d.motorlar.iter().find(|m| bot_anahtari(m).as_deref() == Some(a.as_str())) {
                self.pencere = Some(BotPenceresi { anahtar: a, motor_ad: m.ad.clone() });
            }
        }
        // Açık pencerenin botu bittiyse kapat + bilgi göster.
        if let Some(p) = &self.pencere {
            let hala = d.motorlar.iter().any(|m| bot_anahtari(m).as_deref() == Some(p.anahtar.as_str()));
            if !hala {
                let site = p.anahtar.split('|').nth(1).unwrap_or("").to_string();
                self.bilgi = Some((format!("✓ {site} doğrulaması geçildi"), Instant::now()));
                self.dogrulama_bitis.remove(&p.motor_ad);
                self.pencere = None;
            }
        }
        // Bitmiş eski bot olaylarını bellekten temizle (set şişmesin).
        let aktif: HashSet<String> = d.motorlar.iter().filter_map(bot_anahtari).collect();
        self.kapatilan_botlar.retain(|a| aktif.contains(a));
        (durum, hata)
    }

    pub fn ciz(&mut self, ctx: &egui::Context, ui: &mut egui::Ui, kaynak: &VeriKaynagi) {
        let (durum, hata) = self.guncelle(kaynak, ctx);
        let p = tema::palet(ui.visuals());
        let motorlar: Vec<TarayiciMotoru> = durum.as_ref().map(|d| d.motorlar.clone()).unwrap_or_default();
        let istekler = durum.as_ref().map(|d| d.dogrulama_istekleri.clone()).unwrap_or_default();

        ui.heading("🖥 Tarayıcılar");
        ui.label(
            RichText::new("yeşil=çalışıyor · sarı=bot koruması · gri=boşta").weak().size(12.0),
        );
        if let Some((m, t)) = &self.bilgi {
            if t.elapsed() < BILGI_SURESI {
                ui.colored_label(p.basari, m);
            }
        }
        ui.add_space(4.0);

        if hata.is_some() || motorlar.is_empty() {
            let mut ayrinti = String::new();
            if let Some(h) = &hata {
                ayrinti = h.clone();
            }
            egui::Frame::NONE
                .stroke(egui::Stroke::new(1.0, p.kart_kenar))
                .corner_radius(8)
                .inner_margin(10)
                .show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    ui.horizontal(|ui| {
                        ui.label(RichText::new("●").color(p.kapali).size(20.0));
                        ui.label(RichText::new("çekirdek yanıt vermiyor").color(p.kapali).strong());
                    });
                    if !ayrinti.is_empty() {
                        ui.label(RichText::new(kirp(&ayrinti, 120)).weak().size(11.0)).on_hover_text(ayrinti);
                    }
                });
            return;
        }

        let mut eylemler: Vec<Eylem> = Vec::new();
        egui::ScrollArea::vertical().id_salt("tarayici_kaydir").auto_shrink([false, false]).show(ui, |ui| {
            for m in &motorlar {
                self.kart_ciz(ctx, ui, kaynak, m, &istekler, &mut eylemler);
                ui.add_space(6.0);
            }
        });
        for e in eylemler {
            self.eylem_yap(kaynak, ctx, e);
        }
    }

    fn kart_ciz(
        &mut self,
        ctx: &egui::Context,
        ui: &mut egui::Ui,
        kaynak: &VeriKaynagi,
        m: &TarayiciMotoru,
        istekler: &HashMap<String, i64>,
        eylemler: &mut Vec<Eylem>,
    ) {
        let p = tema::palet(ui.visuals());
        let renk = renk_sec(&m.renk);
        let renk32 = match renk {
            Renk::Yesil => p.basari,
            Renk::Sari => p.uyari,
            Renk::Gri => p.kapali,
        };
        let varsayilan_acik = renk != Renk::Gri;
        let acik = *self.goruntu_acik.get(&m.ad).unwrap_or(&varsayilan_acik);
        let bot_site = m.bot.as_ref().map(|b| b.site.clone()).unwrap_or_default();

        let yanit = egui::Frame::NONE
            .fill(p.kart_arka)
            .stroke(egui::Stroke::new(1.0, p.kart_kenar))
            .corner_radius(8)
            .inner_margin(egui::Margin { left: 14, right: 10, top: 8, bottom: 8 })
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.horizontal(|ui| {
                    ui.label(RichText::new("●").color(renk32).size(22.0));
                    ui.vertical(|ui| {
                        ui.spacing_mut().item_spacing.y = 0.0;
                        ui.label(RichText::new(&m.etiket).strong().size(14.5));
                        ui.label(RichText::new(durum_metni(m)).color(renk32).size(12.5));
                    });
                });
                if !m.aciklama.is_empty() {
                    ui.label(RichText::new(kirp(&m.aciklama, 60)).weak().size(11.0)).on_hover_text(&m.aciklama);
                }
                if !m.is_ozeti.is_empty() {
                    ui.label(RichText::new(kirp(&m.is_ozeti, 46)).size(12.5)).on_hover_text(&m.is_ozeti);
                }
                for s in &m.sekmeler {
                    let metin = if s.baslik.is_empty() { s.site.clone() } else { format!("{} — {}", s.site, s.baslik) };
                    let renk = if s.bot { p.uyari } else { ui.visuals().text_color() };
                    ui.label(RichText::new(format!("▪ {}", kirp(&metin, 44))).size(12.0).color(renk))
                        .on_hover_text(format!("{}\n{}", s.baslik, s.url));
                }
                // Canlı görüntüler
                if acik && m.cdp_port.is_some() {
                    for s in &m.sekmeler {
                        self.goruntu_iste(kaynak, ctx, &m.ad, &s.id, 480);
                        if let Some(d) = self.dokular.get(&(m.ad.clone(), s.id.clone())) {
                            let w = ui.available_width().min(KART_GORUNTU_GENISLIK);
                            let oran = d.size()[1] as f32 / d.size()[0].max(1) as f32;
                            ui.add(egui::Image::new(d).fit_to_exact_size(egui::vec2(w, w * oran)).corner_radius(4));
                        } else {
                            ui.label(RichText::new("görüntü alınıyor…").weak().size(11.0));
                        }
                    }
                }
                // SARI: elle doğrulama
                if renk == Renk::Sari {
                    ui.add_space(4.0);
                    let site = if bot_site.is_empty() { m.sekmeler.first().map(|s| s.site.clone()).unwrap_or_default() } else { bot_site.clone() };
                    let sekme_id = m.bot.as_ref().and_then(|b| b.sekme_id.clone());
                    self.dogrulama_denetimi(ui, m, &site, sekme_id, istekler, eylemler);
                }
                if !m.son_kayitlar.is_empty() {
                    ui.add_space(2.0);
                    for k in m.son_kayitlar.iter().take(5) {
                        let site = k.site.clone().map(|s| format!("{s} · ")).unwrap_or_default();
                        let metin = format!("{} {}{}", saat_kisa(&k.zaman), site, k.mesaj);
                        let r = RichText::new(kirp(&metin, 52)).size(10.5);
                        let r = match k.seviye.as_str() {
                            "hata" => r.color(p.hata),
                            "uyari" => r.color(p.uyari),
                            _ => r.weak(),
                        };
                        ui.label(r).on_hover_text(metin);
                    }
                }
            });
        // Sol kenarda renk şeridi
        let r = yanit.response.rect;
        let serit = egui::Rect::from_min_max(r.min, egui::pos2(r.min.x + 5.0, r.max.y));
        ui.painter().rect_filled(
            serit,
            egui::CornerRadius { nw: 8, sw: 8, ne: 0, se: 0 },
            renk32,
        );
        // Kart başlığına (üst şerit) tıklayınca görüntüler aç/kapa
        let baslik_alani = egui::Rect::from_min_size(r.min, egui::vec2(r.width(), 44.0));
        let tik = ui.interact(baslik_alani, egui::Id::unique(("tarayici_baslik", m.ad.as_str())), Sense::click());
        if tik.clicked() {
            self.goruntu_acik.insert(m.ad.clone(), !acik);
        }
        if m.cdp_port.is_some() {
            tik.on_hover_text(if acik { "Canlı görüntüleri gizle" } else { "Canlı görüntüleri göster" });
        }
    }

    fn dogrulama_denetimi(
        &mut self,
        ui: &mut egui::Ui,
        m: &TarayiciMotoru,
        site: &str,
        sekme_id: Option<String>,
        istekler: &HashMap<String, i64>,
        eylemler: &mut Vec<Eylem>,
    ) {
        let p = tema::palet(ui.visuals());
        let istek_kalan = istekler.get(site).copied().or_else(|| {
            self.dogrulama_bitis
                .get(&m.ad)
                .map(|b| b.saturating_duration_since(Instant::now()).as_secs() as i64)
                .filter(|k| *k > 0)
        });
        match istek_kalan {
            Some(kalan) => {
                ui.label(
                    RichText::new(format!(
                        "Brave penceresi öne getirildi — doğrulamayı tarayıcıda geç; tarama {} bekliyor",
                        kalan_metni(Some(m.bot.as_ref().and_then(|b| b.kalan_sn).unwrap_or(kalan)))
                    ))
                    .color(p.uyari)
                    .size(12.0),
                );
                ui.horizontal(|ui| {
                    if ui.button("✓ Geçtim").clicked() {
                        eylemler.push(Eylem::Gectim { ad: m.ad.clone(), site: site.to_string() });
                    }
                    if ui.button("Vazgeç").clicked() {
                        eylemler.push(Eylem::Gectim { ad: m.ad.clone(), site: site.to_string() });
                    }
                });
            }
            None => {
                if ui
                    .button("🧩 Elle doğrula")
                    .on_hover_text("Kazı tarayıcısının penceresini öne getirir; doğrulamayı sen yaparsın (otomatik geçilmez)")
                    .clicked()
                {
                    eylemler.push(Eylem::DogrulamaIste { ad: m.ad.clone(), site: site.to_string(), sekme_id });
                }
            }
        }
        self.islem_mesaji_goster(ui);
    }

    fn islem_mesaji_goster(&self, ui: &mut egui::Ui) {
        let p = tema::palet(ui.visuals());
        let mesaj = self.ortak.lock().unwrap().islem_mesaji.clone();
        if let Some((m, tamam, t)) = mesaj {
            if t.elapsed() < BILGI_SURESI {
                ui.label(RichText::new(m).size(11.5).color(if tamam { p.basari } else { p.hata }));
            }
        }
    }

    /// Bot penceresi: panel kapalıyken de çizilir.
    pub fn pencere_ciz(&mut self, ctx: &egui::Context, kaynak: &VeriKaynagi) {
        // Panel kapalıyken durum güncellemesi burada da yürür (ciz çağrılmıyorsa).
        if !self.gorunur {
            let _ = self.guncelle(kaynak, ctx);
        }
        let Some(pencere) = self.pencere.as_ref() else {
            // Kısa "geçildi" bilgisi
            self.gecildi_bilgisi_ciz(ctx);
            return;
        };
        let (anahtar, motor_ad) = (pencere.anahtar.clone(), pencere.motor_ad.clone());
        let motor = {
            let o = self.ortak.lock().unwrap();
            o.durum.as_ref().and_then(|d| d.motorlar.iter().find(|m| m.ad == motor_ad).cloned())
        };
        let Some(m) = motor else {
            return;
        };
        let bot = m.bot.clone().unwrap_or_default();
        let site = bot.site.clone();
        let sekme_id = bot.sekme_id.clone().or_else(|| m.sekmeler.iter().find(|s| s.bot).map(|s| s.id.clone()))
            .or_else(|| m.sekmeler.iter().find(|s| s.site == site).map(|s| s.id.clone()));
        let p = tema::palet(&ctx.global_style().visuals);

        // Büyük canlı görüntü
        if let Some(id) = &sekme_id {
            if m.cdp_port.is_some() {
                self.goruntu_iste(kaynak, ctx, &m.ad, id, 960);
            }
        }

        let mut acik = true;
        let mut kapat = false;
        let mut eylemler: Vec<Eylem> = Vec::new();
        let istekler = self.ortak.lock().unwrap().durum.as_ref().map(|d| d.dogrulama_istekleri.clone()).unwrap_or_default();
        egui::Window::new(format!("⚠ Bot koruması: {site}"))
            .id(egui::Id::unique("tarayici_bot_penceresi"))
            .open(&mut acik)
            .collapsible(false)
            .resizable(true)
            .default_width(680.0)
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.label(RichText::new("●").color(p.uyari).size(22.0));
                    ui.label(RichText::new(&m.etiket).strong().size(15.0));
                });
                if !bot.url.is_empty() {
                    ui.label(RichText::new(&bot.url).monospace().size(12.0)).on_hover_text(&bot.url);
                }
                ui.add_space(4.0);
                if let Some(id) = &sekme_id {
                    match self.dokular.get(&(m.ad.clone(), id.clone())) {
                        Some(d) => {
                            let w = ui.available_width().min(PENCERE_GORUNTU_GENISLIK);
                            let oran = d.size()[1] as f32 / d.size()[0].max(1) as f32;
                            ui.add(egui::Image::new(d).fit_to_exact_size(egui::vec2(w, w * oran)).corner_radius(4));
                        }
                        None => {
                            ui.label(RichText::new("görüntü alınıyor…").weak());
                        }
                    }
                } else {
                    ui.label(RichText::new("Bu sekme için canlı görüntü yok").weak());
                }
                ui.add_space(6.0);
                ui.label(
                    "Otomatik geçilmez. 'Elle doğrula'ya bas → kazı tarayıcısının penceresi öne gelir; \
                     CAPTCHA / basılı tut / rakam girişini sen yap. Doğrulama geçince bu pencere kendiliğinden kapanır.",
                );
                let kalan = istekler.get(&site).copied().or(bot.kalan_sn.filter(|_| bot.bekliyor));
                if let Some(k) = kalan {
                    ui.label(
                        RichText::new(format!("Tarama {} bekliyor", kalan_metni(Some(k)))).color(p.uyari).strong(),
                    );
                }
                self.islem_mesaji_goster(ui);
                ui.add_space(6.0);
                ui.horizontal(|ui| {
                    if ui.button("🧩 Elle doğrula (5 dk bekle)").clicked() {
                        eylemler.push(Eylem::DogrulamaIste {
                            ad: m.ad.clone(),
                            site: site.clone(),
                            sekme_id: sekme_id.clone(),
                        });
                    }
                    if ui.button("✓ Geçtim").clicked() {
                        eylemler.push(Eylem::Gectim { ad: m.ad.clone(), site: site.clone() });
                    }
                    if ui.button("Kapat").clicked() {
                        kapat = true;
                    }
                });
            });
        if !acik || kapat {
            self.kapatilan_botlar.insert(anahtar);
            self.pencere = None;
        }
        for e in eylemler {
            self.eylem_yap(kaynak, ctx, e);
        }
        ctx.request_repaint_after(Duration::from_secs(1));
    }

    fn gecildi_bilgisi_ciz(&mut self, ctx: &egui::Context) {
        let Some((metin, t)) = &self.bilgi else {
            return;
        };
        if t.elapsed() >= BILGI_SURESI {
            self.bilgi = None;
            return;
        }
        // Panel açıkken bilgi başlıkta gösterilir; kapalıyken küçük bir kutu.
        if self.gorunur {
            return;
        }
        let p = tema::palet(&ctx.global_style().visuals);
        egui::Area::new(egui::Id::unique("tarayici_gecildi_bilgisi"))
            .anchor(egui::Align2::RIGHT_TOP, [-16.0, 56.0])
            .order(egui::Order::Foreground)
            .interactable(false)
            .show(ctx, |ui| {
                egui::Frame::NONE
                    .fill(p.basari_dolgu)
                    .stroke(egui::Stroke::new(1.2, p.basari))
                    .corner_radius(8)
                    .inner_margin(egui::Margin::symmetric(14, 10))
                    .show(ui, |ui| {
                        ui.label(RichText::new(metin).color(p.basari).strong());
                    });
            });
        ctx.request_repaint_after(Duration::from_secs(1));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::BotDurumu;

    fn motor(ad: &str, durum: &str, renk: &str) -> TarayiciMotoru {
        TarayiciMotoru {
            ad: ad.into(),
            etiket: ad.into(),
            kurulu: true,
            destekli: true,
            durum: durum.into(),
            renk: renk.into(),
            ..Default::default()
        }
    }

    fn botlu(ad: &str, site: &str, zaman: &str) -> TarayiciMotoru {
        let mut m = motor(ad, "bot", "sari");
        m.bot = Some(BotDurumu { site: site.into(), zaman: zaman.into(), ..Default::default() });
        m
    }

    #[test]
    fn renk_sec_bilinen_ve_bilinmeyen() {
        assert_eq!(renk_sec("yesil"), Renk::Yesil);
        assert_eq!(renk_sec("sari"), Renk::Sari);
        assert_eq!(renk_sec("gri"), Renk::Gri);
        assert_eq!(renk_sec("mor"), Renk::Gri);
        assert_eq!(renk_sec(" SARI "), Renk::Sari);
    }

    #[test]
    fn durum_metni_her_durum() {
        assert_eq!(durum_metni(&motor("a", "aktif", "yesil")), "Çalışıyor");
        assert_eq!(durum_metni(&motor("a", "bot", "sari")), "Bot koruması");
        assert_eq!(durum_metni(&motor("a", "bosta", "gri")), "Boşta");
        let mut k = motor("a", "kurulu_degil", "gri");
        k.kurulu = false;
        assert_eq!(durum_metni(&k), "Kurulu değil");
        let mut d = motor("google_chrome", "bosta", "gri");
        d.destekli = false;
        assert_eq!(durum_metni(&d), "Tarama motoru değil");
    }

    #[test]
    fn bot_anahtari_yalniz_sari_botta() {
        assert_eq!(bot_anahtari(&motor("a", "aktif", "yesil")), None);
        assert_eq!(bot_anahtari(&motor("a", "bot", "sari")), None, "bot bilgisi yoksa anahtar yok");
        assert_eq!(
            bot_anahtari(&botlu("brave_kazi", "x.com", "10:00")),
            Some("brave_kazi|x.com|10:00".to_string())
        );
    }

    #[test]
    fn yeni_botlar_yalniz_gorulmemis_olanlari_verir() {
        let motorlar = vec![botlu("brave_kazi", "x.com", "10:00"), motor("b", "aktif", "yesil"), botlu("brave_yerel", "y.com", "10:01")];
        let mut onceki = HashSet::new();
        assert_eq!(yeni_botlar(&onceki, &motorlar).len(), 2);
        onceki.insert("brave_kazi|x.com|10:00".to_string());
        assert_eq!(yeni_botlar(&onceki, &motorlar), vec!["brave_yerel|y.com|10:01".to_string()]);
        // aynı motor, aynı site, yeni zaman → yeni olay
        let yeni = vec![botlu("brave_kazi", "x.com", "10:05")];
        assert_eq!(yeni_botlar(&onceki, &yeni), vec!["brave_kazi|x.com|10:05".to_string()]);
    }

    #[test]
    fn kalan_metni_bicimleri() {
        assert_eq!(kalan_metni(Some(245)), "4 dk 05 sn");
        assert_eq!(kalan_metni(Some(300)), "5 dk 00 sn");
        assert_eq!(kalan_metni(Some(18)), "18 sn");
        assert_eq!(kalan_metni(Some(-3)), "0 sn");
        assert_eq!(kalan_metni(None), "?");
    }

    #[test]
    fn kirp_uzun_metni_keser() {
        assert_eq!(kirp("kısa", 10), "kısa");
        let k = kirp("çok uzun bir iş özeti metni", 10);
        assert_eq!(k.chars().count(), 10);
        assert!(k.ends_with('…'));
    }

    #[test]
    fn saat_kisa_iso_zamandan() {
        assert_eq!(saat_kisa("2026-11-15T10:20:00+03:00"), "10:20:00");
        assert_eq!(saat_kisa("bozuk"), "bozuk");
    }

    #[test]
    fn sozlesme_ornegi_ayristirilir() {
        let json = r#"{"zaman": "2026-11-15T10:20:00+03:00",
          "dogrulama_istekleri": {"hepsiburada.com": 240},
          "motorlar": [{
            "ad": "brave_kazi", "etiket": "Brave (kazı)", "kurulu": true, "destekli": true,
            "durum": "bot", "renk": "sari",
            "calisiyor": true, "cdp_port": 9334,
            "sekmeler": [{"id": "ABC", "url": "https://hepsiburada.com/x", "baslik": "Just a moment", "site": "hepsiburada.com", "bot": true}],
            "is_ozeti": "hepsiburada.com — sayfa açıldı: /ara?q=rtx",
            "son_kayitlar": [{"zaman": "2026-11-15T10:19:00+03:00", "seviye": "bilgi", "site": "hepsiburada.com", "mesaj": "sayfa açıldı"}],
            "bot": {"site": "hepsiburada.com", "url": "https://hepsiburada.com/x", "zaman": "2026-11-15T10:19:30+03:00",
                    "bekliyor": true, "kalan_sn": 18, "sekme_id": null},
            "aciklama": "Ana tarama tarayıcısı (kalıcı profil)"},
            {"ad": "firefox", "etiket": "Firefox", "kurulu": false, "destekli": false, "durum": "kurulu_degil",
             "renk": "gri", "calisiyor": false, "cdp_port": null, "sekmeler": [], "is_ozeti": "",
             "son_kayitlar": [], "bot": null, "aciklama": ""}]}"#;
        let d: TarayiciDurumu = serde_json::from_str(json).expect("ayrıştırılmalı");
        assert_eq!(d.motorlar.len(), 2);
        assert_eq!(d.dogrulama_istekleri.get("hepsiburada.com"), Some(&240));
        let b = &d.motorlar[0];
        assert_eq!(b.cdp_port, Some(9334));
        assert!(b.sekmeler[0].bot);
        assert_eq!(b.son_kayitlar[0].site.as_deref(), Some("hepsiburada.com"));
        let bot = b.bot.as_ref().unwrap();
        assert_eq!((bot.bekliyor, bot.kalan_sn, bot.sekme_id.clone()), (true, Some(18), None));
        assert_eq!(renk_sec(&b.renk), Renk::Sari);
        assert_eq!(durum_metni(&d.motorlar[1]), "Kurulu değil");
        assert!(bot_anahtari(b).is_some());
    }

    #[test]
    fn eksik_alanlar_varsayilanla_dolar() {
        let d: TarayiciDurumu = serde_json::from_str(r#"{"motorlar":[{"ad":"x"}]}"#).unwrap();
        assert_eq!(d.motorlar[0].ad, "x");
        assert!(d.motorlar[0].sekmeler.is_empty());
        assert!(d.motorlar[0].bot.is_none());
        let bos: TarayiciDurumu = serde_json::from_str("{}").unwrap();
        assert!(bos.motorlar.is_empty());
    }
}
