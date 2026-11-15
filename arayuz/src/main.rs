mod api;
mod bicim;
mod http_turu;
mod kayitlar;
mod paralel_ayar;
mod plan_ekrani;
mod plan_model;
mod tarama_alarm;
mod tarayici_paneli;
mod tema;

use std::collections::HashSet;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use eframe::egui::{self, ColorImage, Color32, FontData, FontDefinitions, FontFamily, RichText, Sense, Stroke};
use egui_extras::{Column, TableBuilder};
use egui_plot::{HLine, Legend, Line, Plot, PlotPoints};
use serde::{Deserialize, Serialize};

use crate::api::{
    ws_olaylar_url, AlarmYanit, DetayTeklif, ElleTaramaYanit, OlayMesaji, PanoUrun, PanoYanit,
    SeyahatYanit, SistemToplami, SiteSaglik, TaksitBilgisi, TaksitSatiri, TanimlaYanit,
    TaramaAyari, TaramaPlani, TelegramAyari, UcusSecenek, UrunDetayYanit, VeriKaynagi, YanTurCifti,
};
use crate::tarama_alarm as ta;
use crate::tema::TemaTercihi;

// ======================= Kalıcı Ayarlar =======================

fn deserialize_enum_or_default<'de, D, T>(deserializer: D) -> Result<T, D::Error>
where
    D: serde::Deserializer<'de>,
    T: serde::Deserialize<'de> + Default,
{
    let v = serde_json::Value::deserialize(deserializer)?;
    Ok(T::deserialize(v).unwrap_or_default())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct SiteDurumFiltresi {
    pub yesil: bool,
    pub sari: bool,
    pub kirmizi: bool,
    pub kapali: bool,
}

impl Default for SiteDurumFiltresi {
    fn default() -> Self {
        Self {
            yesil: true,
            sari: true,
            kirmizi: true,
            kapali: true,
        }
    }
}

impl SiteDurumFiltresi {
    pub fn hepsi_secili(&self) -> bool {
        self.yesil && self.sari && self.kirmizi && self.kapali
    }

    pub fn tumunu_sec(&mut self) {
        *self = Self::default();
    }

    pub fn durum_acik(&self, durum: SiteDurum) -> bool {
        match durum {
            SiteDurum::Yesil => self.yesil,
            SiteDurum::Sari => self.sari,
            SiteDurum::Kirmizi => self.kirmizi,
            SiteDurum::Kapali => self.kapali,
        }
    }

    /// Çoklu seçim: Tümü açıkken birine tıklanırsa yalnızca o seçilir;
    /// yalnız biri açıkken aynı düğmeye tıklanırsa Tümü açılır; diğer durumlarda geçiş yapılır.
    pub fn secimi_degistir(&mut self, durum: SiteDurum) {
        let secili_sayisi = [self.yesil, self.sari, self.kirmizi, self.kapali]
            .into_iter()
            .filter(|x| *x)
            .count();
        if self.hepsi_secili() {
            *self = Self {
                yesil: durum == SiteDurum::Yesil,
                sari: durum == SiteDurum::Sari,
                kirmizi: durum == SiteDurum::Kirmizi,
                kapali: durum == SiteDurum::Kapali,
            };
        } else if secili_sayisi == 1 && self.durum_acik(durum) {
            self.tumunu_sec();
        } else {
            match durum {
                SiteDurum::Yesil => self.yesil = !self.yesil,
                SiteDurum::Sari => self.sari = !self.sari,
                SiteDurum::Kirmizi => self.kirmizi = !self.kirmizi,
                SiteDurum::Kapali => self.kapali = !self.kapali,
            }
            if !self.yesil && !self.sari && !self.kirmizi && !self.kapali {
                self.tumunu_sec();
            }
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct UygulamaAyarlar {
    pub api_adresi: String,
    // (G1i) eski `yenileme_sn` alanı kaldırıldı: arayuz.json'da varsa yok sayılır, ekran verisi 30 sn'de bir okunur
    pub koyu_tema: bool,
    #[serde(default, deserialize_with = "deserialize_enum_or_default")]
    pub tema_tercihi: TemaTercihi,
    pub yakinlastirma: f32,
    pub site_durum_filtresi: SiteDurumFiltresi,
    #[serde(default, deserialize_with = "deserialize_enum_or_default")]
    pub teklif_siralama: TeklifSiralama,
    pub teklif_siralama_artan: bool,
    #[serde(default, deserialize_with = "deserialize_enum_or_default")]
    pub ucus_siralama: UcusSiralama,
    pub ucus_siralama_artan: bool,
    #[serde(default, deserialize_with = "deserialize_enum_or_default")]
    pub yan_tur_siralama: YanTurSiralama,
    pub yan_tur_siralama_artan: bool,
    #[serde(default, deserialize_with = "deserialize_enum_or_default")]
    pub pano_siralama: PanoSiralama,
    pub pano_siralama_artan: bool,
    #[serde(default, deserialize_with = "deserialize_enum_or_default")]
    pub site_siralama: SiteSiralama,
    pub site_siralama_artan: bool,
}

impl Default for UygulamaAyarlar {
    fn default() -> Self {
        Self {
            api_adresi: "http://127.0.0.1:8770".to_string(),
            koyu_tema: true,
            tema_tercihi: TemaTercihi::Sistem,
            yakinlastirma: 1.0,
            site_durum_filtresi: SiteDurumFiltresi::default(),
            teklif_siralama: TeklifSiralama::SepetFiyati,
            teklif_siralama_artan: true,
            ucus_siralama: UcusSiralama::Fiyat,
            ucus_siralama_artan: true,
            yan_tur_siralama: YanTurSiralama::Fiyat,
            yan_tur_siralama_artan: true,
            pano_siralama: PanoSiralama::UrunAdi,
            pano_siralama_artan: true,
            site_siralama: SiteSiralama::SiteAdi,
            site_siralama_artan: true,
        }
    }
}

impl UygulamaAyarlar {
    pub fn dosya_yolu() -> PathBuf {
        if let Ok(xdg) = env::var("XDG_CONFIG_HOME") {
            if !xdg.trim().is_empty() {
                return PathBuf::from(xdg).join("kazi").join("arayuz.json");
            }
        }
        if let Ok(home) = env::var("HOME") {
            return PathBuf::from(home)
                .join(".config")
                .join("kazi")
                .join("arayuz.json");
        }
        PathBuf::from("arayuz.json")
    }

    pub fn yukle_yoldan(yol: &Path) -> Self {
        if let Ok(icerik) = fs::read_to_string(yol) {
            if let Ok(mut ayarlar) = serde_json::from_str::<Self>(&icerik) {
                ayarlar.yakinlastirma = ayarlar.yakinlastirma.clamp(0.5, 2.5);
                return ayarlar;
            }
        }
        Self::default()
    }

    pub fn yukle() -> Self {
        let yol = Self::dosya_yolu();
        Self::yukle_yoldan(&yol)
    }

    pub fn kaydet_yola(&self, yol: &Path) -> Result<PathBuf, String> {
        if let Some(ust) = yol.parent() {
            if !ust.as_os_str().is_empty() {
                fs::create_dir_all(ust)
                    .map_err(|e| format!("Ayar klasörü oluşturulamadı ({}): {}", ust.display(), e))?;
            }
        }
        let json = serde_json::to_string_pretty(self)
            .map_err(|e| format!("Ayarlar JSON'a çevrilemedi: {}", e))?;
        fs::write(yol, json)
            .map_err(|e| format!("Ayarlar yazılamadı ({}): {}", yol.display(), e))?;
        Ok(yol.to_path_buf())
    }

    pub fn kaydet(&self) -> Result<PathBuf, String> {
        let yol = Self::dosya_yolu();
        self.kaydet_yola(&yol)
    }
}

// ======================= Duyarlı Düzen ve Arama Yardımcıları =======================

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DuyarliDuzen {
    pub pencere_genisligi: f32,
    pub yan_menu_dar: bool,
    pub ikincil_sutunlari_gizle: bool,
    pub yan_menu_genisligi: f32,
}

pub fn duyarli_duzen_hesapla(genislik: f32) -> DuyarliDuzen {
    let yan_menu_dar = genislik < 1100.0;
    let ikincil_sutunlari_gizle = genislik <= 900.0;
    let yan_menu_genisligi = if yan_menu_dar { 56.0 } else { 174.0 };
    DuyarliDuzen {
        pencere_genisligi: genislik,
        yan_menu_dar,
        ikincil_sutunlari_gizle,
        yan_menu_genisligi,
    }
}

fn turkce_arama_katla(metin: &str) -> String {
    plan_model::turkce_katla(metin).replace('\u{307}', "")
}

/// Türkçe İ/ı ve büyük-küçük harf duyarsız arama (birden fazla kelime boşlukla VE mantığında aranır).
pub fn arama_eslesir(sorgu: &str, alanlar: &[&str]) -> bool {
    let temiz = sorgu.trim();
    if temiz.is_empty() {
        return true;
    }
    let birlesik = alanlar
        .iter()
        .map(|s| turkce_arama_katla(s))
        .collect::<Vec<_>>()
        .join(" ");
    turkce_arama_katla(temiz)
        .split_whitespace()
        .all(|parca| birlesik.contains(parca))
}

/// Seçili uçuş ile yan tur tarih/pencere çakışmasını kontrol eder.
pub fn ucus_yan_tur_cakismasi(ucus: &UcusSecenek, yan: &YanTurCifti) -> Option<String> {
    let hedef_tarih = ucus.tarih.trim();
    if hedef_tarih.is_empty() {
        return None;
    }
    let gidis = yan.gidis.tarih.trim();
    let donus = yan.donus.tarih.trim();
    if !gidis.is_empty() && !donus.is_empty() {
        if hedef_tarih >= gidis && hedef_tarih <= donus {
            return Some(format!(
                "Çakışıyor ({} – {} aralığında)",
                bicim::iso_tarih(gidis),
                bicim::iso_tarih(donus)
            ));
        }
    } else if !gidis.is_empty() && gidis == hedef_tarih {
        return Some(format!("Aynı gidiş günü ({})", bicim::iso_tarih(gidis)));
    } else if !donus.is_empty() && donus == hedef_tarih {
        return Some(format!("Aynı dönüş günü ({})", bicim::iso_tarih(donus)));
    }
    None
}

// ======================= Sekmeler ve Sıralama =======================

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sekme {
    TakipListesi,
    UrunDetayi,
    SeyahatPlanla,
    Ucuslar,
    SiteSagligi,
    AraTanimla,
    Ayarlar,
    Kayitlar,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TeklifSiralama {
    SepetFiyati,
    ListeFiyati,
    IndirimYuzdesi,
    PesinTaksit,
    GozlemZamani,
    Site,
    Satici,
    Baslik,
    Stok,
    Katman,
    Teyit,
}

impl Default for TeklifSiralama {
    fn default() -> Self {
        Self::SepetFiyati
    }
}

impl TeklifSiralama {
    pub const ACILIR_SECENEKLER: &'static [(Self, &'static str)] = &[
        (Self::SepetFiyati, "Sepet fiyatı"),
        (Self::ListeFiyati, "Liste fiyatı"),
        (Self::IndirimYuzdesi, "İndirim % (liste→sepet)"),
        (Self::PesinTaksit, "Peşin taksit"),
        (Self::GozlemZamani, "Gözlem zamanı"),
    ];

    pub fn etiket(self) -> &'static str {
        match self {
            Self::SepetFiyati => "Sepet fiyatı",
            Self::ListeFiyati => "Liste fiyatı",
            Self::IndirimYuzdesi => "İndirim % (liste→sepet)",
            Self::PesinTaksit => "Peşin taksit",
            Self::GozlemZamani => "Gözlem zamanı",
            Self::Site => "Site",
            Self::Satici => "Satıcı",
            Self::Baslik => "Başlık",
            Self::Stok => "Stok",
            Self::Katman => "Katman",
            Self::Teyit => "Teyit",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UcusSiralama {
    Fiyat,
    ToplamSure,
    AktarmaSayisi,
    KalkisSaati,
    VarisSaati,
    AktarmaBekleme,
    Tarih,
    Firma,
    ToplamFiyat,
    UyariSayisi,
    Kaynak,
    SonTarama,
}

impl Default for UcusSiralama {
    fn default() -> Self {
        Self::Fiyat
    }
}

impl UcusSiralama {
    pub const ACILIR_SECENEKLER: &'static [(Self, &'static str)] = &[
        (Self::Fiyat, "Fiyat"),
        (Self::ToplamSure, "Toplam süre"),
        (Self::AktarmaSayisi, "Aktarma sayısı"),
        (Self::KalkisSaati, "Kalkış saati"),
        (Self::VarisSaati, "Varış saati"),
        (Self::AktarmaBekleme, "Aktarma bekleme"),
    ];

    pub fn etiket(self) -> &'static str {
        match self {
            Self::Fiyat => "Fiyat",
            Self::ToplamSure => "Toplam süre",
            Self::AktarmaSayisi => "Aktarma sayısı",
            Self::KalkisSaati => "Kalkış saati",
            Self::VarisSaati => "Varış saati",
            Self::AktarmaBekleme => "Aktarma bekleme",
            Self::Tarih => "Tarih",
            Self::Firma => "Firma",
            Self::ToplamFiyat => "Toplam fiyat",
            Self::UyariSayisi => "Uyarı sayısı",
            Self::Kaynak => "Kaynak",
            Self::SonTarama => "Son tarama",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum YanTurSiralama {
    Fiyat,
    ToplamSure,
    Gece,
    Gidis,
    Donus,
}

impl Default for YanTurSiralama {
    fn default() -> Self {
        Self::Fiyat
    }
}

impl YanTurSiralama {
    pub const ACILIR_SECENEKLER: &'static [(Self, &'static str)] = &[
        (Self::Fiyat, "Fiyat"),
        (Self::ToplamSure, "Toplam süre"),
        (Self::Gece, "Gece"),
        (Self::Gidis, "Gidiş saati"),
        (Self::Donus, "Dönüş saati"),
    ];

    pub fn etiket(self) -> &'static str {
        match self {
            Self::Fiyat => "Fiyat",
            Self::ToplamSure => "Toplam süre",
            Self::Gece => "Gece",
            Self::Gidis => "Gidiş saati",
            Self::Donus => "Dönüş saati",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PanoSiralama {
    UrunAdi,
    HedefFiyat,
    EnIyiFiyat,
    SiteSatici,
    PesinTaksit,
    Ortalama,
    FarkYuzde,
    TeklifAdedi,
    SonTarama,
}

impl Default for PanoSiralama {
    fn default() -> Self {
        Self::UrunAdi
    }
}

impl PanoSiralama {
    pub const ACILIR_SECENEKLER: &'static [(Self, &'static str)] = &[
        (Self::EnIyiFiyat, "Sepet fiyatı (en iyi)"),
        (Self::UrunAdi, "Ürün adı"),
        (Self::HedefFiyat, "Hedef fiyat"),
        (Self::SiteSatici, "Site / Satıcı"),
        (Self::PesinTaksit, "Peşin taksit"),
        (Self::Ortalama, "Ortalama fiyat"),
        (Self::FarkYuzde, "Fark %"),
        (Self::TeklifAdedi, "Teklif adedi"),
        (Self::SonTarama, "Son tarama"),
    ];

    pub fn etiket(self) -> &'static str {
        match self {
            Self::EnIyiFiyat => "Sepet fiyatı (en iyi)",
            Self::UrunAdi => "Ürün adı",
            Self::HedefFiyat => "Hedef fiyat",
            Self::SiteSatici => "Site / Satıcı",
            Self::PesinTaksit => "Peşin taksit",
            Self::Ortalama => "Ortalama fiyat",
            Self::FarkYuzde => "Fark %",
            Self::TeklifAdedi => "Teklif adedi",
            Self::SonTarama => "Son tarama",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SiteSiralama {
    Durum,
    SiteAdi,
    Katman,
    SonBasari,
    SonHata,
    Basarili24s,
    Hatali24s,
    Teklif24s,
}

impl Default for SiteSiralama {
    fn default() -> Self {
        Self::SiteAdi
    }
}

impl SiteSiralama {
    pub const ACILIR_SECENEKLER: &'static [(Self, &'static str)] = &[
        (Self::SiteAdi, "Site adı"),
        (Self::Durum, "Durum"),
        (Self::Katman, "Katman"),
        (Self::SonBasari, "Son başarı"),
        (Self::SonHata, "Son hata"),
        (Self::Basarili24s, "24s başarı"),
        (Self::Hatali24s, "24s hata"),
        (Self::Teklif24s, "24s teklif"),
    ];

    pub fn etiket(self) -> &'static str {
        match self {
            Self::SiteAdi => "Site adı",
            Self::Durum => "Durum",
            Self::Katman => "Katman",
            Self::SonBasari => "Son başarı",
            Self::SonHata => "Son hata",
            Self::Basarili24s => "24s başarı",
            Self::Hatali24s => "24s hata",
            Self::Teklif24s => "24s teklif",
        }
    }
}

// ======================= Sıralama Yardımcıları (Birim Test Edilebilir) =======================

fn gecerli_fiyat(f: Option<f64>) -> Option<f64> {
    f.filter(|v| v.is_finite())
}

fn fiyat_ikincil_karsilastir(fa: Option<f64>, fb: Option<f64>) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    match (gecerli_fiyat(fa), gecerli_fiyat(fb)) {
        (Some(a), Some(b)) => a.partial_cmp(&b).unwrap_or(Ordering::Equal),
        (Some(_), None) => Ordering::Less,
        (None, Some(_)) => Ordering::Greater,
        (None, None) => Ordering::Equal,
    }
}

fn karsilastir_opsiyon<K: PartialOrd>(
    ka: Option<K>,
    kb: Option<K>,
    artan: bool,
    fiyat_a: Option<f64>,
    fiyat_b: Option<f64>,
) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    match (ka, kb) {
        (Some(va), Some(vb)) => {
            let cmp = va.partial_cmp(&vb).unwrap_or(Ordering::Equal);
            let yonlu = if artan { cmp } else { cmp.reverse() };
            if yonlu == Ordering::Equal {
                fiyat_ikincil_karsilastir(fiyat_a, fiyat_b)
            } else {
                yonlu
            }
        }
        (Some(_), None) => Ordering::Less,    // None değerler her zaman sona
        (None, Some(_)) => Ordering::Greater, // None değerler her zaman sona
        (None, None) => fiyat_ikincil_karsilastir(fiyat_a, fiyat_b),
    }
}

/// Ürün teklifinde liste→sepet indirim yüzdesini hesaplar.
/// Örn: liste 100.000, sepet 90.000 => Some(10.0).
pub fn teklif_indirim_yuzdesi(t: &DetayTeklif) -> Option<f64> {
    let sepet = gecerli_fiyat(t.sepet_fiyati)?;
    let liste = gecerli_fiyat(t.liste_fiyati).filter(|&v| v > 0.0)?;
    Some((liste - sepet) / liste * 100.0)
}

pub fn teklifleri_sirala(
    teklifler: &[DetayTeklif],
    kriter: TeklifSiralama,
    artan: bool,
) -> Vec<DetayTeklif> {
    let mut sonuc = teklifler.to_vec();
    sonuc.sort_by(|a, b| {
        let fa = a.sepet_fiyati;
        let fb = b.sepet_fiyati;
        match kriter {
            TeklifSiralama::SepetFiyati => {
                karsilastir_opsiyon(gecerli_fiyat(fa), gecerli_fiyat(fb), artan, fa, fb)
            }
            TeklifSiralama::ListeFiyati => karsilastir_opsiyon(
                gecerli_fiyat(a.liste_fiyati),
                gecerli_fiyat(b.liste_fiyati),
                artan,
                fa,
                fb,
            ),
            TeklifSiralama::IndirimYuzdesi => karsilastir_opsiyon(
                teklif_indirim_yuzdesi(a),
                teklif_indirim_yuzdesi(b),
                artan,
                fa,
                fb,
            ),
            TeklifSiralama::PesinTaksit => {
                let ta = a
                    .taksit
                    .as_ref()
                    .and_then(|t| t.pesin_fiyatina)
                    .filter(|&p| p > 0);
                let tb = b
                    .taksit
                    .as_ref()
                    .and_then(|t| t.pesin_fiyatina)
                    .filter(|&p| p > 0);
                karsilastir_opsiyon(ta, tb, artan, fa, fb)
            }
            TeklifSiralama::GozlemZamani => {
                let za = a
                    .gozlem_zamani
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                let zb = b
                    .gozlem_zamani
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                karsilastir_opsiyon(za, zb, artan, fa, fb)
            }
            TeklifSiralama::Site => {
                let sa = (!a.site.trim().is_empty()).then_some(a.site.trim());
                let sb = (!b.site.trim().is_empty()).then_some(b.site.trim());
                karsilastir_opsiyon(sa, sb, artan, fa, fb)
            }
            TeklifSiralama::Satici => {
                let sa = a.satici.as_deref().map(str::trim).filter(|s| !s.is_empty());
                let sb = b.satici.as_deref().map(str::trim).filter(|s| !s.is_empty());
                karsilastir_opsiyon(sa, sb, artan, fa, fb)
            }
            TeklifSiralama::Baslik => {
                let ba = a.baslik.as_deref().map(str::trim).filter(|s| !s.is_empty());
                let bb = b.baslik.as_deref().map(str::trim).filter(|s| !s.is_empty());
                karsilastir_opsiyon(ba, bb, artan, fa, fb)
            }
            TeklifSiralama::Stok => karsilastir_opsiyon(a.stok, b.stok, artan, fa, fb),
            TeklifSiralama::Katman => {
                let ka = a
                    .kaynak_katmani
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                let kb = b
                    .kaynak_katmani
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                karsilastir_opsiyon(ka, kb, artan, fa, fb)
            }
            TeklifSiralama::Teyit => {
                let ta = a
                    .teyit_durumu
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                let tb = b
                    .teyit_durumu
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                karsilastir_opsiyon(ta, tb, artan, fa, fb)
            }
        }
    });
    sonuc
}

fn ucus_saat_anahtari(iso: &str) -> Option<(String, String)> {
    let temiz = iso.trim();
    if temiz.is_empty() || temiz == "?" || temiz == "..." {
        return None;
    }
    Some((bicim::iso_saat(temiz), temiz.to_string()))
}

pub fn ucuslari_sirala(
    secenekler: &[UcusSecenek],
    kriter: UcusSiralama,
    artan: bool,
) -> Vec<UcusSecenek> {
    let mut sonuc = secenekler.to_vec();
    sonuc.sort_by(|a, b| {
        let fa = (a.kisi_basi > 0.0 && a.kisi_basi.is_finite()).then_some(a.kisi_basi);
        let fb = (b.kisi_basi > 0.0 && b.kisi_basi.is_finite()).then_some(b.kisi_basi);
        match kriter {
            UcusSiralama::Fiyat => karsilastir_opsiyon(fa, fb, artan, fa, fb),
            UcusSiralama::ToplamSure => {
                let sa = (a.sure_dk > 0).then_some(a.sure_dk);
                let sb = (b.sure_dk > 0).then_some(b.sure_dk);
                karsilastir_opsiyon(sa, sb, artan, fa, fb)
            }
            UcusSiralama::AktarmaSayisi => {
                let aa = Some(a.aktarma_sayisi.max(a.aktarmalar.len() as u32));
                let ab = Some(b.aktarma_sayisi.max(b.aktarmalar.len() as u32));
                karsilastir_opsiyon(aa, ab, artan, fa, fb)
            }
            UcusSiralama::KalkisSaati => karsilastir_opsiyon(
                ucus_saat_anahtari(&a.kalkis),
                ucus_saat_anahtari(&b.kalkis),
                artan,
                fa,
                fb,
            ),
            UcusSiralama::VarisSaati => karsilastir_opsiyon(
                ucus_saat_anahtari(&a.varis),
                ucus_saat_anahtari(&b.varis),
                artan,
                fa,
                fb,
            ),
            UcusSiralama::AktarmaBekleme => {
                let ba: Option<i64> = a
                    .aktarma_bekleme_dk
                    .as_ref()
                    .filter(|v| !v.is_empty())
                    .map(|v| v.iter().sum());
                let bb: Option<i64> = b
                    .aktarma_bekleme_dk
                    .as_ref()
                    .filter(|v| !v.is_empty())
                    .map(|v| v.iter().sum());
                karsilastir_opsiyon(ba, bb, artan, fa, fb)
            }
            UcusSiralama::Tarih => {
                let ta = (!a.tarih.trim().is_empty()).then_some(a.tarih.trim());
                let tb = (!b.tarih.trim().is_empty()).then_some(b.tarih.trim());
                karsilastir_opsiyon(ta, tb, artan, fa, fb)
            }
            UcusSiralama::Firma => {
                let f_a = (!a.firma.trim().is_empty()).then_some(a.firma.trim());
                let f_b = (!b.firma.trim().is_empty()).then_some(b.firma.trim());
                karsilastir_opsiyon(f_a, f_b, artan, fa, fb)
            }
            UcusSiralama::ToplamFiyat => {
                let ta = (a.toplam > 0.0 && a.toplam.is_finite()).then_some(a.toplam);
                let tb = (b.toplam > 0.0 && b.toplam.is_finite()).then_some(b.toplam);
                karsilastir_opsiyon(ta, tb, artan, fa, fb)
            }
            UcusSiralama::UyariSayisi => {
                karsilastir_opsiyon(Some(a.uyarilar.len()), Some(b.uyarilar.len()), artan, fa, fb)
            }
            UcusSiralama::Kaynak => {
                let ka = (!a.kaynak.trim().is_empty()).then_some(a.kaynak.trim());
                let kb = (!b.kaynak.trim().is_empty()).then_some(b.kaynak.trim());
                karsilastir_opsiyon(ka, kb, artan, fa, fb)
            }
            UcusSiralama::SonTarama => {
                karsilastir_opsiyon(a.gozlem_zamani.as_deref(), b.gozlem_zamani.as_deref(), artan, fa, fb)
            }
        }
    });
    sonuc
}

pub fn yan_turlari_sirala(
    ciftler: &[YanTurCifti],
    kriter: YanTurSiralama,
    artan: bool,
) -> Vec<YanTurCifti> {
    let mut sonuc = ciftler.to_vec();
    sonuc.sort_by(|a, b| {
        let fa = (a.kisi_basi_toplam > 0.0 && a.kisi_basi_toplam.is_finite())
            .then_some(a.kisi_basi_toplam);
        let fb = (b.kisi_basi_toplam > 0.0 && b.kisi_basi_toplam.is_finite())
            .then_some(b.kisi_basi_toplam);
        match kriter {
            YanTurSiralama::Fiyat => karsilastir_opsiyon(fa, fb, artan, fa, fb),
            YanTurSiralama::ToplamSure => {
                let sa = (a.toplam_sure_dk > 0).then_some(a.toplam_sure_dk);
                let sb = (b.toplam_sure_dk > 0).then_some(b.toplam_sure_dk);
                karsilastir_opsiyon(sa, sb, artan, fa, fb)
            }
            YanTurSiralama::Gece => {
                let ga = (a.gece > 0).then_some(a.gece);
                let gb = (b.gece > 0).then_some(b.gece);
                karsilastir_opsiyon(ga, gb, artan, fa, fb)
            }
            YanTurSiralama::Gidis => karsilastir_opsiyon(
                ucus_saat_anahtari(&a.gidis.kalkis),
                ucus_saat_anahtari(&b.gidis.kalkis),
                artan,
                fa,
                fb,
            ),
            YanTurSiralama::Donus => karsilastir_opsiyon(
                ucus_saat_anahtari(&a.donus.kalkis),
                ucus_saat_anahtari(&b.donus.kalkis),
                artan,
                fa,
                fb,
            ),
        }
    });
    sonuc
}

// ======================= Taksit ve Vade Farkı Yardımcıları =======================

/// Taksit bilgisinin tek hücrelik özet metnini üretir:
/// örn. `6× peşin` / `12× %8,5 vade` / `—`.
pub fn taksit_ozeti(taksit: &TaksitBilgisi) -> String {
    let tablo_pesin = taksit
        .tablo
        .iter()
        .filter(|s| {
            s.taksit_sayisi > 0
                && s.vade_farki_yuzde
                    .is_some_and(|v| v.is_finite() && v <= 0.000_1)
        })
        .map(|s| s.taksit_sayisi)
        .max();
    let en_yuksek_pesin = taksit
        .pesin_fiyatina
        .filter(|&p| p > 0)
        .into_iter()
        .chain(tablo_pesin)
        .max();
    if let Some(p) = en_yuksek_pesin {
        return format!("{}× peşin", p);
    }

    let mut sirali = taksit.tablo.clone();
    taksit_satirlarini_sirala(&mut sirali);
    for satir in sirali {
        if satir.taksit_sayisi > 0 {
            if let Some(vade) = satir.vade_farki_yuzde.filter(|v| v.is_finite() && *v > 0.000_1) {
                return format!(
                    "{}× {} vade",
                    satir.taksit_sayisi,
                    bicim::yuzde_isaretsiz(vade)
                );
            }
        }
    }

    "—".to_string()
}

/// Bir taksit satırının sepet fiyatına göre TL cinsinden vade farkını hesaplar (`toplam − sepet_fiyati`).
pub fn taksit_vade_tl_farki(satir: &TaksitSatiri, sepet_fiyati: Option<f64>) -> Option<f64> {
    let sepet = gecerli_fiyat(sepet_fiyati)?;
    let toplam = gecerli_fiyat(satir.toplam)?;
    Some(toplam - sepet)
}

fn banka_adi_sirala(a: &TaksitSatiri, b: &TaksitSatiri) -> std::cmp::Ordering {
    let ba = a.banka.as_deref().unwrap_or("").trim().to_lowercase();
    let bb = b.banka.as_deref().unwrap_or("").trim().to_lowercase();
    ba.cmp(&bb)
}

fn taksit_satir_karsilastir(a: &TaksitSatiri, b: &TaksitSatiri) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    let grup = |s: &TaksitSatiri| -> u8 {
        match s.vade_farki_yuzde.filter(|v| v.is_finite()) {
            Some(v) if v <= 0.000_1 => 0,
            Some(_) => 1,
            None => 2,
        }
    };
    let ga = grup(a);
    let gb = grup(b);
    if ga != gb {
        return ga.cmp(&gb);
    }
    match ga {
        0 => b
            .taksit_sayisi
            .cmp(&a.taksit_sayisi)
            .then_with(|| banka_adi_sirala(a, b)),
        1 => {
            let va = a.vade_farki_yuzde.unwrap_or(f64::INFINITY);
            let vb = b.vade_farki_yuzde.unwrap_or(f64::INFINITY);
            va.partial_cmp(&vb)
                .unwrap_or(Ordering::Equal)
                .then_with(|| b.taksit_sayisi.cmp(&a.taksit_sayisi))
                .then_with(|| banka_adi_sirala(a, b))
        }
        _ => b
            .taksit_sayisi
            .cmp(&a.taksit_sayisi)
            .then_with(|| banka_adi_sirala(a, b)),
    }
}

/// Önce vade farksızlar (taksit sayısı çoktan aza), sonra vade farklılar (fark azdan çoğa).
pub fn taksit_satirlarini_sirala(satirlar: &mut Vec<TaksitSatiri>) {
    satirlar.sort_by(taksit_satir_karsilastir);
}

/// Taksit satırlarını sıralı biçimde bankaya göre gruplar (her bankanın satırları alt alta).
pub fn taksit_satirlarini_bankaya_gore_grupla(
    satirlar: &[TaksitSatiri],
) -> Vec<(String, Vec<TaksitSatiri>)> {
    let mut sirali = satirlar.to_vec();
    taksit_satirlarini_sirala(&mut sirali);

    let mut gruplar: Vec<(String, Vec<TaksitSatiri>)> = Vec::new();
    for satir in sirali {
        let banka_ad = satir
            .banka
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or("Diğer")
            .to_string();
        if let Some((_, liste)) = gruplar
            .iter_mut()
            .find(|(ad, _)| ad.eq_ignore_ascii_case(&banka_ad))
        {
            liste.push(satir);
        } else {
            gruplar.push((banka_ad, vec![satir]));
        }
    }
    gruplar
}

pub fn pano_urunleri_sirala(
    urunler: &[PanoUrun],
    kriter: PanoSiralama,
    artan: bool,
) -> Vec<PanoUrun> {
    let mut sonuc = urunler.to_vec();
    sonuc.sort_by(|a, b| {
        let fa = a.en_iyi.as_ref().and_then(|e| e.sepet_fiyati);
        let fb = b.en_iyi.as_ref().and_then(|e| e.sepet_fiyati);
        match kriter {
            PanoSiralama::UrunAdi => {
                let ua = (!a.ad.trim().is_empty()).then_some(a.ad.trim());
                let ub = (!b.ad.trim().is_empty()).then_some(b.ad.trim());
                karsilastir_opsiyon(ua, ub, artan, fa, fb)
            }
            PanoSiralama::HedefFiyat => karsilastir_opsiyon(
                gecerli_fiyat(a.hedef_fiyat),
                gecerli_fiyat(b.hedef_fiyat),
                artan,
                fa,
                fb,
            ),
            PanoSiralama::EnIyiFiyat => {
                karsilastir_opsiyon(gecerli_fiyat(fa), gecerli_fiyat(fb), artan, fa, fb)
            }
            PanoSiralama::SiteSatici => {
                let sa = a.en_iyi.as_ref().and_then(|e| {
                    (!e.site.trim().is_empty()).then(|| match &e.satici {
                        Some(s) if !s.is_empty() => format!("{} / {}", e.site, s),
                        _ => e.site.clone(),
                    })
                });
                let sb = b.en_iyi.as_ref().and_then(|e| {
                    (!e.site.trim().is_empty()).then(|| match &e.satici {
                        Some(s) if !s.is_empty() => format!("{} / {}", e.site, s),
                        _ => e.site.clone(),
                    })
                });
                karsilastir_opsiyon(sa, sb, artan, fa, fb)
            }
            PanoSiralama::PesinTaksit => {
                let ta = a
                    .en_iyi
                    .as_ref()
                    .and_then(|e| e.taksit.as_ref())
                    .and_then(|t| t.pesin_fiyatina)
                    .filter(|&p| p > 0);
                let tb = b
                    .en_iyi
                    .as_ref()
                    .and_then(|e| e.taksit.as_ref())
                    .and_then(|t| t.pesin_fiyatina)
                    .filter(|&p| p > 0);
                karsilastir_opsiyon(ta, tb, artan, fa, fb)
            }
            PanoSiralama::Ortalama => karsilastir_opsiyon(
                gecerli_fiyat(a.ozet.ortalama),
                gecerli_fiyat(b.ozet.ortalama),
                artan,
                fa,
                fb,
            ),
            PanoSiralama::FarkYuzde => {
                let fark_hesapla = |u: &PanoUrun| -> Option<f64> {
                    let sepet = gecerli_fiyat(u.en_iyi.as_ref().and_then(|e| e.sepet_fiyati))?;
                    let ort = gecerli_fiyat(u.ozet.ortalama).filter(|&v| v > 0.0)?;
                    Some((sepet - ort) / ort * 100.0)
                };
                karsilastir_opsiyon(fark_hesapla(a), fark_hesapla(b), artan, fa, fb)
            }
            PanoSiralama::TeklifAdedi => {
                let aa = (a.ozet.adet > 0).then_some(a.ozet.adet);
                let ab = (b.ozet.adet > 0).then_some(b.ozet.adet);
                karsilastir_opsiyon(aa, ab, artan, fa, fb)
            }
            PanoSiralama::SonTarama => {
                let ta = a
                    .son_tarama
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                let tb = b
                    .son_tarama
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                karsilastir_opsiyon(ta, tb, artan, fa, fb)
            }
        }
    });
    sonuc
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SiteDurum {
    Yesil,
    Sari,
    Kirmizi,
    Kapali,
}

impl SiteDurum {
    pub fn oncelik(self) -> u8 {
        match self {
            Self::Yesil => 1,
            Self::Sari => 2,
            Self::Kirmizi => 3,
            Self::Kapali => 4,
        }
    }

    pub fn etiket(self) -> &'static str {
        match self {
            Self::Yesil => "● Yeşil",
            Self::Sari => "● Sarı",
            Self::Kirmizi => "● Kırmızı",
            Self::Kapali => "⊘ Kapalı",
        }
    }

    pub fn renk(self, visuals: &egui::Visuals) -> Color32 {
        let p = tema::palet(visuals);
        match self {
            Self::Yesil => p.basari,
            Self::Sari => p.uyari,
            Self::Kirmizi => p.hata,
            Self::Kapali => p.kapali,
        }
    }
}

/// Site sağlığı durum sınıflandırması — rozet, sıralama, sayaç ve süzgecin tek kaynağı.
/// Kural sırası:
/// 1. `hata_turu` "desteklenmiyor:" ile başlıyorsa → `SiteDurum::Kapali` (gri ⊘ Kapalı)
/// 2. `!etkin || (basarili == 0 && hatali > 0) || katman_tercihi == "K3"` → `SiteDurum::Kirmizi`
/// 3. `hatali > 0 || son_hata.is_some()` → `SiteDurum::Sari`
/// 4. Aksi halde → `SiteDurum::Yesil`
pub fn site_durumu(site: &SiteSaglik) -> SiteDurum {
    if site
        .hata_turu
        .as_deref()
        .is_some_and(|h| h.trim().starts_with("desteklenmiyor:"))
    {
        return SiteDurum::Kapali;
    }
    let basarili = site.son_24s.as_ref().map(|s| s.basarili).unwrap_or(0);
    let hatali = site.son_24s.as_ref().map(|s| s.hatali).unwrap_or(0);
    if !site.etkin || (basarili == 0 && hatali > 0) || site.katman_tercihi.as_deref() == Some("K3")
    {
        SiteDurum::Kirmizi
    } else if hatali > 0 || site.son_hata.is_some() {
        SiteDurum::Sari
    } else {
        SiteDurum::Yesil
    }
}

fn site_durum_onceligi(site: &SiteSaglik) -> u8 {
    site_durumu(site).oncelik()
}

pub fn siteleri_sirala(
    siteler: &[SiteSaglik],
    kriter: SiteSiralama,
    artan: bool,
) -> Vec<SiteSaglik> {
    let mut sonuc = siteler.to_vec();
    sonuc.sort_by(|a, b| {
        let ikincil_a = Some(a.son_24s.as_ref().map(|s| s.teklif).unwrap_or(0) as f64);
        let ikincil_b = Some(b.son_24s.as_ref().map(|s| s.teklif).unwrap_or(0) as f64);
        match kriter {
            SiteSiralama::Durum => karsilastir_opsiyon(
                Some(site_durum_onceligi(a)),
                Some(site_durum_onceligi(b)),
                artan,
                ikincil_a,
                ikincil_b,
            ),
            SiteSiralama::SiteAdi => {
                let sa = (!a.ad.trim().is_empty()).then_some(a.ad.trim());
                let sb = (!b.ad.trim().is_empty()).then_some(b.ad.trim());
                karsilastir_opsiyon(sa, sb, artan, ikincil_a, ikincil_b)
            }
            SiteSiralama::Katman => {
                let ka = a
                    .katman_tercihi
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                let kb = b
                    .katman_tercihi
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                karsilastir_opsiyon(ka, kb, artan, ikincil_a, ikincil_b)
            }
            SiteSiralama::SonBasari => {
                let ba = a
                    .son_basari
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                let bb = b
                    .son_basari
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                karsilastir_opsiyon(ba, bb, artan, ikincil_a, ikincil_b)
            }
            SiteSiralama::SonHata => {
                let ha = a
                    .son_hata
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                let hb = b
                    .son_hata
                    .as_deref()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && *s != "?");
                karsilastir_opsiyon(ha, hb, artan, ikincil_a, ikincil_b)
            }
            SiteSiralama::Basarili24s => {
                let sa = a.son_24s.as_ref().map(|s| s.basarili);
                let sb = b.son_24s.as_ref().map(|s| s.basarili);
                karsilastir_opsiyon(sa, sb, artan, ikincil_a, ikincil_b)
            }
            SiteSiralama::Hatali24s => {
                let ha = a.son_24s.as_ref().map(|s| s.hatali);
                let hb = b.son_24s.as_ref().map(|s| s.hatali);
                karsilastir_opsiyon(ha, hb, artan, ikincil_a, ikincil_b)
            }
            SiteSiralama::Teklif24s => {
                let ta = a.son_24s.as_ref().map(|s| s.teklif);
                let tb = b.son_24s.as_ref().map(|s| s.teklif);
                karsilastir_opsiyon(ta, tb, artan, ikincil_a, ikincil_b)
            }
        }
    });
    sonuc
}

// ======================= Arka Plan Mesajları =======================

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ToastTuru {
    Bilgi,
    Basari,
    Hata,
}

#[derive(Debug, Clone)]
pub struct ToastBildirim {
    pub metin: String,
    pub tur: ToastTuru,
    pub olusturulma: Instant,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TakipOzetFiltre {
    Tumu,
    Toplamda,
    AlarmAltinda,
    TeyitBekleyen,
}

pub enum ArkaPlanMesaji {
    GenelYenileme {
        pano: Result<PanoYanit, String>,
        siteler: Result<Vec<SiteSaglik>, String>,
        seyahat: Result<SeyahatYanit, String>,
        detay: Option<Result<UrunDetayYanit, String>>,
        zaman_etiketi: String,
    },
    UrunDetaySonucu(Result<UrunDetayYanit, String>),
    TanimlaSonucu(Result<TanimlaYanit, String>),
    OnaylaSonucu(Result<String, String>),
    TaramaSonucu(Result<String, String>),
    /// PUT /toplam yanıtı (Takip Listesi kutucukları)
    ToplamSonucu(Result<SistemToplami, String>),
    /// GET/PUT /ayarlar/tarama yanıtı (Brave göster/gizle)
    BraveAyari(Result<TaramaAyari, String>),
    /// PUT /ayarlar/tarama (gizlilik/hız anahtarları)
    GizlilikAyari(Result<TaramaAyari, String>),
    /// WS /olaylar bağlantı durumu ve gelen canlı olaylar
    WsDurum(bool),
    WsOlay(OlayMesaji),
    /// GET/PUT /tarama/plan (None = zamanlayıcı henüz yok, 404)
    TaramaPlani(Result<Option<TaramaPlani>, String>),
    /// POST /tarama/sifirla (tumu = "Tümünü sıfırla" ile mi basıldı)
    SayacSifir {
        tumu: bool,
        sonuc: Result<crate::api::TaramaSifirYanit, String>,
    },
    /// POST /tarama/kategori/{id}/simdi
    ElleTarama {
        kategori_id: String,
        kategori_ad: String,
        sonuc: Result<ElleTaramaYanit, String>,
    },
    /// POST /takip/cikar (cikar=true) ya da /takip/geri_al
    TakipIslem {
        cikar: bool,
        adlar: Vec<String>,
        sonuc: Result<PanoYanit, String>,
    },
    /// PUT /siteler/{ad}/etkin (Ayarlar → Aranan siteler)
    SiteEtkin(Result<SiteSaglik, String>),
    /// PUT /urunler/{id}/alarm
    AlarmSonucu {
        urun_ad: String,
        sonuc: Result<AlarmYanit, String>,
    },
    /// GET/PUT /ayarlar/telegram (kaydedildi = PUT yanıtı)
    TelegramAyar {
        kaydedildi: bool,
        sonuc: Result<TelegramAyari, String>,
    },
    /// POST /ayarlar/telegram/test (rapor=false) ya da /rapor
    TelegramGonderim {
        rapor: bool,
        sonuc: Result<bool, String>,
    },
}

/// Satır içi alarm fiyatı düzenleyicisinin nerede açık olduğu
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AlarmDuzenlemeYeri {
    TakipSatiri,
    Pencere,
    Detay,
}

#[derive(Debug, Clone)]
pub struct AlarmDuzenleme {
    pub urun_id: String,
    pub urun_ad: String,
    pub metin: String,
    pub yer: AlarmDuzenlemeYeri,
    pub odak_istendi: bool,
    pub hata: Option<String>,
}

// ======================= Ana Uygulama Durumu =======================

#[derive(Debug, Clone)]
struct AcikTaksitPenceresi {
    urun_ad: String,
    site: String,
    satici: Option<String>,
    sepet_fiyati: Option<f64>,
    taksit: TaksitBilgisi,
    url: Option<String>,
    gozlem_zamani: Option<String>,
}

impl AcikTaksitPenceresi {
    fn from_pano_urun(urun: &PanoUrun) -> Option<Self> {
        let en_iyi = urun.en_iyi.as_ref()?;
        Some(Self {
            urun_ad: urun.ad.clone(),
            site: en_iyi.site.clone(),
            satici: en_iyi.satici.clone(),
            sepet_fiyati: en_iyi.sepet_fiyati,
            taksit: en_iyi.taksit.clone().unwrap_or_default(),
            url: en_iyi.url.clone(),
            gozlem_zamani: en_iyi.gozlem_zamani.clone(),
        })
    }

    fn from_detay_teklif(urun_ad: &str, teklif: &DetayTeklif) -> Self {
        Self {
            urun_ad: urun_ad.to_string(),
            site: teklif.site.clone(),
            satici: teklif.satici.clone(),
            sepet_fiyati: teklif.sepet_fiyati,
            taksit: teklif.taksit.clone().unwrap_or_default(),
            url: teklif.url.clone(),
            gozlem_zamani: teklif.gozlem_zamani.clone(),
        }
    }
}

pub struct KaziArayuzApp {
    ayarlar: UygulamaAyarlar,
    veri_kaynagi: VeriKaynagi,
    aktif_sekme: Sekme,
    kayit_ekrani: kayitlar::KayitEkrani,
    tarayici_paneli: tarayici_paneli::TarayiciPaneli,

    // Geliştirici: --goruntu <dosya.ppm> → açılıştan N sn sonra pencere görüntüsünü kaydet ve kapan
    goruntu_hedefi: Option<(PathBuf, Duration)>,
    goruntu_istendi: bool,
    goruntu_tx: Sender<Arc<ColorImage>>,
    goruntu_rx: Receiver<Arc<ColorImage>>,
    acilis_ani: Instant,
    baslangic_taksit_ac: Option<String>,
    /// Geliştirici: `--kaydir <px>` → Ayarlar ekranı ilk karede bu kadar aşağı kaydırılır (görüntü denetimi için)
    baslangic_kaydir: Option<f32>,
    acik_taksit_penceresi: Option<AcikTaksitPenceresi>,

    // Bağlantı, WS canlı olaylar ve yenileme durumu
    bagli: bool,
    ws_bagli: bool,
    ws_hedef_url: Arc<Mutex<(u64, String)>>,
    yukleniyor: bool,
    detay_yukleniyor: bool,
    son_hata: Option<String>,
    son_yenileme_zamani: String,
    son_yenileme_ani: Instant,
    son_ws_yenileme: Instant,
    toastlar: Vec<ToastBildirim>,
    /// Brave penceresi görünür mü (None = henüz bilinmiyor); istek sürerken true
    brave_gorunur: Option<bool>,
    /// Ayarlar → Tarayıcı gizliliği ve hız (GET/PUT /ayarlar/tarama)
    gizlilik: Option<TaramaAyari>,
    gizlilik_bekliyor: bool,
    brave_bekliyor: bool,
    brave_ipucu: String,
    tx: Sender<ArkaPlanMesaji>,
    rx: Receiver<ArkaPlanMesaji>,

    // Veri önbelleği (çekirdek kapansa bile son veriyi korur)
    pano: Option<PanoYanit>,
    secili_urun_id: Option<String>,
    urun_detay: Option<UrunDetayYanit>,
    secili_teklif_id: Option<i64>,
    siteler: Vec<SiteSaglik>,
    seyahat: Option<SeyahatYanit>,
    secili_ucus_anahtari: Option<String>,
    secili_ucus_bilgisi: Option<UcusSecenek>,

    // Tablo arama ve özet kartı süzgeçleri
    takip_arama: String,
    detay_arama: String,
    ucus_arama: String,
    site_arama: String,
    arama_odak_iste: bool,
    takip_ozet_filtre: TakipOzetFiltre,

    // Ürün detayı süzgeç ve sıralama durumu
    suzgec_yalniz_teyitli: bool,
    suzgec_yalniz_stokta: bool,
    suzgec_site: String,
    teklif_siralama: TeklifSiralama,
    siralama_artan: bool,

    // Ara / Tanımla ekranı durumu
    tanimla_metin: String,
    tanimla_tur: String,
    tanimla_yukleniyor: bool,
    tanimla_sonuc: Option<TanimlaYanit>,
    reddedilen_adaylar: HashSet<String>,
    tanimla_bilgi_mesaji: Option<String>,

    // Ayarlar ekranı geri bildirimi
    ayarlar_mesaji: Option<String>,

    // Seyahat Planla ekranı durumu
    plan_durumu: plan_ekrani::PlanEkraniDurumu,

    // G1i: kategori bazlı tarama planı, elle tarama kilidi, takipten çıkarma, alarm, Telegram
    tarama_plani: Option<TaramaPlani>,
    /// 15.11: Ayarlar → Hızlı HTTP turu kartı (kendi kanalıyla okur)
    http_turu: http_turu::HttpTuruKarti,
    /// Ayarlar'daki tarama sekmesi: 0 = Tarama sıklığı (tarayıcılı), 1 = Hızlı HTTP turu
    tarama_ayar_sekmesi: u8,
    /// GET /tarama/plan 404 → zamanlayıcı henüz yok
    tarama_plani_yok: bool,
    tarama_plani_ani: Instant,
    tarama_plani_bekliyor: bool,
    sayac_sifir_bekliyor: bool,
    son_plan_istegi: Instant,
    elle_kilitler: ta::ElleKilitler,
    elle_tarama_bekliyor: Option<String>,
    /// Takip Listesi'nde kullanıcının TIKLADIĞI satır (açılıştaki otomatik seçim sayılmaz)
    takip_tiklanan_urun: Option<String>,
    /// onay bekleyen takipten çıkarma: (id, ad)
    takip_cikar_onayi: Option<Vec<(String, String)>>,
    takip_islem_bekliyor: bool,
    /// Ayarlar → Aranan siteler: yanıtı beklenen site (aynı anda tek istek)
    site_islem_bekliyor: Option<String>,
    alarm_duzenleme: Option<AlarmDuzenleme>,
    telegram_formu: ta::TelegramFormu,
}

impl KaziArayuzApp {
    /// Gelen detay yanıtı şu an seçili ürüne mi ait? (geç gelen eski yanıtlar yok sayılır)
    fn detay_guncel_mi(&self, d: &UrunDetayYanit) -> bool {
        self.secili_urun_id.as_deref().map_or(true, |id| id == d.urun.id)
    }

    /// `--goruntu`: belirtilen süre dolunca ekran görüntüsü iste; gelince PPM (P6) olarak yaz ve pencereyi kapat.
    fn goruntu_isle(&mut self, ctx: &egui::Context) {
        let Some((yol, sure)) = self.goruntu_hedefi.clone() else { return };
        if !self.goruntu_istendi && self.acilis_ani.elapsed() >= sure {
            self.goruntu_istendi = true;
            let tx = self.goruntu_tx.clone();
            let ctx_clone = ctx.clone();
            ctx.send_viewport_cmd(egui::ViewportCommand::Screenshot(
                egui::ScreenshotCallback::new(move |img| {
                    let _ = tx.send(img);
                    ctx_clone.request_repaint();
                }),
            ));
        }
        if let Ok(img) = self.goruntu_rx.try_recv() {
            let [g, y] = img.size;
            let mut veri = format!("P6\n{g} {y}\n255\n").into_bytes();
            for p in &img.pixels {
                veri.extend_from_slice(&[p.r(), p.g(), p.b()]);
            }
            if let Err(e) = std::fs::write(&yol, veri) {
                eprintln!("görüntü yazılamadı: {e}");
            } else {
                println!("görüntü: {}", yol.display());
            }
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
    }

    pub fn new(
        cc: &eframe::CreationContext<'_>,
        ayarlar: UygulamaAyarlar,
        veri_kaynagi: VeriKaynagi,
    ) -> Self {
        yazi_tiplerini_yukle(&cc.egui_ctx);
        tema::tema_uygula(&cc.egui_ctx, ayarlar.tema_tercihi);
        cc.egui_ctx
            .set_zoom_factor(ayarlar.yakinlastirma.clamp(0.5, 2.5));

        let teklif_siralama = ayarlar.teklif_siralama;
        let siralama_artan = ayarlar.teklif_siralama_artan;
        let ws_url_baslangic = match &veri_kaynagi {
            VeriKaynagi::Http(api) => ws_olaylar_url(api),
            VeriKaynagi::Sahte(_) => String::new(),
        };
        let ws_hedef_url = Arc::new(Mutex::new((1u64, ws_url_baslangic)));
        let (tx, rx) = mpsc::channel();
        let (goruntu_tx, goruntu_rx) = mpsc::channel();
        let mut app = Self {
            ayarlar,
            veri_kaynagi,
            aktif_sekme: Sekme::TakipListesi,
            kayit_ekrani: kayitlar::KayitEkrani::default(),
            tarayici_paneli: Default::default(),
            goruntu_hedefi: None,
            goruntu_istendi: false,
            goruntu_tx,
            goruntu_rx,
            acilis_ani: Instant::now(),
            baslangic_taksit_ac: None,
            baslangic_kaydir: None,
            acik_taksit_penceresi: None,
            bagli: false,
            ws_bagli: true,
            ws_hedef_url,
            yukleniyor: false,
            detay_yukleniyor: false,
            son_hata: None,
            son_yenileme_zamani: "Henüz yenilenmedi".to_string(),
            son_yenileme_ani: Instant::now(),
            son_ws_yenileme: Instant::now() - Duration::from_secs(10),
            toastlar: Vec::new(),
            brave_gorunur: None,
            gizlilik: None,
            gizlilik_bekliyor: false,
            brave_bekliyor: false,
            brave_ipucu: String::new(),
            tx,
            rx,
            pano: None,
            secili_urun_id: None,
            urun_detay: None,
            secili_teklif_id: None,
            siteler: Vec::new(),
            seyahat: None,
            secili_ucus_anahtari: None,
            secili_ucus_bilgisi: None,
            takip_arama: String::new(),
            detay_arama: String::new(),
            ucus_arama: String::new(),
            site_arama: String::new(),
            arama_odak_iste: false,
            takip_ozet_filtre: TakipOzetFiltre::Tumu,
            suzgec_yalniz_teyitli: false,
            suzgec_yalniz_stokta: false,
            suzgec_site: "Tümü".to_string(),
            teklif_siralama,
            siralama_artan,
            tanimla_metin: String::new(),
            tanimla_tur: "urun".to_string(),
            tanimla_yukleniyor: false,
            tanimla_sonuc: None,
            reddedilen_adaylar: HashSet::new(),
            tanimla_bilgi_mesaji: None,
            ayarlar_mesaji: None,
            plan_durumu: plan_ekrani::PlanEkraniDurumu::default(),
            tarama_plani: None,
            http_turu: http_turu::HttpTuruKarti::default(),
            tarama_ayar_sekmesi: 0,
            tarama_plani_yok: false,
            tarama_plani_ani: Instant::now(),
            tarama_plani_bekliyor: false,
            sayac_sifir_bekliyor: false,
            son_plan_istegi: Instant::now(),
            elle_kilitler: ta::ElleKilitler::default(),
            elle_tarama_bekliyor: None,
            takip_tiklanan_urun: None,
            takip_cikar_onayi: None,
            takip_islem_bekliyor: false,
            site_islem_bekliyor: None,
            alarm_duzenleme: None,
            telegram_formu: ta::TelegramFormu::default(),
        };

        // Açılışta ilk yenilemeyi ve WS /olaylar dinleyicisini başlat
        app.genel_yenileme_baslat(&cc.egui_ctx);
        app.brave_ayari_istek(&cc.egui_ctx, None);
        app.tarama_plani_iste(&cc.egui_ctx, None);
        if matches!(app.veri_kaynagi, VeriKaynagi::Http(_)) {
            app.ws_bagli = false;
            ws_dinleyici_baslat(
                Arc::clone(&app.ws_hedef_url),
                app.tx.clone(),
                cc.egui_ctx.clone(),
            );
        }
        app
    }

    fn toast_ekle(&mut self, tur: ToastTuru, metin: impl Into<String>) {
        self.toastlar.push(ToastBildirim {
            metin: metin.into(),
            tur,
            olusturulma: Instant::now(),
        });
        if self.toastlar.len() > 5 {
            let fazla = self.toastlar.len() - 5;
            self.toastlar.drain(0..fazla);
        }
    }

    fn baglanti_tam_mi(&self) -> bool {
        match &self.veri_kaynagi {
            VeriKaynagi::Http(_) => self.bagli && self.ws_bagli,
            VeriKaynagi::Sahte(_) => self.bagli,
        }
    }

    /// `gorunur`: None → yalnız oku (GET), Some(x) → değiştir (PUT).
    fn brave_ayari_istek(&mut self, ctx: &egui::Context, gorunur: Option<bool>) {
        self.brave_bekliyor = true;
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let sonuc = match gorunur {
                None => kaynak.tarama_ayari_getir(),
                Some(g) => kaynak.brave_gorunur_yap(g),
            };
            let _ = tx.send(ArkaPlanMesaji::BraveAyari(sonuc));
            ctx_clone.request_repaint();
        });
    }

    /// Takip Listesi kutucuğu: yereli hemen güncelle (toplam anında değişsin), sonra çekirdeğe kaydet.
    fn toplam_secimi_degistir(&mut self, ctx: &egui::Context, degisen: Vec<(String, bool)>) {
        if degisen.is_empty() {
            return;
        }
        if let Some(pano) = self.pano.as_mut() {
            for (id, secili) in &degisen {
                if let Some(u) = pano.urunler.iter_mut().find(|u| &u.id == id) {
                    u.toplamda = Some(*secili);
                }
            }
            let yeni = yerel_toplam_hesapla(&pano.urunler);
            pano.sistem_toplami = Some(yeni);
        }
        let secimler: std::collections::BTreeMap<String, bool> = degisen.into_iter().collect();
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let _ = tx.send(ArkaPlanMesaji::ToplamSonucu(kaynak.toplam_sec(&secimler)));
            ctx_clone.request_repaint();
        });
    }

    // ======================= G1i: tarama planı, elle tarama, takip, alarm, Telegram =======================

    /// GET /tarama/plan (`araliklar` doluysa PUT ile değiştirir).
    fn tarama_plani_iste(
        &mut self,
        ctx: &egui::Context,
        araliklar: Option<std::collections::BTreeMap<String, u32>>,
    ) {
        if self.tarama_plani_bekliyor && araliklar.is_none() {
            return;
        }
        self.tarama_plani_bekliyor = true;
        self.son_plan_istegi = Instant::now();
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let sonuc = match araliklar {
                None => kaynak.tarama_plani_getir(),
                Some(a) => kaynak.tarama_plani_guncelle(a).map(Some),
            };
            let _ = tx.send(ArkaPlanMesaji::TaramaPlani(sonuc));
            ctx_clone.request_repaint();
        });
    }

    /// "Sıfırla": seçili kategorilerin geri sayımı şimdiden, seçili süreyle yeniden başlar (boş = hepsi).
    fn sayac_sifirla_iste(&mut self, ctx: &egui::Context, kategoriler: Vec<String>) {
        if self.sayac_sifir_bekliyor {
            return;
        }
        self.sayac_sifir_bekliyor = true;
        let tumu = kategoriler.is_empty();
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let sonuc = kaynak.tarama_sayac_sifirla(kategoriler);
            let _ = tx.send(ArkaPlanMesaji::SayacSifir { tumu, sonuc });
            ctx_clone.request_repaint();
        });
    }

    /// Periyodik plan okuması (istek sürerken tekrar gönderilmez).
    fn tarama_plani_ani_tazele_iste(&mut self, ctx: &egui::Context) {
        if !self.tarama_plani_bekliyor {
            self.tarama_plani_iste(ctx, None);
        }
    }

    /// Üst çubuk bağlamı: hangi ekrandayız, hangi ürün seçili?
    fn etkin_baglam(&self) -> ta::EtkinBaglam {
        let kategori_bul = |id: &str| {
            self.pano
                .as_ref()
                .and_then(|p| p.urunler.iter().find(|u| u.id == id))
                .and_then(|u| u.kategori.clone())
        };
        match self.aktif_sekme {
            Sekme::Ucuslar | Sekme::SeyahatPlanla => ta::EtkinBaglam::Ucus,
            Sekme::UrunDetayi => {
                let kat = self
                    .urun_detay
                    .as_ref()
                    .filter(|d| self.detay_guncel_mi(d))
                    .and_then(|d| d.urun.kategori.clone())
                    .or_else(|| self.secili_urun_id.as_deref().and_then(kategori_bul));
                ta::EtkinBaglam::Urun(kat)
            }
            Sekme::TakipListesi => match self.takip_tiklanan_urun.as_deref() {
                Some(id) => ta::EtkinBaglam::Urun(kategori_bul(id)),
                None => ta::EtkinBaglam::Siradaki,
            },
            _ => ta::EtkinBaglam::Siradaki,
        }
    }

    /// Kategori kimliği → okunur ad ("gpu" → "Ekran kartı"); plan yoksa kimlik.
    fn kategori_adi(&self, id: &str) -> String {
        self.tarama_plani
            .as_ref()
            .and_then(|p| p.kategoriler.iter().find(|k| k.id == id))
            .map(|k| k.ad.clone())
            .unwrap_or_else(|| id.to_string())
    }

    fn elle_kilit_kalan(&self, k: &crate::api::TaramaKategori) -> u64 {
        ta::elle_kilit_kalan_sn(
            k,
            self.tarama_plani_ani.elapsed(),
            self.elle_kilitler.bitis(&k.id),
            Instant::now(),
        )
    }

    /// Elle tarama: kategoriyi hemen kuyruğa al; aynı kategori 10 dk kilitlenir.
    fn elle_tarama_baslat(&mut self, ctx: &egui::Context, kategori_id: String, kategori_ad: String) {
        if self.elle_tarama_bekliyor.is_some() {
            return;
        }
        let kilit_sn = self
            .tarama_plani
            .as_ref()
            .map(|p| p.manuel_bekleme_dk as u64 * 60)
            .filter(|s| *s > 0)
            .unwrap_or(ta::ELLE_KILIT_SN);
        self.elle_kilitler.kilitle(&kategori_id, kilit_sn, Instant::now());
        self.elle_tarama_bekliyor = Some(kategori_id.clone());
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let sonuc = kaynak.elle_tarama(&kategori_id);
            let _ = tx.send(ArkaPlanMesaji::ElleTarama { kategori_id, kategori_ad, sonuc });
            ctx_clone.request_repaint();
        });
    }

    fn takip_islem_gonder(&mut self, ctx: &egui::Context, cikar: bool, secim: Vec<(String, String)>) {
        if secim.is_empty() || self.takip_islem_bekliyor {
            return;
        }
        self.takip_islem_bekliyor = true;
        let (idler, adlar): (Vec<String>, Vec<String>) = secim.into_iter().unzip();
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let sonuc = kaynak.takip_islem(cikar, idler);
            let _ = tx.send(ArkaPlanMesaji::TakipIslem { cikar, adlar, sonuc });
            ctx_clone.request_repaint();
        });
    }

    fn gizlilik_gonder(&mut self, ctx: &egui::Context, alan: &'static str, deger: bool) {
        if self.gizlilik_bekliyor {
            return;
        }
        self.gizlilik_bekliyor = true;
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let sonuc = kaynak.gizlilik_ayari_yaz(alan, deger);
            let _ = tx.send(ArkaPlanMesaji::GizlilikAyari(sonuc));
            ctx_clone.request_repaint();
        });
    }

    fn paralel_gonder(&mut self, ctx: &egui::Context, govde: serde_json::Value) {
        if self.gizlilik_bekliyor {
            return;
        }
        self.gizlilik_bekliyor = true;
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let sonuc = kaynak.paralel_ayari_yaz(govde);
            let _ = tx.send(ArkaPlanMesaji::GizlilikAyari(sonuc));
            ctx_clone.request_repaint();
        });
    }

    fn site_etkin_gonder(&mut self, ctx: &egui::Context, ad: String, etkin: bool) {
        if self.site_islem_bekliyor.is_some() {
            return;
        }
        self.site_islem_bekliyor = Some(ad.clone());
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let sonuc = kaynak.site_etkin_yaz(&ad, etkin);
            let _ = tx.send(ArkaPlanMesaji::SiteEtkin(sonuc));
            ctx_clone.request_repaint();
        });
    }

    fn alarm_gonder(&mut self, ctx: &egui::Context, urun_id: String, urun_ad: String, fiyat: Option<f64>) {
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let sonuc = kaynak.alarm_yaz(&urun_id, fiyat);
            let _ = tx.send(ArkaPlanMesaji::AlarmSonucu { urun_ad, sonuc });
            ctx_clone.request_repaint();
        });
    }

    /// `istek` None → GET, Some → PUT /ayarlar/telegram
    fn telegram_ayar_iste(&mut self, ctx: &egui::Context, istek: Option<crate::api::TelegramAyarIstek>) {
        self.telegram_formu.bekliyor = true;
        self.telegram_formu.istendi = true;
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let kaydedildi = istek.is_some();
            let sonuc = match istek {
                None => kaynak.telegram_ayari_getir(),
                Some(i) => kaynak.telegram_ayari_yaz(&i),
            };
            let _ = tx.send(ArkaPlanMesaji::TelegramAyar { kaydedildi, sonuc });
            ctx_clone.request_repaint();
        });
    }

    fn telegram_gonder(&mut self, ctx: &egui::Context, rapor: bool) {
        self.telegram_formu.bekliyor = true;
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        thread::spawn(move || {
            let sonuc = kaynak.telegram_gonder(rapor);
            let _ = tx.send(ArkaPlanMesaji::TelegramGonderim { rapor, sonuc });
            ctx_clone.request_repaint();
        });
    }

    /// Satır içi alarm kutusu: Enter kaydet, Esc vazgeç, boş = alarmı kaldır. `true` → düzenleme kapandı.
    fn alarm_kutusu_ciz(&mut self, ctx: &egui::Context, ui: &mut egui::Ui, genislik: f32) -> bool {
        let Some(d) = self.alarm_duzenleme.as_mut() else { return true };
        let kutu = ui.add(
            egui::TextEdit::singleline(&mut d.metin)
                .desired_width(genislik)
                .hint_text("boş = alarm yok"),
        );
        if !d.odak_istendi {
            kutu.request_focus();
            d.odak_istendi = true;
        }
        let (enter, esc) = ui.input(|i| (i.key_pressed(egui::Key::Enter), i.key_pressed(egui::Key::Escape)));
        let mut kapandi = false;
        if esc {
            kapandi = true;
        } else if enter {
            match ta::alarm_girdisi_ayristir(&d.metin) {
                Ok(f) => {
                    let (id, ad) = (d.urun_id.clone(), d.urun_ad.clone());
                    kapandi = true;
                    self.alarm_gonder(ctx, id, ad, f);
                }
                Err(e) => {
                    d.hata = Some(e.clone());
                    self.toast_ekle(ToastTuru::Hata, e);
                }
            }
        } else if kutu.lost_focus() && d.yer == AlarmDuzenlemeYeri::TakipSatiri {
            // başka yere tıklandı: kaydetmeden kapat (yanlışlıkla Telegram tetiklenmesin)
            kapandi = true;
        }
        if let Some(d) = self.alarm_duzenleme.as_ref() {
            if let Some(h) = &d.hata {
                kutu.on_hover_text(h);
            } else {
                kutu.on_hover_text("TL · Enter: kaydet · Esc: vazgeç · boş bırak: alarmı kaldır");
            }
        }
        if kapandi {
            self.alarm_duzenleme = None;
        }
        kapandi
    }

    fn genel_yenileme_baslat(&mut self, ctx: &egui::Context) {
        if self.yukleniyor {
            return;
        }
        self.yukleniyor = true;
        self.son_yenileme_ani = Instant::now();

        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();
        let secili_id = self.secili_urun_id.clone();

        thread::spawn(move || {
            let _ = kaynak.saglik_getir();
            let pano = kaynak.pano_getir();
            let ilk_urun_id = secili_id.or_else(|| {
                pano.as_ref()
                    .ok()
                    .and_then(|p| p.urunler.first().map(|u| u.id.clone()))
            });
            let detay = ilk_urun_id.map(|id| kaynak.urun_detay_getir(&id));
            let siteler = kaynak.siteler_getir();
            let seyahat = kaynak.seyahat_getir();
            let zaman_etiketi = simdiki_saat_dizgesi();

            let _ = tx.send(ArkaPlanMesaji::GenelYenileme {
                pano,
                siteler,
                seyahat,
                detay,
                zaman_etiketi,
            });
            ctx_clone.request_repaint();
        });
    }

    fn urun_detay_yukle(&mut self, ctx: &egui::Context, urun_id: String) {
        self.secili_urun_id = Some(urun_id.clone());
        self.suzgec_site = "Tümü".to_string();
        self.yukleniyor = true;
        self.detay_yukleniyor = true;

        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();

        thread::spawn(move || {
            let sonuc = kaynak.urun_detay_getir(&urun_id);
            let _ = tx.send(ArkaPlanMesaji::UrunDetaySonucu(sonuc));
            ctx_clone.request_repaint();
        });
    }

    fn tanimla_istegi_gonder(&mut self, ctx: &egui::Context) {
        let metin = self.tanimla_metin.trim().to_string();
        if metin.is_empty() || self.tanimla_yukleniyor {
            return;
        }
        self.tanimla_yukleniyor = true;
        self.tanimla_bilgi_mesaji = None;
        self.reddedilen_adaylar.clear();

        let tur = self.tanimla_tur.clone();
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();

        thread::spawn(move || {
            let sonuc = kaynak.tanimla_gonder(&metin, &tur);
            let _ = tx.send(ArkaPlanMesaji::TanimlaSonucu(sonuc));
            ctx_clone.request_repaint();
        });
    }

    fn aday_onayla_gonder(
        &mut self,
        ctx: &egui::Context,
        istek_id: String,
        aday_id: String,
        baslik: String,
        mpn: Option<String>,
    ) {
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();

        thread::spawn(move || {
            let sonuc = kaynak
                .aday_onayla(&istek_id, vec![aday_id], Some(baslik.clone()), mpn)
                .map(|y| {
                    format!(
                        "✓ Aday onaylandı ve takibe eklendi: {}",
                        y.id.unwrap_or(baslik)
                    )
                });
            let _ = tx.send(ArkaPlanMesaji::OnaylaSonucu(sonuc));
            ctx_clone.request_repaint();
        });
    }

    fn urun_tara_gonder(&mut self, ctx: &egui::Context, urun_id: String) {
        let kaynak = self.veri_kaynagi.clone();
        let tx = self.tx.clone();
        let ctx_clone = ctx.clone();

        thread::spawn(move || {
            let sonuc = kaynak
                .tarama_baslat(&urun_id)
                .map(|y| format!("Tarama başlatıldı (görev: {})", y.gorev_id));
            let _ = tx.send(ArkaPlanMesaji::TaramaSonucu(sonuc));
            ctx_clone.request_repaint();
        });
    }

    fn olay_isle(&mut self, ctx: &egui::Context, olay: &OlayMesaji) {
        let mesaj_metni = olay
            .mesaj
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_string);
        let hedef_altinda = olay
            .veri
            .get("hedef_altinda")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
            || mesaj_metni
                .as_deref()
                .is_some_and(|m| m.to_lowercase().contains("hedef"));
        let hata_var = olay.tur.contains("hata")
            || olay
                .veri
                .get("hata")
                .and_then(|v| v.as_str())
                .is_some_and(|s| !s.trim().is_empty());

        match olay.tur.as_str() {
            "tarama_bitti" => {
                let metin = mesaj_metni.unwrap_or_else(|| "Tarama bitti".to_string());
                self.toast_ekle(
                    if hedef_altinda {
                        ToastTuru::Basari
                    } else {
                        ToastTuru::Bilgi
                    },
                    metin,
                );
                self.son_ws_yenileme = Instant::now();
                self.genel_yenileme_baslat(ctx);
            }
            "tarama_site_bitti" => {
                if hedef_altinda {
                    let urun_ad = olay
                        .urun_id
                        .as_deref()
                        .unwrap_or("Ürün");
                    let site = olay
                        .veri
                        .get("site")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    let ek = if site.is_empty() {
                        urun_ad.to_string()
                    } else {
                        format!("{urun_ad} ({site})")
                    };
                    self.toast_ekle(
                        ToastTuru::Basari,
                        format!("🎯 Hedef fiyat yakalandı: {ek}"),
                    );
                } else if hata_var {
                    let hata = olay
                        .veri
                        .get("hata")
                        .and_then(|v| v.as_str())
                        .or(mesaj_metni.as_deref())
                        .unwrap_or("Site tarama hatası");
                    self.toast_ekle(ToastTuru::Hata, format!("Tarama hatası: {hata}"));
                } else if let Some(m) = mesaj_metni {
                    self.toast_ekle(ToastTuru::Bilgi, m);
                }
                if self.son_ws_yenileme.elapsed() >= Duration::from_secs(2) {
                    self.son_ws_yenileme = Instant::now();
                    self.genel_yenileme_baslat(ctx);
                }
            }
            "tarama_basladi" => {
                if let Some(m) = mesaj_metni {
                    self.toast_ekle(ToastTuru::Bilgi, m);
                }
            }
            "hedef_yakalandi" => {
                let m = mesaj_metni.unwrap_or_else(|| "🎯 Hedef fiyat yakalandı!".to_string());
                self.toast_ekle(ToastTuru::Basari, m);
                self.son_ws_yenileme = Instant::now();
                self.genel_yenileme_baslat(ctx);
            }
            "ayar_degisti" => {
                if let Some(m) = mesaj_metni {
                    self.toast_ekle(ToastTuru::Bilgi, m);
                }
                self.brave_ayari_istek(ctx, None);
            }
            "urun_tanimlandi" | "plan_guncellendi" => {
                if let Some(m) = mesaj_metni {
                    self.toast_ekle(ToastTuru::Bilgi, m);
                }
                self.son_ws_yenileme = Instant::now();
                self.genel_yenileme_baslat(ctx);
            }
            _ => {
                if hata_var {
                    let m = mesaj_metni.unwrap_or_else(|| format!("Hata ({})", olay.tur));
                    self.toast_ekle(ToastTuru::Hata, m);
                } else if hedef_altinda {
                    let m = mesaj_metni.unwrap_or_else(|| "🎯 Hedef fiyat yakalandı!".to_string());
                    self.toast_ekle(ToastTuru::Basari, m);
                    self.genel_yenileme_baslat(ctx);
                } else if let Some(m) = mesaj_metni {
                    self.toast_ekle(ToastTuru::Bilgi, m);
                }
            }
        }
    }

    fn mesajlari_isle(&mut self, ctx: &egui::Context) {
        while let Ok(mesaj) = self.rx.try_recv() {
            match mesaj {
                ArkaPlanMesaji::GenelYenileme {
                    pano,
                    siteler,
                    seyahat,
                    detay,
                    zaman_etiketi,
                } => {
                    self.yukleniyor = false;
                    self.detay_yukleniyor = false;
                    let mut hatalar = Vec::new();
                    let mut en_az_bir_basari = false;

                    match pano {
                        Ok(p) => {
                            if self.secili_urun_id.is_none() {
                                self.secili_urun_id = p.urunler.first().map(|u| u.id.clone());
                            }
                            if let Some(g) = &p.guncelleme {
                                self.son_yenileme_zamani = bicim::iso_tarih(g);
                            } else {
                                self.son_yenileme_zamani = zaman_etiketi.clone();
                            }
                            if self.acik_taksit_penceresi.is_none() {
                                if let Some(hedef_id) = self.baslangic_taksit_ac.as_deref() {
                                    if let Some(urun) = p.urunler.iter().find(|u| u.id == hedef_id)
                                    {
                                        if let Some(pencere) =
                                            AcikTaksitPenceresi::from_pano_urun(urun)
                                        {
                                            self.acik_taksit_penceresi = Some(pencere);
                                            self.baslangic_taksit_ac = None;
                                        }
                                    }
                                }
                            }
                            self.pano = Some(p);
                            en_az_bir_basari = true;
                        }
                        Err(e) => hatalar.push(e),
                    }

                    match siteler {
                        Ok(s) => {
                            self.siteler = s;
                            en_az_bir_basari = true;
                        }
                        Err(e) => hatalar.push(e),
                    }

                    match seyahat {
                        Ok(sy) => {
                            self.seyahat = Some(sy);
                            en_az_bir_basari = true;
                        }
                        Err(e) => hatalar.push(e),
                    }

                    if let Some(detay_sonuc) = detay {
                        match detay_sonuc {
                            // Yarış koruması: yalnız ŞU AN seçili ürünün detayı ekrana yazılır
                            Ok(d) if self.detay_guncel_mi(&d) => {
                                let mevcut_hala_var = self.secili_teklif_id.is_some_and(|id| {
                                    d.teklifler.iter().any(|t| t.teklif_id == Some(id))
                                });
                                if !mevcut_hala_var {
                                    self.secili_teklif_id =
                                        d.teklifler.first().and_then(|t| t.teklif_id);
                                }
                                self.urun_detay = Some(d);
                                en_az_bir_basari = true;
                            }
                            Ok(_) => en_az_bir_basari = true,
                            Err(e) => hatalar.push(e),
                        }
                    }

                    if hatalar.is_empty() {
                        self.bagli = true;
                        self.son_hata = None;
                    } else {
                        self.bagli = en_az_bir_basari;
                        self.son_hata = Some(hatalar.join(" | "));
                    }
                }
                ArkaPlanMesaji::UrunDetaySonucu(sonuc) => {
                    self.yukleniyor = false;
                    self.detay_yukleniyor = false;
                    match sonuc {
                        Ok(d) if self.detay_guncel_mi(&d) => {
                            self.bagli = true;
                            self.son_hata = None;
                            self.secili_teklif_id = d.teklifler.first().and_then(|t| t.teklif_id);
                            self.urun_detay = Some(d);
                        }
                        Ok(_) => self.bagli = true, // eski seçimin geç gelen yanıtı: yok say
                        Err(e) => {
                            self.bagli = false;
                            self.son_hata = Some(e);
                        }
                    }
                }
                ArkaPlanMesaji::TanimlaSonucu(sonuc) => {
                    self.tanimla_yukleniyor = false;
                    match sonuc {
                        Ok(yanit) => {
                            self.bagli = true;
                            if yanit.adaylar.is_empty() {
                                self.tanimla_bilgi_mesaji = Some(
                                    "Aday bulunamadı — tanımlama A3 aşamasında gelecek".to_string(),
                                );
                            }
                            self.tanimla_sonuc = Some(yanit);
                        }
                        Err(e) => {
                            self.bagli = false;
                            self.son_hata = Some(e.clone());
                            self.tanimla_bilgi_mesaji = Some(format!("Hata: {}", e));
                        }
                    }
                }
                ArkaPlanMesaji::OnaylaSonucu(sonuc) => match sonuc {
                    Ok(mesaj) => {
                        self.tanimla_bilgi_mesaji = Some(mesaj.clone());
                        self.toast_ekle(ToastTuru::Basari, mesaj);
                    }
                    Err(e) => {
                        self.tanimla_bilgi_mesaji = Some(format!("Onay hatası: {}", e));
                        self.toast_ekle(ToastTuru::Hata, format!("Onay hatası: {}", e));
                    }
                },
                ArkaPlanMesaji::TaramaSonucu(sonuc) => match sonuc {
                    Ok(mesaj) => {
                        self.son_hata = None;
                        self.son_yenileme_zamani = format!("{} ({})", self.son_yenileme_zamani, mesaj);
                        self.toast_ekle(ToastTuru::Bilgi, mesaj);
                    }
                    Err(e) => {
                        self.son_hata = Some(e.clone());
                        self.toast_ekle(ToastTuru::Hata, e);
                    }
                },
                ArkaPlanMesaji::ToplamSonucu(sonuc) => match sonuc {
                    Ok(toplam) => {
                        if let Some(pano) = self.pano.as_mut() {
                            for u in pano.urunler.iter_mut() {
                                u.toplamda = Some(toplam.secili.contains(&u.id));
                            }
                            pano.sistem_toplami = Some(toplam);
                        }
                    }
                    Err(e) => {
                        self.son_hata = Some(format!("Toplam seçimi kaydedilemedi: {}", e));
                    }
                },
                ArkaPlanMesaji::GizlilikAyari(sonuc) => {
                    self.gizlilik_bekliyor = false;
                    match sonuc {
                        Ok(a) => {
                            self.gizlilik = Some(a);
                            self.toast_ekle(ToastTuru::Basari, "✓ Tarayıcı ayarı kaydedildi");
                        }
                        Err(e) => self.toast_ekle(ToastTuru::Hata, format!("Tarayıcı ayarı kaydedilemedi: {e}")),
                    }
                }
                ArkaPlanMesaji::BraveAyari(sonuc) => {
                    self.brave_bekliyor = false;
                    match sonuc {
                        Ok(a) => {
                            self.brave_gorunur = Some(a.brave_gorunur);
                            self.gizlilik = Some(a.clone());
                            self.brave_ipucu = a
                                .brave
                                .iter()
                                .map(|b| {
                                    let durum = if !b.calisiyor {
                                        "çalışmıyor".to_string()
                                    } else if let Some(h) = &b.hata {
                                        format!("hata: {}", h)
                                    } else if b.pencereler.iter().all(|p| p == "minimized") {
                                        "simge durumunda".to_string()
                                    } else {
                                        "açık".to_string()
                                    };
                                    format!("{} (port {}): {}", b.ad, b.port, durum)
                                })
                                .collect::<Vec<_>>()
                                .join("\n");
                        }
                        Err(e) => {
                            self.son_hata = Some(format!("Tarayıcı ayarı: {}", e));
                        }
                    }
                }
                ArkaPlanMesaji::WsDurum(bagli) => {
                    self.ws_bagli = bagli;
                }
                ArkaPlanMesaji::WsOlay(olay) => {
                    self.ws_bagli = true;
                    self.olay_isle(ctx, &olay);
                }
                ArkaPlanMesaji::SayacSifir { tumu, sonuc } => {
                    self.sayac_sifir_bekliyor = false;
                    match sonuc {
                        Ok(y) => {
                            let ad = |id: &String| {
                                y.plan.kategoriler.iter().find(|k| &k.id == id).map(|k| k.ad.clone()).unwrap_or_else(|| id.clone())
                            };
                            let mut metin = if tumu {
                                format!("↺ {} kategorinin sayacı yeni sürelerle yeniden başladı", y.sifirlanan.len())
                            } else {
                                let adlar: Vec<String> = y.sifirlanan.iter().map(ad).collect();
                                format!("↺ {}: sayaç yeniden başladı", adlar.join(", "))
                            };
                            if !y.atlanan.is_empty() {
                                let adlar: Vec<String> = y.atlanan.iter().map(ad).collect();
                                metin.push_str(&format!(" · {} taranıyor, dokunulmadı", adlar.join(", ")));
                            }
                            self.toast_ekle(ToastTuru::Basari, metin);
                            self.tarama_plani = Some(y.plan);
                            self.tarama_plani_yok = false;
                            self.tarama_plani_ani = Instant::now();
                        }
                        Err(e) => self.toast_ekle(ToastTuru::Hata, format!("Sayaç sıfırlanamadı: {e}")),
                    }
                }
                ArkaPlanMesaji::TaramaPlani(sonuc) => {
                    self.tarama_plani_bekliyor = false;
                    match sonuc {
                        Ok(Some(yeni)) => {
                            let mut taze_veri = false;
                            if let Some(eski) = &self.tarama_plani {
                                for m in ta::bitmis_taramalar(eski, &yeni) {
                                    self.toast_ekle(ToastTuru::Basari, format!("✓ {m}"));
                                    taze_veri = true;
                                }
                            }
                            self.tarama_plani = Some(yeni);
                            self.tarama_plani_yok = false;
                            self.tarama_plani_ani = Instant::now();
                            if taze_veri {
                                self.genel_yenileme_baslat(ctx);
                            }
                        }
                        Ok(None) => {
                            self.tarama_plani = None;
                            self.tarama_plani_yok = true;
                            self.tarama_plani_ani = Instant::now();
                        }
                        Err(e) => {
                            // plan okunamadı: son bilineni koru; yalnız Ayarlar'da (değişiklik yapılırken) göster
                            if self.aktif_sekme == Sekme::Ayarlar {
                                self.toast_ekle(ToastTuru::Hata, format!("Tarama planı: {e}"));
                            }
                        }
                    }
                }
                ArkaPlanMesaji::ElleTarama { kategori_id, kategori_ad, sonuc } => {
                    self.elle_tarama_bekliyor = None;
                    match sonuc {
                        Ok(ElleTaramaYanit::Kuyrukta) => {
                            self.toast_ekle(
                                ToastTuru::Bilgi,
                                format!("🔄 {kategori_ad} taraması başlatıldı (kuyrukta)"),
                            );
                            self.genel_yenileme_baslat(ctx);
                        }
                        Ok(ElleTaramaYanit::Bekle(kalan)) => {
                            self.elle_kilitler.kilitle(&kategori_id, kalan, Instant::now());
                            self.toast_ekle(
                                ToastTuru::Bilgi,
                                format!(
                                    "{kategori_ad}: elle tarama için {} beklenmeli",
                                    ta::geri_sayim(kalan)
                                ),
                            );
                        }
                        Err(e) => {
                            self.elle_kilitler.0.remove(&kategori_id);
                            self.toast_ekle(ToastTuru::Hata, format!("Tarama başlatılamadı: {e}"));
                        }
                    }
                    self.tarama_plani_iste(ctx, None);
                }
                ArkaPlanMesaji::TakipIslem { cikar, adlar, sonuc } => {
                    self.takip_islem_bekliyor = false;
                    match sonuc {
                        Ok(pano) => {
                            if cikar {
                                if self
                                    .takip_tiklanan_urun
                                    .as_ref()
                                    .is_some_and(|id| !pano.urunler.iter().any(|u| &u.id == id))
                                {
                                    self.takip_tiklanan_urun = None;
                                }
                                if self
                                    .secili_urun_id
                                    .as_ref()
                                    .is_some_and(|id| !pano.urunler.iter().any(|u| &u.id == id))
                                {
                                    self.secili_urun_id = pano.urunler.first().map(|u| u.id.clone());
                                }
                            }
                            self.pano = Some(pano);
                            self.toast_ekle(
                                ToastTuru::Basari,
                                if cikar {
                                    format!("🗑 Takipten çıkarıldı: {}", adlar.join(", "))
                                } else {
                                    format!("↩ Yeniden takipte: {}", adlar.join(", "))
                                },
                            );
                            self.tarama_plani_iste(ctx, None);
                        }
                        Err(e) => self.toast_ekle(ToastTuru::Hata, format!("Takip listesi: {e}")),
                    }
                }
                ArkaPlanMesaji::SiteEtkin(sonuc) => {
                    self.site_islem_bekliyor = None;
                    match sonuc {
                        Ok(s) => {
                            let ad = bicim::site_okunur(&s.ad);
                            self.toast_ekle(
                                ToastTuru::Basari,
                                if s.etkin {
                                    format!("✓ {ad} yeniden aranacak")
                                } else {
                                    format!("{ad} aramalardan çıkarıldı — teklifleri listelerde görünmez")
                                },
                            );
                            match self.siteler.iter_mut().find(|x| x.ad == s.ad) {
                                Some(x) => *x = s,
                                None => self.siteler.push(s),
                            }
                            self.genel_yenileme_baslat(ctx);
                        }
                        Err(e) => self.toast_ekle(ToastTuru::Hata, format!("Site ayarı kaydedilemedi: {e}")),
                    }
                }
                ArkaPlanMesaji::AlarmSonucu { urun_ad, sonuc } => match sonuc {
                    Ok(y) => {
                        if let Some(pano) = self.pano.as_mut() {
                            if let Some(u) = pano.urunler.iter_mut().find(|u| u.id == y.urun_id) {
                                u.alarm_fiyat = y.alarm_fiyat;
                                u.hedef_fiyat = y.alarm_fiyat;
                            }
                        }
                        if let Some(d) = self.urun_detay.as_mut().filter(|d| d.urun.id == y.urun_id) {
                            d.urun.alarm_fiyat = y.alarm_fiyat;
                            d.urun.hedef_fiyat = y.alarm_fiyat;
                        }
                        if !y.hemen_gonderilen.is_empty() {
                            self.toast_ekle(
                                ToastTuru::Basari,
                                "🔔 Fiyat zaten alarmın altında — Telegram'a gönderildi",
                            );
                        } else {
                            self.toast_ekle(
                                ToastTuru::Basari,
                                match y.alarm_fiyat {
                                    Some(f) => format!("🔔 {urun_ad}: alarm fiyatı {}", bicim::tl(f)),
                                    None => format!("{urun_ad}: alarm kaldırıldı"),
                                },
                            );
                        }
                        self.genel_yenileme_baslat(ctx);
                    }
                    Err(e) => self.toast_ekle(ToastTuru::Hata, format!("Alarm kaydedilemedi: {e}")),
                },
                ArkaPlanMesaji::TelegramAyar { kaydedildi, sonuc } => {
                    self.telegram_formu.bekliyor = false;
                    match sonuc {
                        Ok(a) => {
                            self.telegram_formu.yukle(a);
                            if kaydedildi {
                                self.telegram_formu.mesaj = Some((true, "✓ Telegram ayarı kaydedildi".into()));
                            }
                        }
                        Err(e) => {
                            self.telegram_formu.mesaj = Some((false, format!("✗ {e}")));
                        }
                    }
                }
                ArkaPlanMesaji::TelegramGonderim { rapor, sonuc } => {
                    self.telegram_formu.bekliyor = false;
                    let ne = if rapor { "Rapor" } else { "Test mesajı" };
                    self.telegram_formu.mesaj = Some(match sonuc {
                        Ok(true) => (true, format!("✓ {ne} gönderildi")),
                        Ok(false) => (false, format!("✗ {ne} gönderilemedi (ayar ya da ağ)")),
                        Err(e) => (false, format!("✗ {ne} gönderilemedi: {e}")),
                    });
                }
            }
        }
    }

    // ======================= Üst Çubuk =======================

    fn brave_anahtari_ciz(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let p = tema::palet(ui.visuals());
        let (metin, renk, ipucu) = match self.brave_gorunur {
            Some(true) => (
                "👁 Tarayıcı: görünür",
                p.vurgu,
                "Tarama sırasında tarayıcı penceresi ekranda. Tıkla: gizle (arka planda tara)",
            ),
            Some(false) => (
                "👁 Tarayıcı: gizli",
                p.kapali,
                "Tarama arka planda; tarayıcı penceresi simge durumunda. Doğrulama (CAPTCHA) gerekirse kendiliğinden açılır. Tıkla: göster",
            ),
            None => ("👁 Tarayıcı: ?", p.kapali, "Durum okunuyor…"),
        };
        let tam_ipucu = if self.brave_ipucu.is_empty() {
            ipucu.to_string()
        } else {
            format!("{}\n\n{}", ipucu, self.brave_ipucu)
        };
        let dugme = ui
            .add_enabled(
                !self.brave_bekliyor && self.brave_gorunur.is_some(),
                egui::Button::new(RichText::new(metin).color(renk)),
            )
            .on_hover_text(tam_ipucu);
        if dugme.clicked() {
            if let Some(g) = self.brave_gorunur {
                self.brave_ayari_istek(ctx, Some(!g));
            }
        }
        if self.brave_bekliyor {
            ui.spinner();
        }
    }

    /// Üst çubuk: etkin kategorinin sonraki taramasına geri sayım + elle tarama (🔄 Yenile, 10 dk kilit).
    fn tarama_sayaci_ciz(&mut self, ctx: &egui::Context, ui: &mut egui::Ui, dar: bool) {
        let p = tema::palet(ui.visuals());
        let baglam = self.etkin_baglam();
        let etkin = self
            .tarama_plani
            .as_ref()
            .and_then(|plan| ta::etkin_kategori(plan, &baglam))
            .cloned();

        let Some(k) = etkin else {
            // Zamanlayıcı yok (404) ya da plan henüz okunmadı: Yenile yalnız ekran verisini okur
            if ui
                .add_enabled(!self.yukleniyor, egui::Button::new("🔄 Yenile"))
                .on_hover_text("Tarama zamanlayıcısı henüz yok: yalnız ekran verisini yeniden okur (F5)")
                .clicked()
            {
                self.genel_yenileme_baslat(ctx);
            }
            let metin = if self.tarama_plani_yok {
                "zamanlayıcı henüz yok"
            } else {
                "tarama planı okunuyor…"
            };
            ui.label(RichText::new(metin).weak().size(13.0));
            return;
        };

        let kilit = self.elle_kilit_kalan(&k);
        let bekliyor = self.elle_tarama_bekliyor.as_deref() == Some(k.id.as_str());
        let dugme_metni = if kilit > 0 {
            format!("🔄 {}", ta::geri_sayim(kilit))
        } else {
            "🔄 Yenile".to_string()
        };
        let ipucu = if kilit > 0 {
            format!(
                "{} için elle tarama {} sonra yeniden yapılabilir.\nSiteler bot sanmasın diye elle tarama aynı kategori için 10 dk'da bir.",
                k.ad,
                ta::geri_sayim(kilit)
            )
        } else {
            format!(
                "{} kategorisini ŞİMDİ tara (sitelerden gerçek fiyat çeker).\nSiteler bot sanmasın diye elle tarama aynı kategori için 10 dk'da bir.\nF5: yalnız ekran verisini yeniden okur.",
                k.ad
            )
        };
        let dugme = ui
            .add_enabled(
                kilit == 0 && !bekliyor && k.durum != "calisiyor",
                egui::Button::new(RichText::new(dugme_metni)),
            )
            .on_hover_text(&ipucu)
            .on_disabled_hover_text(&ipucu);
        if dugme.clicked() {
            self.elle_tarama_baslat(ctx, k.id.clone(), k.ad.clone());
            self.genel_yenileme_baslat(ctx);
        }

        let kalan = ta::kategori_kalan_sn(&k, self.tarama_plani_ani.elapsed());
        match k.durum.as_str() {
            "calisiyor" => {
                ui.spinner();
                ui.label(RichText::new(format!("{} · taranıyor…", k.ad)).color(p.vurgu).size(13.0));
            }
            "kuyrukta" => {
                ui.label(RichText::new(format!("{} · kuyrukta", k.ad)).color(p.uyari).size(13.0))
                    .on_hover_text("Önceki tarama bitince (en az 2 dk arayla) başlayacak");
            }
            _ => {
                let metin = if dar {
                    format!("{} · {}", k.ad, ta::geri_sayim(kalan))
                } else {
                    format!("{} · sonraki tarama {}", k.ad, ta::geri_sayim(kalan))
                };
                ui.label(RichText::new(metin).monospace().size(13.0)).on_hover_text(format!(
                    "Her {} dk'da bir taranır (Ayarlar → Tarayıcı turu).{}\nSon tarama: {}",
                    k.aralik_dk,
                    match &k.sonraki {
                        Some(s) => format!("\nSonraki: {}", bicim::iso_tarih(s)),
                        None => String::new(),
                    },
                    k.son_bitis
                        .as_deref()
                        .map(|b| format!("{} ({})", bicim::iso_tarih(b), ta::son_sonuc_metni(&k)))
                        .unwrap_or_else(|| "henüz yok".to_string())
                ));
            }
        }
    }

    fn ust_cubuk_ciz(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let p = tema::palet(ui.visuals());
        let dar = ctx.input(|i| i.content_rect().width()) <= 900.0;

        ui.horizontal_wrapped(|ui| {
            ui.spacing_mut().item_spacing.x = 8.0;
            ui.heading(RichText::new("Kazı Asistan").strong().size(18.0));
            ui.separator();

            // Bağlantı durumu (WS koparsa HTTP modunda da "● bağlantı yok" gösterir)
            let (durum_metni, durum_rengi) = if self.baglanti_tam_mi() {
                match &self.veri_kaynagi {
                    VeriKaynagi::Http(_) => ("● bağlı", p.basari),
                    VeriKaynagi::Sahte(_) => ("● bağlı (sahte veri)", p.basari),
                }
            } else {
                ("● bağlantı yok", p.hata)
            };
            ui.label(RichText::new(durum_metni).color(durum_rengi).strong())
                .on_hover_text(if matches!(self.veri_kaynagi, VeriKaynagi::Http(_)) && !self.ws_bagli {
                    "Canlı olay bağlantısı (WS /olaylar) koptu, 5 sn'de bir yeniden bağlanılıyor"
                } else {
                    "Çekirdek API ve canlı olay bağlantısı durumu"
                });

            if !dar {
                ui.separator();
                ui.label(
                    RichText::new(format!("Son yenileme: {}", self.son_yenileme_zamani))
                        .size(13.0)
                        .weak(),
                );
            }

            self.tarama_sayaci_ciz(ctx, ui, dar);

            ui.separator();
            self.brave_anahtari_ciz(ctx, ui);

            // Sağ "Tarayıcılar" paneli aç/kapa
            let tarayici_etiket = if self.tarayici_paneli.gorunur { "🖥 Tarayıcılar ✓" } else { "🖥 Tarayıcılar" };
            if ui
                .selectable_label(self.tarayici_paneli.gorunur, tarayici_etiket)
                .on_hover_text("Sağdaki tarayıcı izleme panelini aç/kapa (yeşil=çalışıyor, sarı=bot koruması, gri=boşta)")
                .clicked()
            {
                self.tarayici_paneli.gorunur = !self.tarayici_paneli.gorunur;
            }

            // Hızlı tema değiştirme düğmesi
            let tema_etiket = self.ayarlar.tema_tercihi.etiket();
            if ui
                .button(tema_etiket)
                .on_hover_text("Temayı değiştir (Sistem / Açık / Koyu)")
                .clicked()
            {
                self.ayarlar.tema_tercihi = self.ayarlar.tema_tercihi.sonraki();
                tema::tema_uygula(ctx, self.ayarlar.tema_tercihi);
                if self.goruntu_hedefi.is_none() {
                    let _ = self.ayarlar.kaydet();
                }
            }

            if self.yukleniyor {
                ui.spinner();
                if !dar {
                    ui.label(RichText::new("Yükleniyor…").weak());
                }
            }
        });

        if let Some(hata) = &self.son_hata {
            ui.add_space(4.0);
            egui::Frame::NONE
                .fill(p.hata_dolgu)
                .stroke(Stroke::new(1.0_f32, p.hata))
                .inner_margin(8.0)
                .corner_radius(6.0)
                .show(ui, |ui| {
                    ui.label(
                        RichText::new(format!(
                            "⚠ Çekirdek bağlantı uyarısı (son bilinen veriler gösteriliyor): {}",
                            hata
                        ))
                        .color(p.hata),
                    );
                });
        }
    }

    // ======================= Ortak Kart / Arama / Durum Yardımcıları =======================

    fn arama_kutusu_ciz(
        ui: &mut egui::Ui,
        metin: &mut String,
        ipucu: &str,
        odak_iste: &mut bool,
    ) {
        ui.horizontal(|ui| {
            ui.label("🔍");
            let edit = ui.add_sized(
                [ui.available_width().clamp(160.0, 320.0), 26.0],
                egui::TextEdit::singleline(metin)
                    .hint_text(ipucu)
                    .desired_width(240.0),
            );
            if *odak_iste {
                edit.request_focus();
                *odak_iste = false;
            }
            if !metin.is_empty() && ui.small_button("✕").on_hover_text("Aramayı temizle (Esc)").clicked() {
                metin.clear();
            }
        });
    }

    fn bos_durum_karti(ui: &mut egui::Ui, simge: &str, mesaj: &str) {
        let p = tema::palet(ui.visuals());
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.vertical_centered(|ui| {
                ui.add_space(14.0);
                ui.label(RichText::new(simge).size(28.0).color(p.kapali));
                ui.add_space(6.0);
                ui.label(RichText::new(mesaj).size(14.0).weak());
                ui.add_space(14.0);
            });
        });
    }

    fn yukleniyor_karti(ui: &mut egui::Ui, mesaj: &str) {
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.spinner();
                ui.label(RichText::new(mesaj).size(14.0).weak());
            });
        });
    }

    // ======================= 1. Ekran: Takip Listesi (/pano) =======================

    fn ekran_takip_listesi(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let duzen = duyarli_duzen_hesapla(ctx.input(|i| i.content_rect().width()));
        let p = tema::palet(ui.visuals());

        ui.horizontal_wrapped(|ui| {
            ui.label(RichText::new("Takip Listesi").strong().size(22.0));
            ui.add_space(6.0);
            ui.label(
                RichText::new("Çift tıkla veya sağ tıkla: teklifler, taksit tablosu ve fiyat geçmişi")
                    .size(13.0)
                    .weak(),
            );
        });
        ui.add_space(6.0);

        let Some(pano) = self.pano.clone() else {
            if self.yukleniyor {
                Self::yukleniyor_karti(ui, "Takip listesi verileri yükleniyor…");
            } else {
                Self::bos_durum_karti(
                    ui,
                    "📋",
                    "Henüz takip edilen ürün verisi yok; çekirdek bağlantısını kontrol edip Yenile'ye basın.",
                );
            }
            return;
        };

        // Özet Kartları (tıklanabilir süzgeçler)
        let toplam = pano
            .sistem_toplami
            .clone()
            .unwrap_or_else(|| yerel_toplam_hesapla(&pano.urunler));
        let secili_urun_sayisi = pano
            .urunler
            .iter()
            .filter(|u| u.toplamda.unwrap_or(u.sistemde))
            .count();
        let alarm_altinda_sayisi = pano.urunler.iter().filter(|u| ta::alarm_ulasti(u)).count();
        let teyit_bekleyen_sayisi = pano
            .urunler
            .iter()
            .filter(|u| {
                u.en_iyi
                    .as_ref()
                    .is_some_and(|e| e.teyit_durumu.as_deref() == Some("bekliyor"))
            })
            .count();
        let en_son_tarama = pano
            .urunler
            .iter()
            .filter_map(|u| u.son_tarama.as_deref())
            .max()
            .map(bicim::iso_tarih)
            .or_else(|| pano.guncelleme.as_deref().map(bicim::iso_tarih))
            .unwrap_or_else(|| self.son_yenileme_zamani.clone());

        let kart_sutun = if duzen.ikincil_sutunlari_gizle { 2 } else { 4 };
        let kart_genislik =
            ((ui.available_width() - 10.0 * (kart_sutun as f32 - 1.0)) / kart_sutun as f32)
                .floor()
                .max(140.0);

        let kartlar = [
            (
                TakipOzetFiltre::Toplamda,
                "Sistem Toplamı",
                bicim::tl(toplam.tutar),
                format!("{} ürün dahil · tıkla: süz", secili_urun_sayisi),
                Some(p.vurgu),
            ),
            (
                TakipOzetFiltre::AlarmAltinda,
                "Alarm Altında",
                format!("{} ürün", alarm_altinda_sayisi),
                "Alarm fiyatına ulaşanları göster".to_string(),
                Some(p.basari),
            ),
            (
                TakipOzetFiltre::TeyitBekleyen,
                "Teyit Bekleyen",
                format!("{} ürün", teyit_bekleyen_sayisi),
                "Teyit bekleyenleri göster".to_string(),
                Some(p.uyari),
            ),
            (
                TakipOzetFiltre::Tumu,
                "Son Tarama",
                en_son_tarama,
                format!("{} takip · süzgeci sıfırla", pano.urunler.len()),
                None,
            ),
        ];

        for (s_idx, satir) in kartlar.chunks(kart_sutun).enumerate() {
            if s_idx > 0 {
                ui.add_space(6.0);
            }
            ui.horizontal(|ui| {
                ui.spacing_mut().item_spacing.x = 10.0;
                for (filtre, baslik, deger, alt, renk) in satir {
                    let filtre = *filtre;
                    let renk = *renk;
                    let secili = self.takip_ozet_filtre == filtre && filtre != TakipOzetFiltre::Tumu;
                    let resp = tema::ozet_kart_cerceve(ui, secili, renk).show(ui, |ui| {
                        ui.vertical(|ui| {
                            ui.set_width((kart_genislik - 30.0).max(100.0));
                            ui.label(RichText::new(*baslik).size(12.0).weak().strong());
                            ui.add_space(2.0);
                            let deger_renk = renk.unwrap_or_else(|| ui.visuals().text_color());
                            ui.add(
                                egui::Label::new(
                                    RichText::new(deger)
                                        .size(17.0)
                                        .strong()
                                        .color(deger_renk),
                                )
                                .truncate(),
                            );
                            ui.add(
                                egui::Label::new(RichText::new(alt).size(11.5).weak()).truncate(),
                            );
                        });
                    });
                    let tikla = ui.interact(
                        resp.response.rect,
                        ui.scope_id().with(("ozet_kart", *baslik)),
                        Sense::click(),
                    );
                    if tikla.hovered() {
                        ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
                    }
                    if tikla.clicked() {
                        if filtre == TakipOzetFiltre::Tumu || self.takip_ozet_filtre == filtre {
                            self.takip_ozet_filtre = TakipOzetFiltre::Tumu;
                        } else {
                            self.takip_ozet_filtre = filtre;
                        }
                    }
                }
            });
        }
        ui.add_space(8.0);

        // Arama + Sıralama + Tümü/Hiçbiri çubuğu
        let pano_etiket = self.ayarlar.pano_siralama.etiket();
        let mut siralama_degisti = false;
        let mut degisen_secim: Vec<(String, bool)> = Vec::new();
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.horizontal_wrapped(|ui| {
                ui.spacing_mut().item_spacing.x = 10.0;
                Self::arama_kutusu_ciz(
                    ui,
                    &mut self.takip_arama,
                    "Ürün, site veya satıcı ara… (Ctrl+F)",
                    &mut self.arama_odak_iste,
                );
                ui.separator();
                if siralama_cubugu_ciz(
                    ui,
                    "takip_siralama_combo",
                    PanoSiralama::ACILIR_SECENEKLER,
                    &mut self.ayarlar.pano_siralama,
                    &mut self.ayarlar.pano_siralama_artan,
                    pano_etiket,
                ) {
                    siralama_degisti = true;
                }
            });
            ui.add_space(4.0);
            ui.horizontal_wrapped(|ui| {
                ui.label(RichText::new("Toplama dahil:").weak().size(13.0));
                if ui.small_button("☑ Tümü").clicked() {
                    degisen_secim.extend(pano.urunler.iter().map(|u| (u.id.clone(), true)));
                }
                if ui.small_button("☐ Hiçbiri").clicked() {
                    degisen_secim.extend(pano.urunler.iter().map(|u| (u.id.clone(), false)));
                }
                if self.takip_ozet_filtre != TakipOzetFiltre::Tumu || !self.takip_arama.is_empty() {
                    if ui.small_button("↺ Süzgeçleri temizle").clicked() {
                        self.takip_ozet_filtre = TakipOzetFiltre::Tumu;
                        self.takip_arama.clear();
                    }
                }
                ui.label(
                    RichText::new("· Σ kutucuğu ürünün en iyi fiyatını alttaki toplama ekler/çıkarır")
                        .weak()
                        .small(),
                );
            });
            // Takipten çıkar: tıklanan satır ya da Σ işaretli ürünler (onay penceresiyle, geri alınabilir)
            let tiklanan = self
                .takip_tiklanan_urun
                .as_ref()
                .and_then(|id| pano.urunler.iter().find(|u| &u.id == id))
                .map(|u| (u.id.clone(), u.ad.clone()));
            let isaretliler: Vec<(String, String)> = pano
                .urunler
                .iter()
                .filter(|u| u.toplamda.unwrap_or(u.sistemde))
                .map(|u| (u.id.clone(), u.ad.clone()))
                .collect();
            if tiklanan.is_some() || !isaretliler.is_empty() {
                ui.add_space(2.0);
                ui.horizontal_wrapped(|ui| {
                    ui.label(RichText::new("🗑 Takipten çıkar:").weak().size(13.0));
                    if let Some((id, ad)) = &tiklanan {
                        if ui
                            .small_button(format!("Seçili satır: {ad}"))
                            .on_hover_text("Tıkladığın satırı takip listesinden çıkar (Del)")
                            .clicked()
                        {
                            self.takip_cikar_onayi = Some(vec![(id.clone(), ad.clone())]);
                        }
                    }
                    if !isaretliler.is_empty()
                        && ui
                            .small_button(format!("Σ işaretli {} ürün", isaretliler.len()))
                            .on_hover_text("Σ kutucuğu işaretli ürünlerin hepsini takipten çıkar")
                            .clicked()
                    {
                        self.takip_cikar_onayi = Some(isaretliler.clone());
                    }
                    if self.takip_islem_bekliyor {
                        ui.spinner();
                    }
                });
            }
        });
        ui.add_space(8.0);

        let suzulmus_urunler: Vec<PanoUrun> = pano_urunleri_sirala(
            &pano.urunler,
            self.ayarlar.pano_siralama,
            self.ayarlar.pano_siralama_artan,
        )
        .into_iter()
        .filter(|u| {
            let kart_uygun = match self.takip_ozet_filtre {
                TakipOzetFiltre::Tumu => true,
                TakipOzetFiltre::Toplamda => u.toplamda.unwrap_or(u.sistemde),
                TakipOzetFiltre::AlarmAltinda => ta::alarm_ulasti(u),
                TakipOzetFiltre::TeyitBekleyen => u
                    .en_iyi
                    .as_ref()
                    .is_some_and(|e| e.teyit_durumu.as_deref() == Some("bekliyor")),
            };
            if !kart_uygun {
                return false;
            }
            let site = u.en_iyi.as_ref().map(|e| e.site.as_str()).unwrap_or("");
            let satici = u
                .en_iyi
                .as_ref()
                .and_then(|e| e.satici.as_deref())
                .unwrap_or("");
            arama_eslesir(&self.takip_arama, &[&u.ad, &u.id, site, satici])
        })
        .collect();

        let mut tiklanan_urun: Option<String> = None;
        let mut secilen_satir_urun: Option<String> = None;
        let mut tiklanan_taksit: Option<AcikTaksitPenceresi> = None;
        let mut alarm_duzenle: Option<(String, String, Option<f64>, AlarmDuzenlemeYeri)> = None;
        let mut cikarilacak: Option<(String, String)> = None;

        // Kalan yüksekliği tablo ve alttaki toplam kartı arasında paylaştır
        let secililer_sayisi = pano
            .urunler
            .iter()
            .filter(|u| u.toplamda.unwrap_or(u.sistemde))
            .count();
        let alt_kart_yuksekligi = if secililer_sayisi == 0 {
            92.0
        } else {
            (78.0 + (secililer_sayisi.min(5) as f32) * 20.0).min(190.0)
        };
        let takip_disi_payi = if pano.takip_disi.is_empty() { 0.0 } else { 34.0 };
        let alt_kart_yuksekligi = alt_kart_yuksekligi + takip_disi_payi;
        let tablo_yuksekligi = (ui.available_height() - alt_kart_yuksekligi - 12.0).max(200.0);

        if suzulmus_urunler.is_empty() {
            ui.allocate_ui(egui::vec2(ui.available_width(), tablo_yuksekligi), |ui| {
                Self::bos_durum_karti(
                    ui,
                    "🔎",
                    "Etkin süzgeç veya arama sorgusuyla eşleşen ürün bulunamadı.",
                );
            });
        } else {
            let gizle_ikincil = duzen.ikincil_sutunlari_gizle;
            let tablo_yuksekligi = (ui.available_height() - alt_kart_yuksekligi - 12.0).max(150.0);
            tema::kart_cerceve(ui)
                .inner_margin(egui::Margin::symmetric(10, 8))
                .show(ui, |ui| {
                    ui.allocate_ui(egui::vec2(ui.available_width(), tablo_yuksekligi), |ui| {
                        let kova = genislik_kovasi(ui);
                        let mut tablo = TableBuilder::new(ui)
                            .id_salt(("takip_tablosu", gizle_ikincil, kova))
                            .striped(true)
                            .resizable(!gizle_ikincil)
                            .sense(Sense::click())
                            .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                            .column(Column::exact(26.0)) // Toplama dahil kutucuğu
                            .column(Column::remainder().at_least(150.0).clip(true)); // Ürün

                        if !gizle_ikincil {
                            tablo = tablo.column(Column::initial(104.0).at_least(92.0).clip(true)); // Alarm fiyatı
                        }
                        tablo = tablo
                            .column(Column::exact(106.0)) // En İyi (Sepet)
                            .column(
                                Column::initial(if gizle_ikincil { 116.0 } else { 140.0 })
                                    .at_least(80.0)
                                    .clip(true),
                            ) // Site / Satıcı
                            .column(Column::exact(80.0)); // Taksit
                        if !gizle_ikincil {
                            tablo = tablo
                                .column(Column::initial(84.0).at_least(68.0).clip(true)) // Ortalama
                                .column(Column::initial(64.0).at_least(52.0).clip(true)) // Fark %
                                .column(Column::initial(64.0).at_least(52.0).clip(true)) // Teklif Adedi
                                .column(Column::initial(116.0).at_least(104.0).clip(true)); // Son Tarama
                        }

                        tablo
                            .header(28.0, |mut header| {
                                header.col(|ui| {
                                    ui.label(RichText::new("Σ").strong())
                                        .on_hover_text("İşaretli ürünlerin en iyi fiyatı alttaki toplama eklenir");
                                });
                                let mut basliklar: Vec<(&str, PanoSiralama)> =
                                    vec![("Ürün", PanoSiralama::UrunAdi)];
                                if !gizle_ikincil {
                                    basliklar.push(("Alarm fiyatı", PanoSiralama::HedefFiyat));
                                }
                                basliklar.push(("En İyi (Sepet)", PanoSiralama::EnIyiFiyat));
                                basliklar.push(("Site / Satıcı", PanoSiralama::SiteSatici));
                                basliklar.push(("Taksit", PanoSiralama::PesinTaksit));
                                if !gizle_ikincil {
                                    basliklar.push(("Ortalama", PanoSiralama::Ortalama));
                                    basliklar.push(("Fark %", PanoSiralama::FarkYuzde));
                                    basliklar.push(("Teklif", PanoSiralama::TeklifAdedi));
                                    basliklar.push(("Son Tarama", PanoSiralama::SonTarama));
                                }
                                for (baslik, kriter) in basliklar {
                                    header.col(|ui| {
                                        if siralanabilir_baslik(
                                            ui,
                                            baslik,
                                            kriter,
                                            &mut self.ayarlar.pano_siralama,
                                            &mut self.ayarlar.pano_siralama_artan,
                                        ) {
                                            siralama_degisti = true;
                                        }
                                    });
                                }
                            })
                            .body(|mut body| {
                                for urun in &suzulmus_urunler {
                                    let satir_secili =
                                        self.secili_urun_id.as_deref() == Some(urun.id.as_str());
                                    let en_iyi_url =
                                        urun.en_iyi.as_ref().and_then(|e| e.url.clone());

                                    body.row(28.0, |mut row| {
                                        row.set_selected(satir_secili);

                                        row.col(|ui| {
                                            let mut secili = urun.toplamda.unwrap_or(urun.sistemde);
                                            if ui
                                                .checkbox(&mut secili, "")
                                                .on_hover_text(if secili {
                                                    "Toplamda — çıkarmak için tıkla"
                                                } else {
                                                    "Toplamda değil — eklemek için tıkla"
                                                })
                                                .changed()
                                            {
                                                degisen_secim.push((urun.id.clone(), secili));
                                            }
                                        });

                                        row.col(|ui| {
                                            if let Some(kat) = &urun.kategori {
                                                let kat_ad = self.kategori_adi(kat);
                                                egui::Frame::NONE
                                                    .fill(ui.visuals().faint_bg_color)
                                                    .corner_radius(4.0)
                                                    .inner_margin(egui::Margin::symmetric(4, 0))
                                                    .show(ui, |ui| {
                                                        ui.label(RichText::new(&kat_ad).weak().size(11.0));
                                                    })
                                                    .response
                                                    .on_hover_text(format!("Tarama grubu: {kat_ad}"));
                                            }
                                            let resp = ui
                                                .add(
                                                    egui::Label::new(
                                                        RichText::new(&urun.ad).strong(),
                                                    )
                                                    .truncate()
                                                    .sense(Sense::click()),
                                                )
                                                .on_hover_text(format!(
                                                    "{}\nTıkla: seç · Çift tıkla: detay aç · Sağ tık: menü",
                                                    urun.ad
                                                ));
                                            if resp.clicked() {
                                                secilen_satir_urun = Some(urun.id.clone());
                                            }
                                            if resp.double_clicked() {
                                                tiklanan_urun = Some(urun.id.clone());
                                            }
                                            resp.context_menu(|ui| {
                                                if ui.button("🔎 Detay").clicked() {
                                                    tiklanan_urun = Some(urun.id.clone());
                                                    ui.close();
                                                }
                                                let url_var = en_iyi_url
                                                    .as_deref()
                                                    .is_some_and(|u| !u.is_empty());
                                                if ui
                                                    .add_enabled(url_var, egui::Button::new("🌐 Sitede aç"))
                                                    .clicked()
                                                {
                                                    if let Some(u) = &en_iyi_url {
                                                        baglanti_ac(u);
                                                    }
                                                    ui.close();
                                                }
                                                if ui
                                                    .add_enabled(
                                                        url_var,
                                                        egui::Button::new("📋 Linki kopyala"),
                                                    )
                                                    .clicked()
                                                {
                                                    if let Some(u) = &en_iyi_url {
                                                        ctx.copy_text(u.clone());
                                                    }
                                                    ui.close();
                                                }
                                                ui.separator();
                                                if ui.button("🔔 Alarm fiyatı…").clicked() {
                                                    alarm_duzenle = Some((
                                                        urun.id.clone(),
                                                        urun.ad.clone(),
                                                        ta::urun_alarm_fiyati(urun),
                                                        AlarmDuzenlemeYeri::Pencere,
                                                    ));
                                                    ui.close();
                                                }
                                                if ui.button("🗑 Takipten çıkar").clicked() {
                                                    cikarilacak = Some((urun.id.clone(), urun.ad.clone()));
                                                    ui.close();
                                                }
                                            });
                                        });

                                        if !gizle_ikincil {
                                            row.col(|ui| {
                                                let duzenleniyor = self.alarm_duzenleme.as_ref().is_some_and(|d| {
                                                    d.urun_id == urun.id && d.yer == AlarmDuzenlemeYeri::TakipSatiri
                                                });
                                                if duzenleniyor {
                                                    let g = ui.available_width();
                                                    self.alarm_kutusu_ciz(ctx, ui, g);
                                                    return;
                                                }
                                                let alarm = ta::urun_alarm_fiyati(urun);
                                                let s = alarm.map(bicim::tl).unwrap_or_else(|| "＋ alarm".to_string());
                                                ui.with_layout(
                                                    egui::Layout::right_to_left(egui::Align::Center),
                                                    |ui| {
                                                        let metin = if alarm.is_some() {
                                                            RichText::new(s).monospace()
                                                        } else {
                                                            RichText::new(s).weak().size(12.0)
                                                        };
                                                        let r = ui
                                                            .add(egui::Label::new(metin).sense(Sense::click()))
                                                            .on_hover_text(
                                                                "Alarm fiyatı: en iyi fiyat buna eşit ya da altına inince Telegram'a anında mesaj gider.\nTıkla: düzenle",
                                                            );
                                                        if r.hovered() {
                                                            ui.ctx().set_cursor_icon(egui::CursorIcon::Text);
                                                        }
                                                        if r.clicked() {
                                                            secilen_satir_urun = Some(urun.id.clone());
                                                            alarm_duzenle = Some((
                                                                urun.id.clone(),
                                                                urun.ad.clone(),
                                                                alarm,
                                                                AlarmDuzenlemeYeri::TakipSatiri,
                                                            ));
                                                        }
                                                    },
                                                );
                                            });
                                        }

                                        row.col(|ui| {
                                            let s = urun
                                                .en_iyi
                                                .as_ref()
                                                .and_then(|e| e.sepet_fiyati)
                                                .map(bicim::tl)
                                                .unwrap_or_else(|| "—".to_string());
                                            let alarm_ulasti = ta::alarm_ulasti(urun);
                                            ui.with_layout(
                                                egui::Layout::right_to_left(egui::Align::Center),
                                                |ui| {
                                                    let mut metin = RichText::new(s).strong().monospace();
                                                    if alarm_ulasti {
                                                        metin = metin.color(p.basari);
                                                    }
                                                    let resp = ui.add(
                                                        egui::Label::new(metin).sense(Sense::click()),
                                                    );
                                                    if resp.clicked() {
                                                        secilen_satir_urun = Some(urun.id.clone());
                                                    }
                                                    if resp.double_clicked() {
                                                        tiklanan_urun = Some(urun.id.clone());
                                                    }
                                                    if alarm_ulasti {
                                                        ui.label(RichText::new("🔔").color(p.basari)).on_hover_text(
                                                            "Alarm fiyatına ulaştı — Telegram'a bildirildi",
                                                        );
                                                    }
                                                },
                                            );
                                        });

                                        row.col(|ui| {
                                            let tam = if let Some(en_iyi) = &urun.en_iyi {
                                                match &en_iyi.satici {
                                                    Some(sat) if !sat.is_empty() => {
                                                        format!("{} / {}", en_iyi.site, sat)
                                                    }
                                                    _ => en_iyi.site.clone(),
                                                }
                                            } else {
                                                "—".to_string()
                                            };
                                            let r = ui
                                                .add(
                                                    egui::Label::new(&tam)
                                                        .truncate()
                                                        .sense(Sense::click()),
                                                )
                                                .on_hover_text(&tam);
                                            if r.clicked() {
                                                secilen_satir_urun = Some(urun.id.clone());
                                            }
                                            if r.double_clicked() {
                                                tiklanan_urun = Some(urun.id.clone());
                                            }
                                        });

                                        row.col(|ui| {
                                            if let Some(pencere) =
                                                AcikTaksitPenceresi::from_pano_urun(urun)
                                            {
                                                if taksit_ozet_hucre_ciz(ui, &pencere.taksit) {
                                                    tiklanan_taksit = Some(pencere);
                                                }
                                            } else {
                                                ui.label("—");
                                            }
                                        });

                                        if !gizle_ikincil {
                                            row.col(|ui| {
                                                let s = urun
                                                    .ozet
                                                    .ortalama
                                                    .map(bicim::tl)
                                                    .unwrap_or_else(|| "—".to_string());
                                                ui.with_layout(
                                                    egui::Layout::right_to_left(egui::Align::Center),
                                                    |ui| {
                                                        let r = ui.add(
                                                            egui::Label::new(
                                                                RichText::new(s).monospace(),
                                                            )
                                                            .sense(Sense::click()),
                                                        );
                                                        if r.clicked() {
                                                            secilen_satir_urun = Some(urun.id.clone());
                                                        }
                                                        if r.double_clicked() {
                                                            tiklanan_urun = Some(urun.id.clone());
                                                        }
                                                    },
                                                );
                                            });

                                            row.col(|ui| {
                                                ui.with_layout(
                                                    egui::Layout::right_to_left(egui::Align::Center),
                                                    |ui| {
                                                        if let (Some(sepet), Some(ort)) = (
                                                            urun.en_iyi
                                                                .as_ref()
                                                                .and_then(|e| e.sepet_fiyati),
                                                            urun.ozet.ortalama,
                                                        ) {
                                                            if ort > 0.0 {
                                                                let fark = (sepet - ort) / ort * 100.0;
                                                                let renk = if fark < 0.0 {
                                                                    p.basari
                                                                } else {
                                                                    p.hata
                                                                };
                                                                let r = ui.add(
                                                                    egui::Label::new(
                                                                        RichText::new(bicim::yuzde(
                                                                            fark,
                                                                        ))
                                                                        .color(renk)
                                                                        .monospace()
                                                                        .strong(),
                                                                    )
                                                                    .sense(Sense::click()),
                                                                );
                                                                if r.clicked() {
                                                                    secilen_satir_urun =
                                                                        Some(urun.id.clone());
                                                                }
                                                                if r.double_clicked() {
                                                                    tiklanan_urun =
                                                                        Some(urun.id.clone());
                                                                }
                                                            } else {
                                                                ui.label("—");
                                                            }
                                                        } else {
                                                            ui.label("—");
                                                        }
                                                    },
                                                );
                                            });

                                            row.col(|ui| {
                                                ui.with_layout(
                                                    egui::Layout::right_to_left(egui::Align::Center),
                                                    |ui| {
                                                        let r = ui.add(
                                                            egui::Label::new(
                                                                RichText::new(
                                                                    urun.ozet.adet.to_string(),
                                                                )
                                                                .monospace(),
                                                            )
                                                            .sense(Sense::click()),
                                                        );
                                                        if r.clicked() {
                                                            secilen_satir_urun = Some(urun.id.clone());
                                                        }
                                                        if r.double_clicked() {
                                                            tiklanan_urun = Some(urun.id.clone());
                                                        }
                                                    },
                                                );
                                            });

                                            row.col(|ui| {
                                                let s = urun
                                                    .son_tarama
                                                    .as_deref()
                                                    .map(bicim::iso_tarih)
                                                    .unwrap_or_else(|| "—".to_string());
                                                let r = ui.add(
                                                    egui::Label::new(RichText::new(s).size(12.5))
                                                        .sense(Sense::click()),
                                                );
                                                if r.clicked() {
                                                    secilen_satir_urun = Some(urun.id.clone());
                                                }
                                                if r.double_clicked() {
                                                    tiklanan_urun = Some(urun.id.clone());
                                                }
                                            });
                                        }

                                        let resp = row.response();
                                        if resp.clicked() {
                                            secilen_satir_urun = Some(urun.id.clone());
                                        }
                                        if resp.double_clicked() {
                                            tiklanan_urun = Some(urun.id.clone());
                                        }
                                        resp.context_menu(|ui| {
                                            if ui.button("🔎 Detay").clicked() {
                                                tiklanan_urun = Some(urun.id.clone());
                                                ui.close();
                                            }
                                            let url_var = en_iyi_url
                                                .as_deref()
                                                .is_some_and(|u| !u.is_empty());
                                            if ui
                                                .add_enabled(url_var, egui::Button::new("🌐 Sitede aç"))
                                                .clicked()
                                            {
                                                if let Some(u) = &en_iyi_url {
                                                    baglanti_ac(u);
                                                }
                                                ui.close();
                                            }
                                            if ui
                                                .add_enabled(
                                                    url_var,
                                                    egui::Button::new("📋 Linki kopyala"),
                                                )
                                                .clicked()
                                            {
                                                if let Some(u) = &en_iyi_url {
                                                    ctx.copy_text(u.clone());
                                                }
                                                ui.close();
                                            }
                                            ui.separator();
                                            if ui.button("🔔 Alarm fiyatı…").clicked() {
                                                alarm_duzenle = Some((
                                                    urun.id.clone(),
                                                    urun.ad.clone(),
                                                    ta::urun_alarm_fiyati(urun),
                                                    AlarmDuzenlemeYeri::Pencere,
                                                ));
                                                ui.close();
                                            }
                                            if ui.button("🗑 Takipten çıkar").clicked() {
                                                cikarilacak = Some((urun.id.clone(), urun.ad.clone()));
                                                ui.close();
                                            }
                                        });
                                    });
                                }
                            });
                    });
                });
        }

        if let Some(pencere) = tiklanan_taksit {
            self.acik_taksit_penceresi = Some(pencere);
        }

        if let Some(secilen) = secilen_satir_urun {
            self.takip_tiklanan_urun = Some(secilen.clone());
            self.secili_urun_id = Some(secilen);
        }
        if let Some((id, ad, alarm, yer)) = alarm_duzenle {
            self.alarm_duzenleme = Some(AlarmDuzenleme {
                urun_id: id,
                urun_ad: ad,
                metin: ta::alarm_kutusu_metni(alarm),
                yer,
                odak_istendi: false,
                hata: None,
            });
        }
        if let Some(c) = cikarilacak {
            self.takip_cikar_onayi = Some(vec![c]);
        }
        // Del: tıklanan satırı takipten çıkar (yazı kutusu odakta değilse)
        if !ctx.egui_wants_keyboard_input()
            && self.takip_cikar_onayi.is_none()
            && ctx.input(|i| i.key_pressed(egui::Key::Delete))
        {
            if let Some(u) = self
                .takip_tiklanan_urun
                .as_ref()
                .and_then(|id| pano.urunler.iter().find(|u| &u.id == id))
            {
                self.takip_cikar_onayi = Some(vec![(u.id.clone(), u.ad.clone())]);
            }
        }

        self.toplam_secimi_degistir(ctx, degisen_secim);

        if siralama_degisti {
            let _ = self.ayarlar.kaydet();
        }

        if let Some(urun_id) = tiklanan_urun {
            self.aktif_sekme = Sekme::UrunDetayi;
            self.urun_detay_yukle(ctx, urun_id);
        }

        // Takipten çıkarılanlar (geri alınabilir)
        if !pano.takip_disi.is_empty() {
            ui.add_space(6.0);
            let mut geri_al: Option<(String, String)> = None;
            egui::CollapsingHeader::new(
                RichText::new(format!("Takipten çıkarılanlar ({})", pano.takip_disi.len())).weak(),
            )
            .id_salt("takip_disi_listesi")
            .default_open(false)
            .show(ui, |ui| {
                egui::ScrollArea::vertical().id_salt("takip_disi_kaydir").max_height(120.0).show(ui, |ui| {
                    for o in &pano.takip_disi {
                        ui.horizontal(|ui| {
                            if ui.small_button("↩ Geri al").clicked() {
                                geri_al = Some((o.id.clone(), o.ad.clone()));
                            }
                            ui.label(&o.ad);
                            if let Some(z) = &o.zaman {
                                ui.label(RichText::new(bicim::iso_tarih(z)).weak().small());
                            }
                        });
                    }
                });
            });
            if let Some(g) = geri_al {
                self.takip_islem_gonder(ctx, false, vec![g]);
            }
        }

        self.takip_cikar_onayi_ciz(ctx);
        self.alarm_penceresi_ciz(ctx);

        // Altta: işaretli ürünlerin toplamı (hangi ürünlerin toplandığı açıkça yazılır)
        ui.add_space(10.0);
        let Some(pano) = self.pano.as_ref() else { return };
        let secililer: Vec<&PanoUrun> = pano
            .urunler
            .iter()
            .filter(|u| u.toplamda.unwrap_or(u.sistemde))
            .collect();
        let toplam = pano
            .sistem_toplami
            .clone()
            .unwrap_or_else(|| yerel_toplam_hesapla(&pano.urunler));
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal_wrapped(|ui| {
                ui.label(
                    RichText::new(format!("Seçili ürünlerin toplamı ({} ürün):", secililer.len()))
                        .strong()
                        .size(16.0),
                );
                ui.label(
                    RichText::new(bicim::tl(toplam.tutar))
                        .strong()
                        .size(16.0)
                        .color(p.basari),
                );
                if !toplam.eksik.is_empty() {
                    ui.separator();
                    ui.label(
                        RichText::new(format!(
                            "⚠ Fiyatı olmayan ({}): {}",
                            toplam.eksik.len(),
                            toplam.eksik.join(", ")
                        ))
                        .color(p.uyari),
                    );
                }
            });
            if secililer.is_empty() {
                ui.label(
                    RichText::new("Hiç ürün işaretli değil — tablodaki Σ kutucuklarından seç.")
                        .weak(),
                );
            } else {
                ui.add_space(4.0);
                egui::ScrollArea::vertical()
                    .id_salt("secili_urun_toplam_scroll")
                    .max_height(105.0)
                    .show(ui, |ui| {
                        for u in &secililer {
                            let fiyat = u.en_iyi.as_ref().and_then(|e| e.sepet_fiyati);
                            ui.horizontal(|ui| {
                                ui.label(RichText::new("•").weak());
                                ui.add(egui::Label::new(&u.ad).truncate())
                                    .on_hover_text(&u.ad);
                                ui.with_layout(
                                    egui::Layout::right_to_left(egui::Align::Center),
                                    |ui| {
                                        ui.label(
                                            RichText::new(
                                                fiyat
                                                    .map(bicim::tl)
                                                    .unwrap_or_else(|| "fiyat yok".to_string()),
                                            )
                                            .monospace(),
                                        );
                                    },
                                );
                            });
                        }
                    });
            }
        });
    }

    /// "… takip listesinden çıkarılsın mı?" onay penceresi
    fn takip_cikar_onayi_ciz(&mut self, ctx: &egui::Context) {
        let Some(secim) = self.takip_cikar_onayi.clone() else { return };
        let adlar: Vec<String> = secim.iter().map(|(_, ad)| ad.clone()).collect();
        let mut kapat = false;
        let mut onay = false;
        let modal = egui::Modal::new(egui::Id::unique("takip_cikar_onayi")).show(ctx, |ui| {
            ui.set_max_width(420.0);
            ui.label(RichText::new("🗑 Takipten çıkar").strong().size(16.0));
            ui.add_space(6.0);
            ui.label(ta::takip_cikar_onay_metni(&adlar));
            ui.add_space(10.0);
            ui.horizontal(|ui| {
                if ui.button(RichText::new("🗑 Çıkar").strong()).clicked() {
                    onay = true;
                }
                if ui.button("Vazgeç").clicked() {
                    kapat = true;
                }
            });
        });
        if modal.should_close() {
            kapat = true;
        }
        if onay {
            self.takip_cikar_onayi = None;
            self.takip_islem_gonder(ctx, true, secim);
        } else if kapat {
            self.takip_cikar_onayi = None;
        }
    }

    /// Dar ekran / sağ tık menüsünden açılan alarm fiyatı penceresi
    fn alarm_penceresi_ciz(&mut self, ctx: &egui::Context) {
        let Some(d) = self.alarm_duzenleme.clone() else { return };
        if d.yer != AlarmDuzenlemeYeri::Pencere {
            return;
        }
        let mut kaydet = false;
        let mut kaldir = false;
        let modal = egui::Modal::new(egui::Id::unique("alarm_penceresi")).show(ctx, |ui| {
            ui.set_max_width(380.0);
            ui.label(RichText::new("🔔 Alarm fiyatı").strong().size(16.0));
            ui.label(RichText::new(&d.urun_ad).weak());
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                self.alarm_kutusu_ciz(ctx, ui, 160.0);
                ui.label("TL");
            });
            ui.label(
                RichText::new("En iyi fiyat buna eşit ya da altına inince Telegram'a anında mesaj gider.")
                    .weak()
                    .small(),
            );
            ui.add_space(8.0);
            ui.horizontal(|ui| {
                kaydet = ui.button("💾 Kaydet").clicked();
                kaldir = ui.button("Alarmı kaldır").clicked();
                if ui.button("Vazgeç").clicked() {
                    self.alarm_duzenleme = None;
                }
            });
        });
        if modal.should_close() {
            self.alarm_duzenleme = None;
        }
        if kaydet || kaldir {
            if let Some(d) = self.alarm_duzenleme.take() {
                let girdi = if kaldir { Ok(None) } else { ta::alarm_girdisi_ayristir(&d.metin) };
                match girdi {
                    Ok(f) => self.alarm_gonder(ctx, d.urun_id, d.urun_ad, f),
                    Err(e) => {
                        self.toast_ekle(ToastTuru::Hata, e.clone());
                        self.alarm_duzenleme = Some(AlarmDuzenleme { hata: Some(e), ..d });
                    }
                }
            }
        }
    }

    // ======================= 2. Ekran: Ürün Detayı (/urunler/{id}/detay) =======================

    fn ekran_urun_detayi(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let duzen = duyarli_duzen_hesapla(ctx.input(|i| i.content_rect().width()));
        let p = tema::palet(ui.visuals());

        // Ürün seçici üst satır
        ui.horizontal_wrapped(|ui| {
            if ui.button("← Takip Listesine Dön").clicked() {
                self.aktif_sekme = Sekme::TakipListesi;
            }
            ui.separator();
            if let Some(pano) = &self.pano {
                let secili_etiket = self
                    .secili_urun_id
                    .as_ref()
                    .and_then(|id| pano.urunler.iter().find(|u| &u.id == id))
                    .map(|u| u.ad.clone())
                    .unwrap_or_else(|| "Ürün Seçin".to_string());

                let mut yeni_secim: Option<String> = None;
                egui::ComboBox::from_label("Ürün")
                    .selected_text(secili_etiket)
                    .show_ui(ui, |ui| {
                        for u in &pano.urunler {
                            if ui
                                .selectable_label(
                                    self.secili_urun_id.as_deref() == Some(u.id.as_str()),
                                    &u.ad,
                                )
                                .clicked()
                            {
                                yeni_secim = Some(u.id.clone());
                            }
                        }
                    });
                if let Some(id) = yeni_secim {
                    self.urun_detay_yukle(ctx, id);
                }
            }

            if let Some(id) = self.secili_urun_id.clone() {
                if ui.button("🔍 Şimdi Tara").clicked() {
                    self.urun_tara_gonder(ctx, id);
                }
            }

            if self.detay_yukleniyor {
                ui.spinner();
                ui.label(RichText::new("Detay yükleniyor…").weak());
            }
        });

        ui.add_space(6.0);

        let Some(detay) = self.urun_detay.clone() else {
            if self.detay_yukleniyor || self.yukleniyor {
                Self::yukleniyor_karti(ui, "Ürün detayı ve teklifler yükleniyor…");
            } else {
                Self::bos_durum_karti(
                    ui,
                    "🔎",
                    "Henüz seçili ürün detayı yok; Takip Listesi'nden bir ürün seçin.",
                );
            }
            return;
        };

        // Üst özet kartı: Ürün adı, hedef, ortalama/medyan/adet
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal_wrapped(|ui| {
                ui.label(RichText::new(&detay.urun.ad).strong().size(20.0));
                if let Some(kat) = &detay.urun.kategori {
                    ui.label(RichText::new(format!("[{}]", kat)).weak());
                }
                ui.separator();
                let alarm = detay.urun.alarm_fiyat.or(detay.urun.hedef_fiyat).filter(|f| *f > 0.0);
                let duzenleniyor = self.alarm_duzenleme.as_ref().is_some_and(|d| {
                    d.urun_id == detay.urun.id && d.yer == AlarmDuzenlemeYeri::Detay
                });
                if duzenleniyor {
                    ui.label(RichText::new("🔔 Alarm fiyatı:").strong().color(p.uyari));
                    self.alarm_kutusu_ciz(ctx, ui, 110.0);
                    ui.label(RichText::new("Enter: kaydet · Esc: vazgeç").weak().small());
                } else {
                    let r = ui
                        .add(
                            egui::Label::new(
                                RichText::new(format!(
                                    "🔔 Alarm fiyatı: {} ✎",
                                    alarm.map(bicim::tl).unwrap_or_else(|| "yok".to_string())
                                ))
                                .strong()
                                .color(p.uyari),
                            )
                            .sense(Sense::click()),
                        )
                        .on_hover_text(
                            "En iyi fiyat buna eşit ya da altına inince Telegram'a anında mesaj gider. Tıkla: düzenle",
                        );
                    if r.hovered() {
                        ui.ctx().set_cursor_icon(egui::CursorIcon::Text);
                    }
                    if r.clicked() {
                        self.alarm_duzenleme = Some(AlarmDuzenleme {
                            urun_id: detay.urun.id.clone(),
                            urun_ad: detay.urun.ad.clone(),
                            metin: ta::alarm_kutusu_metni(alarm),
                            yer: AlarmDuzenlemeYeri::Detay,
                            odak_istendi: false,
                            hata: None,
                        });
                    }
                }
                ui.separator();
                ui.label(format!(
                    "Ortalama: {}",
                    detay
                        .ozet
                        .ortalama
                        .map(bicim::tl)
                        .unwrap_or_else(|| "—".to_string())
                ));
                ui.separator();
                ui.label(format!(
                    "Medyan: {}",
                    detay
                        .ozet
                        .medyan
                        .map(bicim::tl)
                        .unwrap_or_else(|| "—".to_string())
                ));
                ui.separator();
                ui.label(format!("Teklif Adedi: {}", detay.ozet.adet));
            });

            if let Some(kurallar) = &detay.urun.kurallar {
                if !kurallar.dahil.is_empty() || !kurallar.haric.is_empty() {
                    ui.add_space(4.0);
                    egui::CollapsingHeader::new(
                        RichText::new(format!(
                            "Eşleşme kuralları (dahil: {}, hariç: {})",
                            kurallar.dahil.len(),
                            kurallar.haric.len()
                        ))
                        .small()
                        .weak(),
                    )
                    .id_salt(format!("eslesme_kurallari_{}", detay.urun.id))
                    .default_open(false)
                    .show(ui, |ui| {
                        ui.horizontal_wrapped(|ui| {
                            if !kurallar.dahil.is_empty() {
                                ui.label(
                                    RichText::new(format!(
                                        "Dahil: {}",
                                        kurallar.dahil.join(", ")
                                    ))
                                    .small()
                                    .color(p.basari),
                                );
                            }
                            if !kurallar.haric.is_empty() {
                                ui.label(
                                    RichText::new(format!(
                                        "Hariç: {}",
                                        kurallar.haric.join(", ")
                                    ))
                                    .small()
                                    .color(p.hata),
                                );
                            }
                        });
                    });
                }
            }
        });

        ui.add_space(8.0);

        // Süzgeçler, Arama ve Sıralama Çubuğu
        let mut siteler_listesi: Vec<String> = detay
            .teklifler
            .iter()
            .map(|t| t.site.clone())
            .collect::<HashSet<_>>()
            .into_iter()
            .collect();
        siteler_listesi.sort();

        let mut siralama_degisti = false;

        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal_wrapped(|ui| {
                ui.spacing_mut().item_spacing.x = 10.0;
                Self::arama_kutusu_ciz(
                    ui,
                    &mut self.detay_arama,
                    "Site, satıcı veya başlık ara… (Ctrl+F)",
                    &mut self.arama_odak_iste,
                );
                ui.separator();
                ui.checkbox(&mut self.suzgec_yalniz_teyitli, "Yalnız teyitli");
                ui.checkbox(&mut self.suzgec_yalniz_stokta, "Yalnız stokta");

                egui::ComboBox::from_label("Site")
                    .selected_text(&self.suzgec_site)
                    .show_ui(ui, |ui| {
                        ui.selectable_value(&mut self.suzgec_site, "Tümü".to_string(), "Tümü");
                        for s in &siteler_listesi {
                            ui.selectable_value(&mut self.suzgec_site, s.clone(), s);
                        }
                    });

                ui.separator();
                let teklif_etiket = self.teklif_siralama.etiket();
                if siralama_cubugu_ciz(
                    ui,
                    "urun_detay_siralama_combo",
                    TeklifSiralama::ACILIR_SECENEKLER,
                    &mut self.teklif_siralama,
                    &mut self.siralama_artan,
                    teklif_etiket,
                ) {
                    siralama_degisti = true;
                }
            });
        });

        ui.add_space(8.0);

        // Teklifleri süz ve veri kopyası üzerinde sırala
        let suzulmus_ham: Vec<DetayTeklif> = detay
            .teklifler
            .iter()
            .filter(|t| {
                if self.suzgec_yalniz_teyitli
                    && t.teyit_durumu.as_deref() != Some("teyitli")
                {
                    return false;
                }
                if self.suzgec_yalniz_stokta && t.stok == Some(false) {
                    return false;
                }
                if self.suzgec_site != "Tümü" && t.site != self.suzgec_site {
                    return false;
                }
                let satici = t.satici.as_deref().unwrap_or("");
                let baslik = t.baslik.as_deref().unwrap_or("");
                arama_eslesir(&self.detay_arama, &[&t.site, satici, baslik])
            })
            .cloned()
            .collect();

        let suzulmus = teklifleri_sirala(&suzulmus_ham, self.teklif_siralama, self.siralama_artan);
        let mut tiklanan_taksit: Option<AcikTaksitPenceresi> = None;
        let gizle_ikincil = duzen.ikincil_sutunlari_gizle;

        egui::ScrollArea::vertical().show(ui, |ui| {
            if suzulmus.is_empty() {
                Self::bos_durum_karti(
                    ui,
                    "🔎",
                    "Bu ürün için süzgeçlere uyan teklif bulunamadı.",
                );
            } else {
                tema::kart_cerceve(ui)
                    .inner_margin(egui::Margin::symmetric(10, 8))
                    .show(ui, |ui| {
                        ui.set_min_width(ui.available_width());
                        let tablo_y = (ui.available_height() * 0.45).clamp(220.0, 340.0);
                        ui.allocate_ui(egui::vec2(ui.available_width(), tablo_y), |ui| {
                            let kova = genislik_kovasi(ui);
                            let mut tablo = TableBuilder::new(ui)
                                .id_salt(("teklif_tablosu", kova))
                                .striped(true)
                                .resizable(true)
                                .sense(Sense::click())
                                .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                                .column(Column::initial(92.0).at_least(64.0).clip(true)) // Site
                                .column(Column::initial(104.0).at_least(70.0).clip(true)) // Satıcı
                                .column(Column::remainder().at_least(150.0).clip(true)) // Başlık
                                .column(Column::initial(114.0).at_least(88.0).clip(true)); // Sepet

                            if !gizle_ikincil {
                                tablo = tablo
                                    .column(Column::initial(80.0).at_least(60.0).clip(true)) // Liste
                                    .column(Column::initial(64.0).at_least(50.0).clip(true)); // İndirim %
                            }
                            tablo = tablo
                                .column(Column::initial(94.0).at_least(72.0).clip(true)) // Taksit
                                .column(Column::initial(42.0).at_least(34.0).clip(true)); // Stok
                            if !gizle_ikincil {
                                tablo = tablo.column(Column::initial(50.0).at_least(38.0).clip(true)); // Katman
                            }
                            tablo = tablo.column(Column::initial(74.0).at_least(56.0).clip(true)); // Teyit
                            if !gizle_ikincil {
                                tablo = tablo.column(Column::initial(112.0).at_least(90.0).clip(true)); // Gözlem
                            }
                            tablo = tablo.column(Column::initial(84.0).at_least(72.0).clip(true)); // İşlem

                            tablo
                                .header(28.0, |mut header| {
                                    let mut sutunlar: Vec<(&str, TeklifSiralama)> = vec![
                                        ("Site", TeklifSiralama::Site),
                                        ("Satıcı", TeklifSiralama::Satici),
                                        ("Başlık", TeklifSiralama::Baslik),
                                        ("Sepet", TeklifSiralama::SepetFiyati),
                                    ];
                                    if !gizle_ikincil {
                                        sutunlar.push(("Liste", TeklifSiralama::ListeFiyati));
                                        sutunlar.push(("İndirim %", TeklifSiralama::IndirimYuzdesi));
                                    }
                                    sutunlar.push(("Taksit", TeklifSiralama::PesinTaksit));
                                    sutunlar.push(("Stok", TeklifSiralama::Stok));
                                    if !gizle_ikincil {
                                        sutunlar.push(("Katman", TeklifSiralama::Katman));
                                    }
                                    sutunlar.push(("Teyit", TeklifSiralama::Teyit));
                                    if !gizle_ikincil {
                                        sutunlar.push(("Gözlem", TeklifSiralama::GozlemZamani));
                                    }

                                    for (baslik, kriter) in sutunlar {
                                        header.col(|ui| {
                                            if siralanabilir_baslik(
                                                ui,
                                                baslik,
                                                kriter,
                                                &mut self.teklif_siralama,
                                                &mut self.siralama_artan,
                                            ) {
                                                siralama_degisti = true;
                                            }
                                        });
                                    }
                                    header.col(|ui| {
                                        ui.strong("İşlem");
                                    });
                                })
                                .body(|mut body| {
                                    for teklif in &suzulmus {
                                        let secili = self.secili_teklif_id == teklif.teklif_id
                                            && teklif.teklif_id.is_some();
                                        let teklif_url = teklif.url.clone();

                                        body.row(28.0, |mut row| {
                                            row.set_selected(secili);

                                            row.col(|ui| {
                                                let mut rt = RichText::new(&teklif.site);
                                                if secili {
                                                    rt = rt.strong().color(p.vurgu);
                                                }
                                                let r = ui
                                                    .add(
                                                        egui::Label::new(rt)
                                                            .truncate()
                                                            .sense(Sense::click()),
                                                    )
                                                    .on_hover_text(&teklif.site);
                                                if r.clicked() {
                                                    self.secili_teklif_id = teklif.teklif_id;
                                                }
                                            });
                                            row.col(|ui| {
                                                let tam_sat =
                                                    teklif.satici.as_deref().unwrap_or("—");
                                                let r = ui
                                                    .add(
                                                        egui::Label::new(tam_sat)
                                                            .truncate()
                                                            .sense(Sense::click()),
                                                    )
                                                    .on_hover_text(tam_sat);
                                                if r.clicked() {
                                                    self.secili_teklif_id = teklif.teklif_id;
                                                }
                                            });
                                            row.col(|ui| {
                                                let tam_baslik =
                                                    teklif.baslik.as_deref().unwrap_or("—");
                                                let r = ui
                                                    .add(
                                                        egui::Label::new(tam_baslik)
                                                            .truncate()
                                                            .sense(Sense::click()),
                                                    )
                                                    .on_hover_text(tam_baslik);
                                                if r.clicked() {
                                                    self.secili_teklif_id = teklif.teklif_id;
                                                }
                                                if r.double_clicked() {
                                                    if let Some(u) = &teklif_url {
                                                        if !u.is_empty() {
                                                            baglanti_ac(u);
                                                        }
                                                    }
                                                }
                                            });
                                            row.col(|ui| {
                                                let s = teklif
                                                    .sepet_fiyati
                                                    .map(bicim::tl)
                                                    .unwrap_or_else(|| "—".to_string());
                                                ui.with_layout(
                                                    egui::Layout::right_to_left(egui::Align::Center),
                                                    |ui| {
                                                        ui.label(
                                                            RichText::new(s).strong().monospace(),
                                                        );
                                                        if teklif.hedef_altinda {
                                                            hedef_altinda_rozeti_ciz(ui);
                                                        }
                                                    },
                                                );
                                            });

                                            if !gizle_ikincil {
                                                row.col(|ui| {
                                                    let s = teklif
                                                        .liste_fiyati
                                                        .map(bicim::tl)
                                                        .unwrap_or_else(|| "—".to_string());
                                                    ui.with_layout(
                                                        egui::Layout::right_to_left(
                                                            egui::Align::Center,
                                                        ),
                                                        |ui| {
                                                            ui.label(RichText::new(s).monospace());
                                                        },
                                                    );
                                                });
                                                row.col(|ui| {
                                                    ui.with_layout(
                                                        egui::Layout::right_to_left(
                                                            egui::Align::Center,
                                                        ),
                                                        |ui| {
                                                            if let Some(ind) =
                                                                teklif_indirim_yuzdesi(teklif)
                                                            {
                                                                let renk = if ind > 0.0 {
                                                                    p.basari
                                                                } else {
                                                                    ui.visuals().text_color()
                                                                };
                                                                ui.label(
                                                                    RichText::new(bicim::yuzde(
                                                                        -ind,
                                                                    ))
                                                                    .color(renk)
                                                                    .monospace(),
                                                                );
                                                            } else {
                                                                ui.label("—");
                                                            }
                                                        },
                                                    );
                                                });
                                            }

                                            row.col(|ui| {
                                                let taksit =
                                                    teklif.taksit.clone().unwrap_or_default();
                                                if taksit_ozet_hucre_ciz(ui, &taksit) {
                                                    tiklanan_taksit = Some(
                                                        AcikTaksitPenceresi::from_detay_teklif(
                                                            &detay.urun.ad,
                                                            teklif,
                                                        ),
                                                    );
                                                }
                                            });
                                            row.col(|ui| match teklif.stok {
                                                Some(true) => {
                                                    ui.label(RichText::new("Var").color(p.basari));
                                                }
                                                Some(false) => {
                                                    ui.label(RichText::new("Yok").color(p.hata));
                                                }
                                                None => {
                                                    ui.label(RichText::new("?").weak())
                                                        .on_hover_text(
                                                            "Kaynak stok bilgisi vermiyor (arama listesi); teyitte sitede kontrol edilir",
                                                        );
                                                }
                                            });

                                            if !gizle_ikincil {
                                                row.col(|ui| {
                                                    katman_rozeti_ciz(
                                                        ui,
                                                        teklif
                                                            .kaynak_katmani
                                                            .as_deref()
                                                            .unwrap_or("—"),
                                                    );
                                                });
                                            }

                                            row.col(|ui| {
                                                teyit_durumu_ciz(
                                                    ui,
                                                    teklif
                                                        .teyit_durumu
                                                        .as_deref()
                                                        .unwrap_or("bekliyor"),
                                                );
                                            });

                                            if !gizle_ikincil {
                                                row.col(|ui| {
                                                    ui.label(
                                                        RichText::new(
                                                            teklif
                                                                .gozlem_zamani
                                                                .as_deref()
                                                                .map(bicim::iso_tarih)
                                                                .unwrap_or_else(|| "—".to_string()),
                                                        )
                                                        .size(12.5),
                                                    );
                                                });
                                            }

                                            row.col(|ui| {
                                                if let Some(url) = &teklif.url {
                                                    if !url.is_empty()
                                                        && ui
                                                            .small_button("Aç")
                                                            .on_hover_text(url)
                                                            .clicked()
                                                    {
                                                        baglanti_ac(url);
                                                    }
                                                }
                                                if ui.small_button("Detay").clicked() {
                                                    self.secili_teklif_id = teklif.teklif_id;
                                                }
                                            });

                                            let resp = row.response();
                                            if resp.clicked() {
                                                self.secili_teklif_id = teklif.teklif_id;
                                            }
                                            if resp.double_clicked() {
                                                if let Some(u) = &teklif_url {
                                                    if !u.is_empty() {
                                                        baglanti_ac(u);
                                                    }
                                                }
                                            }
                                            resp.context_menu(|ui| {
                                                let url_var = teklif_url
                                                    .as_deref()
                                                    .is_some_and(|u| !u.is_empty());
                                                if ui
                                                    .add_enabled(
                                                        url_var,
                                                        egui::Button::new("🌐 Sitede aç"),
                                                    )
                                                    .clicked()
                                                {
                                                    if let Some(u) = &teklif_url {
                                                        baglanti_ac(u);
                                                    }
                                                    ui.close();
                                                }
                                                if ui
                                                    .add_enabled(
                                                        url_var,
                                                        egui::Button::new("📋 Linki kopyala"),
                                                    )
                                                    .clicked()
                                                {
                                                    if let Some(u) = &teklif_url {
                                                        ctx.copy_text(u.clone());
                                                    }
                                                    ui.close();
                                                }
                                                if ui.button("🔎 Detay").clicked() {
                                                    self.secili_teklif_id = teklif.teklif_id;
                                                    ui.close();
                                                }
                                            });
                                        });
                                    }
                                });
                        });
                    });
            }

            if let Some(pencere) = tiklanan_taksit {
                self.acik_taksit_penceresi = Some(pencere);
            }

            if siralama_degisti {
                self.ayarlar.teklif_siralama = self.teklif_siralama;
                self.ayarlar.teklif_siralama_artan = self.siralama_artan;
                let _ = self.ayarlar.kaydet();
            }

            ui.add_space(12.0);

            // Teklif Ayrıntıları ve Taksit Tabloları (kart içinde)
            tema::kart_cerceve(ui).show(ui, |ui| {
                ui.set_min_width(ui.available_width());
                ui.label(
                    RichText::new("Teklif Ayrıntıları ve Taksit Tablosu")
                        .strong()
                        .size(16.0),
                );
                ui.add_space(4.0);
                for (idx, teklif) in suzulmus.iter().enumerate() {
                    let varsayilan_acik = (self.secili_teklif_id.is_some()
                        && self.secili_teklif_id == teklif.teklif_id)
                        || (self.secili_teklif_id.is_none() && idx == 0);

                    let hedef_rozet = if teklif.hedef_altinda { " 🎯" } else { "" };
                    let baslik_ozet = format!(
                        "{} / {} — {}{}",
                        teklif.site,
                        teklif.satici.as_deref().unwrap_or("—"),
                        teklif
                            .sepet_fiyati
                            .map(bicim::tl)
                            .unwrap_or_else(|| "—".to_string()),
                        hedef_rozet
                    );

                    egui::CollapsingHeader::new(RichText::new(baslik_ozet).strong())
                        .id_salt(format!("teklif_detay_{}_{}", idx, teklif.site))
                        .default_open(varsayilan_acik)
                        .show(ui, |ui| {
                            ui.label(format!(
                                "Başlık: {}",
                                teklif.baslik.as_deref().unwrap_or("—")
                            ));
                            if let Some(url) = &teklif.url {
                                ui.horizontal_wrapped(|ui| {
                                    ui.label(format!("Tam Link: {}", url));
                                    if ui.small_button("Tarayıcıda Aç ↪").clicked() {
                                        baglanti_ac(url);
                                    }
                                });
                            }
                            let kanit = teklif
                                .kanit_goruntusu
                                .as_deref()
                                .filter(|s| !s.is_empty() && *s != "?")
                                .unwrap_or("—");
                            ui.horizontal(|ui| {
                                ui.label(format!("Kanıt Görüntüsü Yolu: {}", kanit));
                                if kanit != "—" && ui.small_button("Görüntüyü Aç").clicked() {
                                    baglanti_ac(kanit);
                                }
                            });

                            if let Some(taksit) = &teklif.taksit {
                                ui.add_space(4.0);
                                ui.strong("Taksit Tablosu:");
                                ui.push_id(format!("teklif_taksit_{}", idx), |ui| {
                                    taksit_tablosu_ciz(ui, taksit, teklif.sepet_fiyati);
                                });
                            }
                        });
                }
            });

            // Fiyat Geçmişi Grafiği
            ui.add_space(12.0);
            tema::kart_cerceve(ui).show(ui, |ui| {
                ui.set_min_width(ui.available_width());
                ui.label(RichText::new("Fiyat Geçmişi Grafiği").strong().size(16.0));
                ui.add_space(4.0);
                if detay.gecmis.is_empty() {
                    ui.label(
                        RichText::new("Bu ürün için henüz geçmiş fiyat verisi bulunmuyor.").weak(),
                    );
                } else {
                    let en_dusuk_noktalar: PlotPoints = detay
                        .gecmis
                        .iter()
                        .enumerate()
                        .map(|(i, g)| [i as f64, g.en_dusuk])
                        .collect();
                    let ortalama_noktalar: PlotPoints = detay
                        .gecmis
                        .iter()
                        .enumerate()
                        .filter_map(|(i, g)| g.ortalama.map(|ort| [i as f64, ort]))
                        .collect();

                    let gunler: Vec<String> =
                        detay.gecmis.iter().map(|g| g.gun.clone()).collect();
                    let hedef = detay.urun.alarm_fiyat.or(detay.urun.hedef_fiyat);

                    Plot::new("fiyat_gecmisi_plot")
                        .height(230.0)
                        .legend(Legend::default())
                        .x_axis_formatter(move |mark, _range| {
                            let idx = mark.value.round() as isize;
                            if idx >= 0 && (idx as usize) < gunler.len() {
                                bicim::iso_tarih(&gunler[idx as usize])
                            } else {
                                String::new()
                            }
                        })
                        .show(ui, |plot_ui| {
                            plot_ui.line(
                                Line::new("En Düşük Fiyat", en_dusuk_noktalar)
                                    .color(p.basari)
                                    .width(2.5_f32),
                            );
                            plot_ui.line(
                                Line::new("Ortalama Fiyat", ortalama_noktalar)
                                    .color(p.vurgu)
                                    .width(2.0_f32),
                            );
                            if let Some(h) = hedef {
                                plot_ui.hline(
                                    HLine::new("Alarm fiyatı", h)
                                        .color(p.uyari)
                                        .width(2.0_f32),
                                );
                            }
                        });
                }
            });
        });
    }

    // ======================= 3. Ekran: Uçuşlar (/seyahat) =======================

    fn ekran_ucuslar(&mut self, ui: &mut egui::Ui) {
        let ctx = ui.ctx().clone();
        let duzen = duyarli_duzen_hesapla(ctx.input(|i| i.content_rect().width()));
        let p = tema::palet(ui.visuals());

        ui.horizontal(|ui| {
            ui.label(RichText::new("✈ Seyahat ve Uçuş Planlayıcı").strong().size(22.0));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button("✎ Planı düzenle").clicked() {
                    self.aktif_sekme = Sekme::SeyahatPlanla;
                }
            });
        });
        // 15.11 kullanıcı: uçuşlarda da son tarama zamanı görünsün
        if let Some(k) = self
            .tarama_plani
            .as_ref()
            .and_then(|pl| pl.kategoriler.iter().find(|k| k.id == "ucus"))
        {
            let yas = self.tarama_plani_ani.elapsed();
            ui.horizontal_wrapped(|ui| {
                ui.label(RichText::new("Son uçuş taraması:").weak());
                match (k.son_bitis.as_deref(), k.durum.as_str()) {
                    (_, "calisiyor") => {
                        ui.spinner();
                        ui.label(RichText::new("şu an taranıyor…").color(p.vurgu));
                    }
                    (Some(b), _) => {
                        ui.label(RichText::new(bicim::iso_tarih(b)).strong());
                        ui.label(RichText::new(ta::son_sonuc_metni(k)).weak().small());
                    }
                    (None, _) => {
                        ui.label(RichText::new("henüz yok").weak());
                    }
                }
                if k.durum != "calisiyor" {
                    ui.separator();
                    ui.label(RichText::new("Sonraki:").weak());
                    ui.label(
                        RichText::new(ta::geri_sayim(ta::kategori_kalan_sn(k, yas))).monospace(),
                    );
                    if let Some(sn) = &k.sonraki {
                        ui.label(RichText::new(bicim::iso_tarih(sn)).weak().small());
                    }
                }
            });
        }
        ui.add_space(6.0);

        let Some(seyahat) = self.seyahat.clone() else {
            if self.yukleniyor {
                Self::yukleniyor_karti(ui, "Seyahat ve uçuş verileri yükleniyor…");
            } else {
                Self::bos_durum_karti(
                    ui,
                    "✈",
                    "Henüz seyahat verisi yok; Seyahat Planla ekranından plan oluşturun veya Yenile'ye basın.",
                );
            }
            return;
        };

        // Arama + seçili uçuş çakışma özeti
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal_wrapped(|ui| {
                ui.spacing_mut().item_spacing.x = 10.0;
                Self::arama_kutusu_ciz(
                    ui,
                    &mut self.ucus_arama,
                    "Firma, rota, tarih veya kaynak ara… (Ctrl+F)",
                    &mut self.arama_odak_iste,
                );
                if let Some(secili_u) = &self.secili_ucus_bilgisi {
                    ui.separator();
                    ui.label(
                        RichText::new(format!(
                            "Seçili uçuş: {} · {}→{} ({}) {}",
                            bicim::iso_tarih(&secili_u.tarih),
                            secili_u.kalkis_nokta,
                            secili_u.varis_nokta,
                            bicim::firma_metni(secili_u.arac.as_deref(), &secili_u.firma),
                            bicim::tl(secili_u.kisi_basi)
                        ))
                        .color(p.vurgu)
                        .strong(),
                    );
                    if ui.small_button("✕ Seçimi kaldır").clicked() {
                        self.secili_ucus_anahtari = None;
                        self.secili_ucus_bilgisi = None;
                    }
                }
            });
        });
        ui.add_space(8.0);

        egui::ScrollArea::vertical().show(ui, |ui| {
            // Yolcular Kartı
            tema::kart_cerceve(ui).show(ui, |ui| {
                ui.set_min_width(ui.available_width());
                ui.horizontal_wrapped(|ui| {
                    ui.strong("Yolcular:");
                    for yolcu in &seyahat.yolcular {
                        ui.separator();
                        ui.label(
                            RichText::new(format!(
                                "👤 {} ({})",
                                yolcu.ad,
                                yolcu.uyruklar.join(", ")
                            ))
                            .strong(),
                        );
                        if yolcu.izinler.is_empty() {
                            ui.label(RichText::new("ek izin kaydı yok").small().weak());
                        } else {
                            for izin in &yolcu.izinler {
                                let varsayim_mi = izin.durum.as_deref() == Some("varsayim");
                                let bitis_metni = izin
                                    .bitis
                                    .as_deref()
                                    .map(|b| format!(" ({})", bicim::iso_tarih(b)))
                                    .unwrap_or_default();
                                ui.label(
                                    RichText::new(format!(
                                        "· {} {}{}",
                                        izin.ulke, izin.tur, bitis_metni
                                    ))
                                    .small(),
                                );
                                if varsayim_mi {
                                    let rozet = ui.label(
                                        RichText::new("⚠ varsayım")
                                            .small()
                                            .color(p.uyari)
                                            .strong(),
                                    );
                                    if let Some(not) = &izin.not {
                                        rozet.on_hover_text(not);
                                    }
                                }
                            }
                        }
                    }
                });
            });

            ui.add_space(8.0);

            // Ana Bacaklar
            let mut ucus_siralama_degisti = false;
            for (b_idx, bacak) in seyahat.bacaklar.iter().enumerate() {
                tema::kart_cerceve(ui).show(ui, |ui| {
                    ui.set_min_width(ui.available_width());
                    let erken = bacak
                        .en_erken
                        .as_deref()
                        .map(bicim::iso_tarih)
                        .unwrap_or_else(|| "—".to_string());
                    let gec = bacak
                        .en_gec
                        .as_deref()
                        .map(bicim::iso_tarih)
                        .unwrap_or_else(|| "—".to_string());
                    let bacak_baslik = format!(
                        "✈ {}  ({} – {} · Yolcular: {} · {} seçenek)",
                        bacak.baslik,
                        erken,
                        gec,
                        bacak.yolcular.join(", "),
                        bacak.secenekler.len()
                    );

                    egui::CollapsingHeader::new(RichText::new(bacak_baslik).strong().size(16.0))
                        .id_salt(format!("bacak_katla_{}", bacak.id))
                        .default_open(true)
                        .show(ui, |ui| {
                            ui.horizontal_wrapped(|ui| {
                                let ucus_etiket = self.ayarlar.ucus_siralama.etiket();
                                if siralama_cubugu_ciz(
                                    ui,
                                    &format!("ucus_bacak_siralama_{}", b_idx),
                                    UcusSiralama::ACILIR_SECENEKLER,
                                    &mut self.ayarlar.ucus_siralama,
                                    &mut self.ayarlar.ucus_siralama_artan,
                                    ucus_etiket,
                                ) {
                                    ucus_siralama_degisti = true;
                                }
                            });

                            ui.add_space(4.0);

                            let suzulmus_ham: Vec<UcusSecenek> = bacak
                                .secenekler
                                .iter()
                                .filter(|s| {
                                    let akt = s.aktarmalar.join(" ");
                                    arama_eslesir(
                                        &self.ucus_arama,
                                        &[
                                            &s.firma,
                                            &s.kalkis_nokta,
                                            &s.varis_nokta,
                                            &akt,
                                            &s.kaynak,
                                            &s.tarih,
                                        ],
                                    )
                                })
                                .cloned()
                                .collect();
                            let secenekler = ucuslari_sirala(
                                &suzulmus_ham,
                                self.ayarlar.ucus_siralama,
                                self.ayarlar.ucus_siralama_artan,
                            );

                            if secenekler.is_empty() {
                                ui.label(
                                    RichText::new("Bu bacak için arama sorgusuna uyan uçuş yok.")
                                        .weak(),
                                );
                            } else {
                                ui.push_id(format!("bacak_tablo_{}", b_idx), |ui| {
                                    if ucus_secenek_tablosu_ciz(
                                        ui,
                                        &secenekler,
                                        &mut self.ayarlar.ucus_siralama,
                                        &mut self.ayarlar.ucus_siralama_artan,
                                        &mut self.secili_ucus_anahtari,
                                        &mut self.secili_ucus_bilgisi,
                                        duzen.ikincil_sutunlari_gizle,
                                    ) {
                                        ucus_siralama_degisti = true;
                                    }
                                });
                            }

                            ui.add_space(4.0);
                            egui::CollapsingHeader::new(
                                RichText::new(format!("Elenenler ({})", bacak.elenen_sayisi))
                                    .small()
                                    .strong(),
                            )
                            .id_salt(format!("elenenler_{}", bacak.id))
                            .default_open(false)
                            .show(ui, |ui| {
                                if bacak.elenen_ornekleri.is_empty() {
                                    ui.label("Elenen seçenek örneği yok.");
                                } else {
                                    egui::ScrollArea::vertical()
                                        .id_salt(format!("elenen_scroll_{}", bacak.id))
                                        .max_height(110.0)
                                        .show(ui, |ui| {
                                            for elenen in &bacak.elenen_ornekleri {
                                                ui.horizontal_wrapped(|ui| {
                                                    ui.strong(bicim::firma_metni(None, &elenen.firma));
                                                    ui.label(format!("({}) →", elenen.rota));
                                                    ui.label(
                                                        RichText::new(&elenen.gerekce)
                                                            .color(p.hata),
                                                    );
                                                });
                                            }
                                        });
                                }
                            });
                        });
                });

                ui.add_space(8.0);
            }

            // 15.11 (C1 #6): en iyi yolculuk zincirleri — bacakların birbirine bağlanabildiği tam rota
            if !seyahat.zincirler.is_empty() || seyahat.zincir_hatasi.is_some() {
                tema::kart_cerceve(ui).show(ui, |ui| {
                    ui.set_min_width(ui.available_width());
                    ui.label(RichText::new("🧭 En iyi yolculuk zincirleri").strong().size(16.0));
                    ui.label(
                        RichText::new("Bütün ana bacaklar birlikte: aktarma/bağlantı payı denetlenmiş, kişi başı toplama göre.")
                            .weak()
                            .small(),
                    );
                    if let Some(h) = &seyahat.zincir_hatasi {
                        ui.label(RichText::new(format!("Zincir hesaplanamadı: {h}")).color(p.uyari));
                    }
                    let bul = |id: &str| {
                        seyahat
                            .bacaklar
                            .iter()
                            .find_map(|b| b.secenekler.iter().find(|s| s.id == id).map(|s| (b, s)))
                    };
                    for (i, z) in seyahat.zincirler.iter().enumerate() {
                        ui.add_space(4.0);
                        ui.horizontal_wrapped(|ui| {
                            ui.label(RichText::new(format!("{}.", i + 1)).strong());
                            ui.label(RichText::new(format!("{} / kişi", bicim::tl(z.kisi_basi_toplam))).strong().color(p.vurgu));
                            if let Some(t) = z.toplam {
                                ui.label(RichText::new(format!("(toplam {})", bicim::tl(t))).weak());
                            }
                        });
                        ui.indent(("zincir", i), |ui| {
                            for sid in &z.secenek_idler {
                                match bul(sid) {
                                    Some((b, s)) => {
                                        ui.horizontal_wrapped(|ui| {
                                            ui.label(RichText::new(&b.baslik).weak().small());
                                            ui.label(format!(
                                                "{} {} · {}",
                                                bicim::kisa_tarih(&s.tarih),
                                                bicim::kalkis_varis_saat(&s.kalkis, &s.varis),
                                                bicim::firma_metni(s.arac.as_deref(), &s.firma)
                                            ));
                                            ui.label(RichText::new(bicim::tl(s.kisi_basi)).monospace());
                                            if let Some(l) = s.link.as_deref().filter(|l| !l.is_empty()) {
                                                if ui.small_button("Aç").on_hover_text(l).clicked() {
                                                    baglanti_ac(l);
                                                }
                                            }
                                        });
                                    }
                                    None => {
                                        ui.label(RichText::new(format!("seçenek {sid}")).weak().small());
                                    }
                                }
                            }
                            for u in &z.uyarilar {
                                ui.label(RichText::new(format!("⚠ {u}")).color(p.uyari).small());
                            }
                        });
                    }
                });
                ui.add_space(8.0);
            }

            // Yan Turlar Bölümü
            if !seyahat.yan_turlar.is_empty() {
                for (yt_idx, yan_tur) in seyahat.yan_turlar.iter().enumerate() {
                    tema::kart_cerceve(ui).show(ui, |ui| {
                        ui.set_min_width(ui.available_width());
                        let pencere_metni = if yan_tur.pencere.len() == 2 {
                            format!(
                                " · Pencere: {} – {}",
                                bicim::iso_tarih(&yan_tur.pencere[0]),
                                bicim::iso_tarih(&yan_tur.pencere[1])
                            )
                        } else {
                            String::new()
                        };
                        let yt_baslik = format!(
                            "↪ Yan Tur: {}{} · Yolcular: {} ({} çift)",
                            yan_tur.baslik,
                            pencere_metni,
                            yan_tur.yolcular.join(", "),
                            yan_tur.ciftler.len()
                        );

                        egui::CollapsingHeader::new(RichText::new(yt_baslik).strong().size(16.0))
                            .id_salt(format!("yan_tur_katla_{}", yt_idx))
                            .default_open(true)
                            .show(ui, |ui| {
                                let yt_etiket = self.ayarlar.yan_tur_siralama.etiket();
                                if siralama_cubugu_ciz(
                                    ui,
                                    &format!("yan_tur_siralama_{}", yt_idx),
                                    YanTurSiralama::ACILIR_SECENEKLER,
                                    &mut self.ayarlar.yan_tur_siralama,
                                    &mut self.ayarlar.yan_tur_siralama_artan,
                                    yt_etiket,
                                ) {
                                    ucus_siralama_degisti = true;
                                }
                                ui.add_space(4.0);

                                let suzulmus_ciftler_ham: Vec<YanTurCifti> = yan_tur
                                    .ciftler
                                    .iter()
                                    .filter(|c| {
                                        arama_eslesir(
                                            &self.ucus_arama,
                                            &[
                                                &c.gidis.firma,
                                                &c.gidis.kalkis_nokta,
                                                &c.gidis.varis_nokta,
                                                &c.gidis.tarih,
                                                &c.donus.firma,
                                                &c.donus.kalkis_nokta,
                                                &c.donus.varis_nokta,
                                                &c.donus.tarih,
                                            ],
                                        )
                                    })
                                    .cloned()
                                    .collect();
                                let sirali_ciftler = yan_turlari_sirala(
                                    &suzulmus_ciftler_ham,
                                    self.ayarlar.yan_tur_siralama,
                                    self.ayarlar.yan_tur_siralama_artan,
                                );

                                let secili_ucus_ref = self.secili_ucus_bilgisi.as_ref();
                                let cakisma_sayisi = secili_ucus_ref
                                    .map(|u| {
                                        sirali_ciftler
                                            .iter()
                                            .filter(|c| ucus_yan_tur_cakismasi(u, c).is_some())
                                            .count()
                                    })
                                    .unwrap_or(0);
                                if let Some(u) = secili_ucus_ref {
                                    let (uyari_metni, uyari_rengi) = if cakisma_sayisi > 0 {
                                        (
                                            format!(
                                                "⚠ Seçili uçuş ({}) ile {} yan tur çifti çakışıyor — kırmızı ile işaretlendi",
                                                bicim::iso_tarih(&u.tarih),
                                                cakisma_sayisi
                                            ),
                                            p.hata,
                                        )
                                    } else {
                                        (
                                            format!(
                                                "✓ Seçili uçuş ({}) ile çakışan yan tur yok",
                                                bicim::iso_tarih(&u.tarih)
                                            ),
                                            p.basari,
                                        )
                                    };
                                    ui.label(RichText::new(uyari_metni).color(uyari_rengi).strong());
                                    ui.add_space(2.0);
                                }

                                ui.scope(|ui| {
                                    ui.set_max_height(175.0);
                                    egui::ScrollArea::horizontal()
                                        .id_salt(format!("yan_tur_yatay_{}", yt_idx))
                                        .auto_shrink([false, true])
                                        .show(ui, |ui| {
                                            ui.spacing_mut().item_spacing.x = 6.0;
                                            ui.push_id(format!("yan_tur_tablo_{}", yt_idx), |ui| {
                                                let kova = genislik_kovasi(ui);
                                                let mut yt_tablo = TableBuilder::new(ui)
                                                    .id_salt(("yan_tur_tablosu", kova))
                                                    .striped(true)
                                                    .resizable(true)
                                                    .min_scrolled_height(85.0)
                                                    .max_scroll_height(150.0)
                                                    .cell_layout(egui::Layout::left_to_right(
                                                        egui::Align::Center,
                                                    ))
                                                    .column(
                                                        Column::remainder().at_least(180.0).clip(true),
                                                    ) // Gidiş
                                                    .column(
                                                        Column::initial(240.0)
                                                            .at_least(160.0)
                                                            .clip(true),
                                                    ) // Dönüş
                                                    .column(
                                                        Column::initial(58.0)
                                                            .at_least(44.0)
                                                            .clip(true),
                                                    ); // Gece
                                                if !duzen.ikincil_sutunlari_gizle {
                                                    yt_tablo = yt_tablo.column(
                                                        Column::initial(84.0)
                                                            .at_least(64.0)
                                                            .clip(true),
                                                    ); // Toplam Süre
                                                }
                                                yt_tablo = yt_tablo
                                                    .column(
                                                        Column::initial(114.0)
                                                            .at_least(92.0)
                                                            .clip(true),
                                                    ) // Kişi Başı Toplam
                                                    .column(
                                                        Column::initial(108.0)
                                                            .at_least(86.0)
                                                            .clip(true),
                                                    ); // Bağlantılar

                                                yt_tablo
                                                    .header(26.0, |mut header| {
                                                        let mut yt_sutunlar: Vec<(&str, YanTurSiralama)> = vec![
                                                            ("Gidiş (Tokyo → Seul)", YanTurSiralama::Gidis),
                                                            ("Dönüş (Seul → Tokyo)", YanTurSiralama::Donus),
                                                            ("Gece", YanTurSiralama::Gece),
                                                        ];
                                                        if !duzen.ikincil_sutunlari_gizle {
                                                            yt_sutunlar.push(("Toplam Süre", YanTurSiralama::ToplamSure));
                                                        }
                                                        yt_sutunlar.push(("Kişi Başı Toplam", YanTurSiralama::Fiyat));
                                                        for (h, kriter) in yt_sutunlar {
                                                            header.col(|ui| {
                                                                if siralanabilir_baslik(
                                                                    ui,
                                                                    h,
                                                                    kriter,
                                                                    &mut self
                                                                        .ayarlar
                                                                        .yan_tur_siralama,
                                                                    &mut self
                                                                        .ayarlar
                                                                        .yan_tur_siralama_artan,
                                                                ) {
                                                                    ucus_siralama_degisti = true;
                                                                }
                                                            });
                                                        }
                                                        header.col(|ui| {
                                                            ui.strong("Link");
                                                        });
                                                    })
                                                    .body(|mut body| {
                                                        for cift in &sirali_ciftler {
                                                            let cakisma = secili_ucus_ref
                                                                .and_then(|u| {
                                                                    ucus_yan_tur_cakismasi(u, cift)
                                                                });
                                                            body.row(28.0, |mut row| {
                                                                row.col(|ui| {
                                                                    let g = &cift.gidis;
                                                                    let tam = format!(
                                                                        "{} {} {}→{} ({}) {}",
                                                                        bicim::iso_tarih(&g.tarih),
                                                                        bicim::kalkis_varis_saat(
                                                                            &g.kalkis, &g.varis
                                                                        ),
                                                                        g.kalkis_nokta,
                                                                        g.varis_nokta,
                                                                        bicim::firma_metni(g.arac.as_deref(), &g.firma),
                                                                        bicim::tl(g.kisi_basi),
                                                                    );
                                                                    let mut rt = RichText::new(&tam);
                                                                    if cakisma.is_some() {
                                                                        rt = rt.color(p.hata);
                                                                    }
                                                                    let hover = match &cakisma {
                                                                        Some(uy) => format!("{tam}\n⚠ {uy}"),
                                                                        None => tam.clone(),
                                                                    };
                                                                    ui.add(egui::Label::new(rt).truncate())
                                                                        .on_hover_text(hover);
                                                                });
                                                                row.col(|ui| {
                                                                    let d = &cift.donus;
                                                                    let tam = format!(
                                                                        "{} {} {}→{} ({}) {}",
                                                                        bicim::iso_tarih(&d.tarih),
                                                                        bicim::kalkis_varis_saat(
                                                                            &d.kalkis, &d.varis
                                                                        ),
                                                                        d.kalkis_nokta,
                                                                        d.varis_nokta,
                                                                        bicim::firma_metni(d.arac.as_deref(), &d.firma),
                                                                        bicim::tl(d.kisi_basi),
                                                                    );
                                                                    ui.add(egui::Label::new(&tam).truncate())
                                                                        .on_hover_text(tam);
                                                                });
                                                                row.col(|ui| {
                                                                    ui.with_layout(
                                                                        egui::Layout::right_to_left(
                                                                            egui::Align::Center,
                                                                        ),
                                                                        |ui| {
                                                                            ui.label(
                                                                                RichText::new(format!(
                                                                                    "{} gece",
                                                                                    cift.gece
                                                                                ))
                                                                                .monospace(),
                                                                            );
                                                                        },
                                                                    );
                                                                });
                                                                if !duzen.ikincil_sutunlari_gizle {
                                                                    row.col(|ui| {
                                                                        ui.with_layout(
                                                                            egui::Layout::right_to_left(
                                                                                egui::Align::Center,
                                                                            ),
                                                                            |ui| {
                                                                                ui.label(
                                                                                    RichText::new(
                                                                                        bicim::dakika(
                                                                                            cift.toplam_sure_dk,
                                                                                        ),
                                                                                    )
                                                                                    .monospace(),
                                                                                );
                                                                            },
                                                                        );
                                                                    });
                                                                }
                                                                row.col(|ui| {
                                                                    ui.with_layout(
                                                                        egui::Layout::right_to_left(
                                                                            egui::Align::Center,
                                                                        ),
                                                                        |ui| {
                                                                            ui.label(
                                                                                RichText::new(bicim::tl(
                                                                                    cift.kisi_basi_toplam,
                                                                                ))
                                                                                .strong()
                                                                                .monospace(),
                                                                            );
                                                                        },
                                                                    );
                                                                });
                                                                row.col(|ui| {
                                                                    if let Some(l) = &cift.gidis.link {
                                                                        if ui
                                                                            .small_button("Gidiş")
                                                                            .on_hover_text(l)
                                                                            .clicked()
                                                                        {
                                                                            baglanti_ac(l);
                                                                        }
                                                                    }
                                                                    if let Some(l) = &cift.donus.link {
                                                                        if ui
                                                                            .small_button("Dönüş")
                                                                            .on_hover_text(l)
                                                                            .clicked()
                                                                        {
                                                                            baglanti_ac(l);
                                                                        }
                                                                    }
                                                                });
                                                            });
                                                        }
                                                    });
                                            });
                                        });
                                });
                            });
                    });
                    ui.add_space(6.0);
                }
            }

            if ucus_siralama_degisti {
                let _ = self.ayarlar.kaydet();
            }
        });
    }

    // ======================= 4. Ekran: Site Sağlığı (/siteler) =======================

    fn site_sagligi_tablosu_ciz(
        ui: &mut egui::Ui,
        id_tuz: &str,
        siteler: &[SiteSaglik],
        kriter: &mut SiteSiralama,
        artan: &mut bool,
        gizle_ikincil: bool,
    ) -> bool {
        let mut siralama_degisti = false;
        let p = tema::palet(ui.visuals());

        ui.push_id(id_tuz, |ui| {
            let tablo_y = ((siteler.len() as f32) * 28.0 + 36.0).clamp(110.0, 320.0);
            ui.allocate_ui(egui::vec2(ui.available_width(), tablo_y), |ui| {
                let kova = genislik_kovasi(ui);
                let mut tablo = TableBuilder::new(ui)
                    .id_salt(("site_tablosu", kova))
                    .striped(true)
                    .resizable(true)
                    .sense(Sense::click())
                    .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                    .column(Column::initial(92.0).at_least(78.0).clip(true)) // Durum
                    .column(Column::initial(130.0).at_least(90.0).clip(true)); // Site

                if !gizle_ikincil {
                    tablo = tablo
                        .column(Column::initial(64.0).at_least(48.0).clip(true)) // Katman
                        .column(Column::initial(126.0).at_least(100.0).clip(true)); // Son Başarı
                }
                tablo = tablo.column(Column::remainder().at_least(130.0).clip(true)); // Son Hata
                if !gizle_ikincil {
                    tablo = tablo.column(Column::initial(84.0).at_least(64.0).clip(true)); // 24s Başarı
                }
                tablo = tablo
                    .column(Column::initial(78.0).at_least(60.0).clip(true)) // 24s Hata
                    .column(Column::initial(84.0).at_least(64.0).clip(true)); // 24s Teklif

                tablo
                    .header(28.0, |mut header| {
                        let mut sutunlar: Vec<(&str, SiteSiralama)> = vec![
                            ("Durum", SiteSiralama::Durum),
                            ("Site", SiteSiralama::SiteAdi),
                        ];
                        if !gizle_ikincil {
                            sutunlar.push(("Katman", SiteSiralama::Katman));
                            sutunlar.push(("Son Başarı", SiteSiralama::SonBasari));
                        }
                        sutunlar.push(("Son Hata", SiteSiralama::SonHata));
                        if !gizle_ikincil {
                            sutunlar.push(("24s Başarı", SiteSiralama::Basarili24s));
                        }
                        sutunlar.push(("24s Hata", SiteSiralama::Hatali24s));
                        sutunlar.push(("24s Teklif", SiteSiralama::Teklif24s));

                        for (baslik, k) in sutunlar {
                            header.col(|ui| {
                                if siralanabilir_baslik(ui, baslik, k, kriter, artan) {
                                    siralama_degisti = true;
                                }
                            });
                        }
                    })
                    .body(|mut body| {
                        for site in siteler {
                            let basarili =
                                site.son_24s.as_ref().map(|s| s.basarili).unwrap_or(0);
                            let hatali = site.son_24s.as_ref().map(|s| s.hatali).unwrap_or(0);
                            let teklif = site.son_24s.as_ref().map(|s| s.teklif).unwrap_or(0);
                            let durum = site_durumu(site);

                            body.row(28.0, |mut row| {
                                row.col(|ui| {
                                    let durum_renk = durum.renk(ui.visuals());
                                    ui.label(
                                        RichText::new(durum.etiket())
                                            .color(durum_renk)
                                            .strong(),
                                    );
                                });
                                row.col(|ui| {
                                    ui.add(egui::Label::new(RichText::new(&site.ad).strong()).truncate())
                                        .on_hover_text(&site.ad);
                                });
                                if !gizle_ikincil {
                                    row.col(|ui| {
                                        katman_rozeti_ciz(
                                            ui,
                                            site.katman_tercihi.as_deref().unwrap_or("K0"),
                                        );
                                    });
                                    row.col(|ui| {
                                        ui.label(
                                            RichText::new(
                                                site.son_basari
                                                    .as_deref()
                                                    .map(bicim::iso_tarih)
                                                    .unwrap_or_else(|| "—".to_string()),
                                            )
                                            .size(12.5),
                                        );
                                    });
                                }
                                row.col(|ui| {
                                    let hata_zaman = site
                                        .son_hata
                                        .as_deref()
                                        .map(bicim::iso_tarih)
                                        .unwrap_or_else(|| "—".to_string());
                                    let hata_metin = match &site.hata_turu {
                                        Some(tur) if !tur.is_empty() && tur != "?" => {
                                            if hata_zaman == "—" {
                                                tur.clone()
                                            } else {
                                                format!("{} ({})", hata_zaman, tur)
                                            }
                                        }
                                        _ => hata_zaman,
                                    };
                                    ui.add(egui::Label::new(&hata_metin).truncate())
                                        .on_hover_text(hata_metin);
                                });
                                if !gizle_ikincil {
                                    row.col(|ui| {
                                        ui.with_layout(
                                            egui::Layout::right_to_left(egui::Align::Center),
                                            |ui| {
                                                ui.label(
                                                    RichText::new(basarili.to_string())
                                                        .color(p.basari)
                                                        .monospace(),
                                                );
                                            },
                                        );
                                    });
                                }
                                row.col(|ui| {
                                    let renk = if hatali > 0 {
                                        if durum == SiteDurum::Kapali {
                                            p.kapali
                                        } else {
                                            p.hata
                                        }
                                    } else {
                                        ui.visuals().text_color()
                                    };
                                    ui.with_layout(
                                        egui::Layout::right_to_left(egui::Align::Center),
                                        |ui| {
                                            ui.label(
                                                RichText::new(hatali.to_string())
                                                    .color(renk)
                                                    .monospace(),
                                            );
                                        },
                                    );
                                });
                                row.col(|ui| {
                                    ui.with_layout(
                                        egui::Layout::right_to_left(egui::Align::Center),
                                        |ui| {
                                            ui.label(
                                                RichText::new(teklif.to_string())
                                                    .strong()
                                                    .monospace(),
                                            );
                                        },
                                    );
                                });
                            });
                        }
                    });
            });
        });

        siralama_degisti
    }

    fn ekran_site_sagligi(&mut self, ui: &mut egui::Ui) {
        let duzen = duyarli_duzen_hesapla(ui.ctx().input(|i| i.content_rect().width()));
        let p = tema::palet(ui.visuals());

        ui.label(
            RichText::new("Site Sağlığı ve Tarayıcı Katmanları")
                .strong()
                .size(22.0),
        );
        ui.label(
            RichText::new(
                "Katmanlar: K0 = Düz HTTP · K1 = Başsız Chromium · K2 = Brave Kalıcı Profil · K3 = Kullanıcı Müdahalesi",
            )
            .size(13.0)
            .weak(),
        );
        ui.add_space(8.0);

        if self.siteler.is_empty() {
            if self.yukleniyor {
                Self::yukleniyor_karti(ui, "Site sağlığı verileri yükleniyor…");
            } else {
                Self::bos_durum_karti(ui, "🛡", "Henüz site sağlığı verisi yok.");
            }
            return;
        }

        // Durum sayımları (her durum tek site_durumu() fonksiyonu üzerinden)
        let toplam_sayi = self.siteler.len();
        let yesil_sayi = self
            .siteler
            .iter()
            .filter(|s| site_durumu(s) == SiteDurum::Yesil)
            .count();
        let sari_sayi = self
            .siteler
            .iter()
            .filter(|s| site_durumu(s) == SiteDurum::Sari)
            .count();
        let kirmizi_sayi = self
            .siteler
            .iter()
            .filter(|s| site_durumu(s) == SiteDurum::Kirmizi)
            .count();
        let kapali_sayi = self
            .siteler
            .iter()
            .filter(|s| site_durumu(s) == SiteDurum::Kapali)
            .count();

        let mut filtre_degisti = false;
        let mut site_siralama_degisti = false;
        let site_etiket = self.ayarlar.site_siralama.etiket();

        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            // Durum filtresi düğmeleri (çoklu seçim + sayılar + kaydedilir)
            ui.horizontal_wrapped(|ui| {
                ui.spacing_mut().item_spacing.x = 6.0;
                let hepsi = self.ayarlar.site_durum_filtresi.hepsi_secili();
                if ui
                    .selectable_label(hepsi, RichText::new(format!("Tümü ({toplam_sayi})")).strong())
                    .clicked()
                {
                    self.ayarlar.site_durum_filtresi.tumunu_sec();
                    filtre_degisti = true;
                }
                for (durum, adet) in [
                    (SiteDurum::Yesil, yesil_sayi),
                    (SiteDurum::Sari, sari_sayi),
                    (SiteDurum::Kirmizi, kirmizi_sayi),
                    (SiteDurum::Kapali, kapali_sayi),
                ] {
                    let secili = !hepsi && self.ayarlar.site_durum_filtresi.durum_acik(durum);
                    let etiket = format!("{} ({adet})", durum.etiket());
                    let renk = durum.renk(ui.visuals());
                    let mut dugme = egui::Button::new(
                        RichText::new(etiket)
                            .color(if secili { renk } else { ui.visuals().text_color() })
                            .strong(),
                    )
                    .corner_radius(14.0);
                    if secili {
                        dugme = dugme.fill(p.vurgu_dolgu).stroke(Stroke::new(1.0, renk));
                    }
                    if ui
                        .add(dugme)
                        .on_hover_text("Durum süzgeci (çoklu seçim destekler)")
                        .clicked()
                    {
                        self.ayarlar.site_durum_filtresi.secimi_degistir(durum);
                        filtre_degisti = true;
                    }
                }
            });

            ui.add_space(6.0);
            ui.horizontal_wrapped(|ui| {
                ui.spacing_mut().item_spacing.x = 10.0;
                Self::arama_kutusu_ciz(
                    ui,
                    &mut self.site_arama,
                    "Site, katman veya hata ara… (Ctrl+F)",
                    &mut self.arama_odak_iste,
                );
                ui.separator();
                if siralama_cubugu_ciz(
                    ui,
                    "site_sagligi_siralama_combo",
                    SiteSiralama::ACILIR_SECENEKLER,
                    &mut self.ayarlar.site_siralama,
                    &mut self.ayarlar.site_siralama_artan,
                    site_etiket,
                ) {
                    site_siralama_degisti = true;
                }
            });
        });
        ui.add_space(8.0);

        let suzulmus_siteler: Vec<SiteSaglik> = siteleri_sirala(
            &self.siteler,
            self.ayarlar.site_siralama,
            self.ayarlar.site_siralama_artan,
        )
        .into_iter()
        .filter(|site| {
            let durum = site_durumu(site);
            if !self.ayarlar.site_durum_filtresi.durum_acik(durum) {
                return false;
            }
            let hata = site.hata_turu.as_deref().unwrap_or("");
            let katman = site.katman_tercihi.as_deref().unwrap_or("");
            let tur = site.tur.as_deref().unwrap_or("");
            arama_eslesir(&self.site_arama, &[&site.ad, hata, katman, tur])
        })
        .collect();

        if suzulmus_siteler.is_empty() {
            Self::bos_durum_karti(
                ui,
                "🛡",
                "Seçili durum filtreleri veya arama sorgusuna uyan site yok.",
            );
        } else {
            let urun_siteleri: Vec<SiteSaglik> = suzulmus_siteler
                .iter()
                .filter(|s| s.tur.as_deref() != Some("ucus"))
                .cloned()
                .collect();
            let ucus_kaynaklari: Vec<SiteSaglik> = suzulmus_siteler
                .iter()
                .filter(|s| s.tur.as_deref() == Some("ucus"))
                .cloned()
                .collect();

            egui::ScrollArea::vertical().show(ui, |ui| {
                for (id_tuz, baslik_ad, alt_siteler) in [
                    ("urun_siteleri", "Ürün siteleri", &urun_siteleri),
                    ("ucus_kaynaklari", "Uçuş kaynakları", &ucus_kaynaklari),
                ] {
                    if alt_siteler.is_empty() {
                        continue;
                    }
                    let y = alt_siteler
                        .iter()
                        .filter(|s| site_durumu(s) == SiteDurum::Yesil)
                        .count();
                    let sa = alt_siteler
                        .iter()
                        .filter(|s| site_durumu(s) == SiteDurum::Sari)
                        .count();
                    let k = alt_siteler
                        .iter()
                        .filter(|s| site_durumu(s) == SiteDurum::Kirmizi)
                        .count();
                    let kp = alt_siteler
                        .iter()
                        .filter(|s| site_durumu(s) == SiteDurum::Kapali)
                        .count();
                    let mut ozet_parcalar = Vec::new();
                    if y > 0 {
                        ozet_parcalar.push(format!("{y} yeşil"));
                    }
                    if sa > 0 {
                        ozet_parcalar.push(format!("{sa} sarı"));
                    }
                    if k > 0 {
                        ozet_parcalar.push(format!("{k} kırmızı"));
                    }
                    if kp > 0 {
                        ozet_parcalar.push(format!("{kp} kapalı"));
                    }
                    let baslik = format!(
                        "{baslik_ad} ({} · {})",
                        alt_siteler.len(),
                        ozet_parcalar.join(", ")
                    );

                    tema::kart_cerceve(ui).show(ui, |ui| {
                        ui.set_min_width(ui.available_width());
                        egui::CollapsingHeader::new(RichText::new(baslik).strong().size(16.0))
                            .id_salt(format!("site_bolum_{id_tuz}"))
                            .default_open(true)
                            .show(ui, |ui| {
                                if Self::site_sagligi_tablosu_ciz(
                                    ui,
                                    id_tuz,
                                    alt_siteler,
                                    &mut self.ayarlar.site_siralama,
                                    &mut self.ayarlar.site_siralama_artan,
                                    duzen.ikincil_sutunlari_gizle,
                                ) {
                                    site_siralama_degisti = true;
                                }
                            });
                    });
                    ui.add_space(8.0);
                }
            });
        }

        if filtre_degisti || site_siralama_degisti {
            if self.goruntu_hedefi.is_none() {
                let _ = self.ayarlar.kaydet();
            }
        }
    }

    // ======================= 5. Ekran: Ara / Tanımla (POST /tanimla) =======================

    fn ekran_ara_tanimla(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let p = tema::palet(ui.visuals());
        ui.label(
            RichText::new("Ara / Tanımla (\"Bu ürün mü?\")")
                .strong()
                .size(22.0),
        );
        ui.label(
            RichText::new("İstemi yazıp türü seçin; katalog adaylarından doğru olanı onaylayarak takibe alın.")
                .size(13.0)
                .weak(),
        );
        ui.add_space(8.0);

        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal_wrapped(|ui| {
                ui.label("İstem:");
                let yanit = ui.add(
                    egui::TextEdit::singleline(&mut self.tanimla_metin)
                        .hint_text("Örn: rtx 5080 en ucuz, 2x32 DDR5 6000 CL30...")
                        .desired_width(320.0),
                );
                if yanit.lost_focus() && ui.input(|i| i.key_pressed(egui::Key::Enter)) {
                    self.tanimla_istegi_gonder(ctx);
                }

                ui.separator();
                ui.label("Tür:");
                for (kod, etiket) in [
                    ("urun", "Ürün"),
                    ("ucus", "Uçuş ✈"),
                    ("bilet", "Bilet 🚄/🚌"),
                    ("diger", "Diğer"),
                ] {
                    ui.selectable_value(&mut self.tanimla_tur, kod.to_string(), etiket);
                }

                if ui
                    .add_enabled(
                        !self.tanimla_yukleniyor && !self.tanimla_metin.trim().is_empty(),
                        egui::Button::new("🔍 Tanımla"),
                    )
                    .clicked()
                {
                    self.tanimla_istegi_gonder(ctx);
                }

                if self.tanimla_yukleniyor {
                    ui.spinner();
                }
            });
        });

        ui.add_space(10.0);

        if let Some(bilgi) = &self.tanimla_bilgi_mesaji {
            tema::kart_cerceve(ui)
                .fill(p.uyari_dolgu)
                .stroke(Stroke::new(1.0, p.uyari))
                .show(ui, |ui| {
                    ui.set_min_width(ui.available_width());
                    ui.label(RichText::new(bilgi).color(p.uyari).strong());
                });
            ui.add_space(8.0);
        }

        if let Some(sonuc) = self.tanimla_sonuc.clone() {
            if sonuc.adaylar.is_empty() {
                Self::bos_durum_karti(ui, "🔍", "Bu sorgu için aday kartı döndürülmedi.");
            } else {
                ui.label(
                    RichText::new(format!(
                        "İstek No: {} — {} aday bulundu:",
                        sonuc.istek_id,
                        sonuc.adaylar.len()
                    ))
                    .strong(),
                );
                ui.add_space(6.0);

                egui::ScrollArea::vertical().show(ui, |ui| {
                    for aday in &sonuc.adaylar {
                        if self.reddedilen_adaylar.contains(&aday.id) {
                            continue;
                        }
                        tema::kart_cerceve(ui).show(ui, |ui| {
                            ui.set_min_width(ui.available_width());
                            ui.horizontal_wrapped(|ui| {
                                ui.label(RichText::new(&aday.baslik).strong().size(16.0));
                                if let Some(kaynak) = &aday.kaynak {
                                    ui.label(RichText::new(format!("[{}]", kaynak)).weak());
                                }
                            });
                            if let Some(mpn) = &aday.mpn {
                                if !mpn.is_empty() && mpn != "?" {
                                    ui.label(format!("MPN / Kod: {}", mpn));
                                }
                            }
                            if let Some(band) = &aday.fiyat_araligi {
                                let alt = band
                                    .alt
                                    .map(bicim::tl)
                                    .unwrap_or_else(|| "—".to_string());
                                let ust = band
                                    .ust
                                    .map(bicim::tl)
                                    .unwrap_or_else(|| "—".to_string());
                                ui.label(format!("Fiyat Aralığı: {} – {}", alt, ust));
                            }
                            if !aday.ozellikler.is_empty() {
                                let ozet: Vec<String> = aday
                                    .ozellikler
                                    .iter()
                                    .map(|(k, v)| format!("{}: {}", k, v))
                                    .collect();
                                ui.label(format!("Özellikler: {}", ozet.join(" · ")));
                            }

                            ui.add_space(6.0);
                            ui.horizontal(|ui| {
                                if ui
                                    .button(
                                        RichText::new("✓ Evet, bu")
                                            .color(p.basari)
                                            .strong(),
                                    )
                                    .clicked()
                                {
                                    self.aday_onayla_gonder(
                                        ctx,
                                        sonuc.istek_id.clone(),
                                        aday.id.clone(),
                                        aday.baslik.clone(),
                                        aday.mpn.clone(),
                                    );
                                }
                                if ui
                                    .button(
                                        RichText::new("✗ Hayır").color(p.hata),
                                    )
                                    .clicked()
                                {
                                    self.reddedilen_adaylar.insert(aday.id.clone());
                                }
                                if let Some(url) = &aday.url {
                                    if !url.is_empty()
                                        && url != "?"
                                        && ui.button("Katalogda Aç ↪").clicked()
                                    {
                                        baglanti_ac(url);
                                    }
                                }
                            });
                        });
                        ui.add_space(8.0);
                    }
                });
            }
        } else if !self.tanimla_yukleniyor {
            Self::bos_durum_karti(
                ui,
                "➕",
                "Yukarıya takip etmek istediğiniz ürünü veya uçuşu yazıp Tanımla düğmesine basın.",
            );
        }
    }

    // ======================= 6. Ekran: Ayarlar =======================

    fn ekran_ayarlar(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let p = tema::palet(ui.visuals());
        ui.label(RichText::new("Ayarlar").strong().size(22.0));
        ui.label(
            RichText::new(format!(
                "Yapılandırma dosyası: {}",
                UygulamaAyarlar::dosya_yolu().display()
            ))
            .size(13.0)
            .weak(),
        );
        ui.add_space(10.0);

        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            egui::Grid::new("ayarlar_grid")
                .num_columns(2)
                .spacing([18.0, 12.0])
                .show(ui, |ui| {
                    ui.label("Çekirdek API Adresi:");
                    ui.add(
                        egui::TextEdit::singleline(&mut self.ayarlar.api_adresi)
                            .desired_width(280.0),
                    );
                    ui.end_row();

                    ui.label("Fiyat taraması — Tarayıcı penceresi:");
                    ui.vertical(|ui| {
                        self.brave_anahtari_ciz(ctx, ui);
                        ui.label(
                            RichText::new(
                                "Gizli: tarama arka planda sürer, tarayıcı penceresi simge durumunda kalır. \
Doğrulama (CAPTCHA) gerekirse pencere kendiliğinden açılır.",
                            )
                            .weak()
                            .small(),
                        );
                    });
                    ui.end_row();

                    ui.label("Görünüm Teması:");
                    ui.horizontal(|ui| {
                        for (tercih, etiket) in [
                            (TemaTercihi::Sistem, "🖥 Sistem"),
                            (TemaTercihi::Acik, "☀ Açık"),
                            (TemaTercihi::Koyu, "🌙 Koyu"),
                        ] {
                            let secili = self.ayarlar.tema_tercihi == tercih;
                            if ui.selectable_label(secili, etiket).clicked() {
                                self.ayarlar.tema_tercihi = tercih;
                                self.ayarlar.koyu_tema = tercih == TemaTercihi::Koyu;
                                tema::tema_uygula(ctx, tercih);
                                if self.goruntu_hedefi.is_none() {
                                    let _ = self.ayarlar.kaydet();
                                }
                            }
                        }
                    });
                    ui.end_row();

                    ui.label("Yakınlaştırma:");
                    ui.horizontal(|ui| {
                        let yuzde = (self.ayarlar.yakinlastirma * 100.0).round() as i32;
                        if ui.small_button("−").clicked() {
                            let yeni = (self.ayarlar.yakinlastirma - 0.1).clamp(0.5, 2.5);
                            self.ayarlar.yakinlastirma = (yeni * 10.0).round() / 10.0;
                            ctx.set_zoom_factor(self.ayarlar.yakinlastirma);
                            if self.goruntu_hedefi.is_none() {
                                let _ = self.ayarlar.kaydet();
                            }
                        }
                        ui.label(RichText::new(format!("%{yuzde}")).monospace().strong());
                        if ui.small_button("+").clicked() {
                            let yeni = (self.ayarlar.yakinlastirma + 0.1).clamp(0.5, 2.5);
                            self.ayarlar.yakinlastirma = (yeni * 10.0).round() / 10.0;
                            ctx.set_zoom_factor(self.ayarlar.yakinlastirma);
                            if self.goruntu_hedefi.is_none() {
                                let _ = self.ayarlar.kaydet();
                            }
                        }
                        if ui.small_button("Sıfırla (Ctrl+0)").clicked() {
                            self.ayarlar.yakinlastirma = 1.0;
                            ctx.set_zoom_factor(1.0);
                            if self.goruntu_hedefi.is_none() {
                                let _ = self.ayarlar.kaydet();
                            }
                        }
                    });
                    ui.end_row();
                });

            ui.add_space(12.0);
            ui.horizontal(|ui| {
                if ui.button("💾 Kaydet ve Uygula").clicked() {
                    tema::tema_uygula(ctx, self.ayarlar.tema_tercihi);
                    ctx.set_zoom_factor(self.ayarlar.yakinlastirma.clamp(0.5, 2.5));
                    if let VeriKaynagi::Http(_) = self.veri_kaynagi {
                        self.veri_kaynagi = VeriKaynagi::Http(self.ayarlar.api_adresi.clone());
                        if let Ok(mut g) = self.ws_hedef_url.lock() {
                            g.0 = g.0.wrapping_add(1);
                            g.1 = ws_olaylar_url(&self.ayarlar.api_adresi);
                        }
                    }
                    match self.ayarlar.kaydet() {
                        Ok(yol) => {
                            self.ayarlar_mesaji =
                                Some(format!("✓ Ayarlar kaydedildi: {}", yol.display()));
                        }
                        Err(e) => {
                            self.ayarlar_mesaji = Some(format!("⚠ {}", e));
                        }
                    }
                    self.genel_yenileme_baslat(ctx);
                }

                if ui.button("Varsayılanlara Sıfırla").clicked() {
                    self.ayarlar = UygulamaAyarlar::default();
                    self.teklif_siralama = self.ayarlar.teklif_siralama;
                    self.siralama_artan = self.ayarlar.teklif_siralama_artan;
                    tema::tema_uygula(ctx, self.ayarlar.tema_tercihi);
                    ctx.set_zoom_factor(self.ayarlar.yakinlastirma);
                }
            });

            if let Some(msg) = &self.ayarlar_mesaji {
                ui.add_space(8.0);
                ui.label(RichText::new(msg).color(p.basari));
            }
        });

        ui.add_space(12.0);
        // 15.11: tarama ayarları iki sekme — solda tarayıcılı kategori taramaları, yanında tarayıcısız hızlı HTTP turu
        ui.horizontal(|ui| {
            let http_etiket = match self.http_turu.ozet_etiketi() {
                Some(e) => format!("⚡ Hızlı HTTP turu · {e}"),
                None => "⚡ Hızlı HTTP turu".to_string(),
            };
            let sik_etiket = if self.http_turu.tarayici_otomatik() == Some(false) {
                "🌐 Tarayıcı turu · kapalı".to_string()
            } else {
                "🌐 Tarayıcı turu".to_string()
            };
            for (no, etiket) in [(0u8, sik_etiket), (1u8, http_etiket)] {
                let secili = self.tarama_ayar_sekmesi == no;
                if ui
                    .add(egui::Button::selectable(secili, RichText::new(etiket).size(14.5).strong()))
                    .clicked()
                {
                    self.tarama_ayar_sekmesi = no;
                }
            }
        });
        ui.add_space(4.0);
        if self.tarama_ayar_sekmesi == 1 {
            self.http_turu.goster(ctx, ui, &self.veri_kaynagi);
        } else {
            self.http_turu.tarayici_turu_karti(ctx, ui, &self.veri_kaynagi);
            ui.add_space(8.0);
            self.tarama_sikligi_karti(ctx, ui);
        }
        ui.add_space(12.0);
        self.aranan_siteler_karti(ctx, ui);
        ui.add_space(12.0);
        self.paralel_karti(ctx, ui);
        ui.add_space(12.0);
        self.gizlilik_karti(ctx, ui);
        ui.add_space(12.0);
        self.telegram_karti(ctx, ui);
    }

    /// Ayarlar → "Tarama sıklığı (kategori bazında)"
    fn tarama_sikligi_karti(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let p = tema::palet(ui.visuals());
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.label(RichText::new("Tarayıcı turu sıklığı (kategori bazında)").strong().size(16.0));
            ui.label(
                RichText::new(
                    "Süre, kategorinin son taraması BİTTİĞİNDE başlar; dolunca tarama kendiliğinden başlar (30–120 dk, 5'er dk). \
Süreyi değiştirdikten sonra ↺ Sıfırla: sayaç şimdiden, yeni süreyle başlar (taranan kategoriye dokunmaz). \
İki otomatik tarama arasında en az 2 dk bırakılır; zamanı çakışanlar birlikte ya da sırayla taranır. \
Şimdi tara: aynı kategori için 10 dk'da bir.",
                )
                .weak()
                .size(12.5),
            );
            ui.add_space(6.0);
            let Some(plan) = self.tarama_plani.clone() else {
                ui.label(
                    RichText::new(if self.tarama_plani_yok {
                        "Tarama zamanlayıcısı henüz yok (çekirdekte /tarama/plan bulunamadı)."
                    } else {
                        "Tarama planı okunuyor…"
                    })
                    .color(p.uyari),
                );
                return;
            };
            let secenekler = if plan.secenekler_dk.is_empty() {
                (30..=120).step_by(5).collect()
            } else {
                plan.secenekler_dk.clone()
            };
            let yas = self.tarama_plani_ani.elapsed();
            let mut degisen: Option<(String, u32)> = None;
            let mut tara: Option<(String, String)> = None;
            let mut sifirla: Option<Vec<String>> = None;
            let sifir_bekliyor = self.sayac_sifir_bekliyor;
            egui::ScrollArea::horizontal().id_salt("tarama_sikligi_kaydir").show(ui, |ui| {
                egui::Grid::new("tarama_sikligi_grid")
                    .num_columns(5)
                    .striped(true)
                    .min_col_width(110.0)
                    .spacing([16.0, 8.0])
                    .show(ui, |ui| {
                        for baslik in ["Kategori", "Sıklık", "Son tarama"] {
                            ui.label(RichText::new(baslik).weak().strong().size(12.5));
                        }
                        ui.horizontal(|ui| {
                            ui.label(RichText::new("Sonraki").weak().strong().size(12.5));
                            if ui
                                .add_enabled(!sifir_bekliyor, egui::Button::new("↺ Tümünü sıfırla").small())
                                .on_hover_text(
                                    "Bütün kategorilerin geri sayımını ŞİMDİDEN, seçili sürelerle yeniden başlatır.\n\
Şu an taranan kategoriye dokunulmaz (o, tarama bitince kendi süresiyle sayar).",
                                )
                                .clicked()
                            {
                                sifirla = Some(Vec::new());
                            }
                        });
                        ui.label("");
                        ui.end_row();
                        for k in &plan.kategoriler {
                            ui.vertical(|ui| {
                                ui.set_width(240.0);
                                ui.label(RichText::new(&k.ad).strong());
                                let alt = if k.tur == "ucus" {
                                    "uçuş kaynakları".to_string()
                                } else {
                                    let adlar: Vec<String> = k
                                        .urunler
                                        .iter()
                                        .map(|id| {
                                            self.pano
                                                .as_ref()
                                                .and_then(|p| p.urunler.iter().find(|u| &u.id == id))
                                                .map(|u| u.ad.clone())
                                                .unwrap_or_else(|| id.clone())
                                        })
                                        .collect();
                                    adlar.join(", ")
                                };
                                ui.add(egui::Label::new(RichText::new(&alt).weak().small()).truncate())
                                    .on_hover_text(&alt);
                            });
                            let mut secili = k.aralik_dk;
                            egui::ComboBox::from_id_salt(("tarama_aralik", &k.id))
                                .width(86.0)
                                .selected_text(format!("{} dk", secili))
                                .show_ui(ui, |ui| {
                                    for dk in &secenekler {
                                        ui.selectable_value(&mut secili, *dk, format!("{dk} dk"));
                                    }
                                });
                            if secili != k.aralik_dk {
                                degisen = Some((k.id.clone(), secili));
                            }
                            ui.vertical(|ui| {
                                ui.set_min_width(130.0);
                                ui.add(
                                    egui::Label::new(
                                        k.son_bitis
                                            .as_deref()
                                            .map(bicim::iso_tarih)
                                            .unwrap_or_else(|| "—".to_string()),
                                    )
                                    .extend(),
                                );
                                ui.label(RichText::new(ta::son_sonuc_metni(k)).weak().small());
                            });
                            match k.durum.as_str() {
                                "calisiyor" => {
                                    ui.horizontal(|ui| {
                                        ui.spinner();
                                        ui.label(RichText::new("taranıyor…").color(p.vurgu));
                                    });
                                }
                                "kuyrukta" => {
                                    ui.label(RichText::new("kuyrukta").color(p.uyari));
                                }
                                _ => {
                                    ui.horizontal(|ui| {
                                        ui.vertical(|ui| {
                                            ui.label(
                                                RichText::new(ta::geri_sayim(ta::kategori_kalan_sn(k, yas)))
                                                    .monospace(),
                                            );
                                            if let Some(sn) = &k.sonraki {
                                                ui.label(RichText::new(bicim::iso_tarih(sn)).weak().small());
                                            }
                                        });
                                        if ui
                                            .add_enabled(!sifir_bekliyor, egui::Button::new("↺ Sıfırla").small())
                                            .on_hover_text(format!(
                                                "Geri sayımı şimdiden yeniden başlat: {} dk sonra taranır.",
                                                k.aralik_dk
                                            ))
                                            .clicked()
                                        {
                                            sifirla = Some(vec![k.id.clone()]);
                                        }
                                    });
                                }
                            }
                            let kilit = self.elle_kilit_kalan(k);
                            let etiket = if kilit > 0 {
                                format!("Şimdi tara · {}", ta::geri_sayim(kilit))
                            } else {
                                "Şimdi tara".to_string()
                            };
                            if ui
                                .add_enabled(
                                    kilit == 0 && k.durum != "calisiyor" && self.elle_tarama_bekliyor.is_none(),
                                    egui::Button::new(etiket),
                                )
                                .on_hover_text("Bu kategoriyi hemen tara (aynı kategori için 10 dk'da bir)")
                                .clicked()
                            {
                                tara = Some((k.id.clone(), k.ad.clone()));
                            }
                            ui.end_row();
                        }
                    });
            });
            if let Some((id, dk)) = degisen {
                let mut a = std::collections::BTreeMap::new();
                a.insert(id, dk);
                self.tarama_plani_iste(ctx, Some(a));
            }
            if let Some((id, ad)) = tara {
                self.elle_tarama_baslat(ctx, id, ad);
            }
            if let Some(idler) = sifirla {
                self.sayac_sifirla_iste(ctx, idler);
            }
        });
    }

    /// Ayarlar → "Paralel tarama ve hız" (15.11)
    fn paralel_karti(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let p = tema::palet(ui.visuals());
        let mut govde: Option<serde_json::Value> = None;
        let bekliyor = self.gizlilik_bekliyor;
        let ayar = self.gizlilik.clone();
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.label(RichText::new("Paralel tarama ve hız").strong().size(16.0));
                if bekliyor {
                    ui.spinner();
                }
            });
            ui.label(
                RichText::new(
                    "Her sekme ayrı bir sitede arar; bir site aynı anda yalnız tek sekmede açılır (siteler bot sanmasın). \
Ayar bir sonraki taramada geçerli olur.",
                )
                .weak()
                .size(12.5),
            );
            ui.add_space(6.0);
            let Some(a) = ayar else {
                ui.label(RichText::new("Ayarlar okunuyor…").color(p.uyari));
                return;
            };
            let sistem = a.sistem.clone();
            let onerilen = sistem.as_ref().map(|s| s.onerilen_sekme);

            ui.label(RichText::new("Aynı anda sekme").strong());
            let mut sekme = a.sekme_sayisi.clamp(2, 10);
            ui.add_enabled_ui(!bekliyor, |ui| {
                egui::ComboBox::from_id_salt("paralel_sekme")
                    .selected_text(format!("{sekme} sekme"))
                    .show_ui(ui, |ui| {
                        for n in 2..=10u32 {
                            let etiket = if Some(n) == onerilen {
                                format!("{n} sekme · önerilen")
                            } else {
                                format!("{n} sekme")
                            };
                            ui.selectable_value(&mut sekme, n, etiket);
                        }
                    });
            });
            if sekme != a.sekme_sayisi.clamp(2, 10) {
                govde = Some(serde_json::json!({ "sekme_sayisi": sekme }));
            }
            if let Some(s) = &sistem {
                let tahmin = paralel_ayar::tahmini_ram_mb(sekme, s) / 1024.0;
                ui.label(
                    RichText::new(format!(
                        "Tahmini bellek: {} · Boş: {} · {} çekirdek",
                        paralel_ayar::gb_metni(tahmin),
                        paralel_ayar::gb_metni(s.bos_ram_gb),
                        s.cpu_cekirdek
                    ))
                    .weak()
                    .small(),
                );
                match paralel_ayar::uyari(sekme, s) {
                    paralel_ayar::Uyari::Yok => {}
                    paralel_ayar::Uyari::Sari(m) => {
                        ui.label(RichText::new(format!("⚠ {m}")).color(p.uyari).small());
                    }
                    paralel_ayar::Uyari::Kirmizi(m) => {
                        ui.label(RichText::new(format!("⛔ {m}")).color(p.hata).small());
                    }
                }
            }
            ui.add_space(8.0);

            ui.label(RichText::new("Aynı anda kategori").strong());
            let mut kategori = a.kategori_esz.clamp(1, 3);
            ui.add_enabled_ui(!bekliyor, |ui| {
                egui::ComboBox::from_id_salt("paralel_kategori")
                    .selected_text(format!("{kategori} kategori"))
                    .show_ui(ui, |ui| {
                        for n in 1..=3u32 {
                            ui.selectable_value(&mut kategori, n, format!("{n} kategori"));
                        }
                    });
            });
            if kategori != a.kategori_esz.clamp(1, 3) {
                govde = Some(serde_json::json!({ "kategori_esz": kategori }));
            }
            ui.label(
                RichText::new("Birden fazla kategori aynı anda taranır; sekmeler aralarında paylaşılır.")
                    .weak()
                    .small(),
            );
            ui.add_space(8.0);

            ui.label(RichText::new("Sayfa yükünü azalt").strong());
            let satirlar: [(&'static str, bool, &str, &str); 15] = [
                ("reklam_engelle", a.reklam_engelle, "Reklam ve izleyicileri engelle",
                 "~3.500 reklam/izleyici alan adına istek gitmez, sayfalar hızlanır"),
                ("gorsel_engelle", a.gorsel_engelle, "Görselleri yükleme",
                 "En hızlısı; fiyat ve başlık metinden okunur. Bir sitede engel artarsa kapat"),
                ("medya_engelle", a.medya_engelle, "Video ve sesleri yükleme",
                 "Ürün tanıtım videoları ve otomatik oynayan medya indirilmez"),
                ("yazi_tipi_engelle", a.yazi_tipi_engelle, "Web yazı tiplerini yükleme",
                 "Sistem yazı tipi kullanılır; sayfa daha erken çizilir, metin aynı okunur"),
                ("widget_engelle", a.widget_engelle, "Canlı destek, bildirim ve kampanya pencerelerini engelle",
                 "Insider, Segmentify, Intercom, Zendesk, OneSignal… betikleri yüklenmez; açılır pencereler de azalır"),
                ("animasyon_kapat", a.animasyon_kapat, "Animasyon ve geçişleri kapat",
                 "Sayfaya \"hareketi azalt\" tercihi bildirilir (sıradan erişilebilirlik ayarı); kayan bantlar, geçişler beklenmez"),
                ("gif_engelle", a.gif_engelle, "Hareketli görselleri (GIF) yükleme",
                 "Kampanya ve banner GIF'leri indirilmez; ürün görselleri ayrı ayarla yönetilir"),
                ("gomulu_engelle", a.gomulu_engelle, "Gömülü video, harita ve sosyal medya içeriklerini yükleme",
                 "YouTube/Vimeo, Google Haritalar, Facebook/Instagram/X/TikTok/Pinterest gömüleri yüklenmez"),
                ("otomatik_oynatma_kapat", a.otomatik_oynatma_kapat, "Video ve sesler kendiliğinden başlamasın",
                 "Tarayıcı ayarı: oynatma ancak tıklamayla başlar (Brave bir sonraki açılışında etkin olur)"),
                ("", false, "", ""),
                ("bot_muafiyeti", a.bot_muafiyeti, "Bota düşen sitede hız engellerini gevşet (önerilir)",
                 "Son 24 saatte doğrulama sayfası çıkan sitede yalnız reklam engeli kalır; sayfa sıradan bir ziyaretçideki gibi tam yüklenir"),
                ("", false, "", ""),
                ("chromium_yedek", a.chromium_yedek, "Engel çıkarsa görünür Chromium'da dene",
                 "Brave'de doğrulama çıkan site bir kez de ayrı Chromium penceresinde aranır"),
                ("yerel_profil_yedek", a.yerel_profil_yedek, "Son çare: kendi Brave profilimin kopyasıyla dene",
                 "Chromium da engellenirse kişisel Brave profilinin çerez kopyasıyla (asıl profile dokunulmaz) bir kez daha aranır"),
                ("", false, "", ""),
            ];
            for (alan, deger, baslik, aciklama) in satirlar {
                if alan.is_empty() {
                    ui.add_space(6.0); // ayraç
                    continue;
                }
                let mut v = deger;
                let yanit = ui.add_enabled(!bekliyor, egui::Checkbox::new(&mut v, RichText::new(baslik).strong()));
                if yanit.changed() {
                    govde = Some(serde_json::json!({ alan: v }));
                }
                ui.indent(alan, |ui| {
                    ui.label(RichText::new(aciklama).weak().small());
                });
                ui.add_space(2.0);
            }
            let mut bekle = a.dogrulama_bekle_sn;
            let etiket = |sn: u32| if sn == 0 { "Bekleme, sıradaki siteye geç".to_string() } else if sn < 60 {
                format!("{sn} sn bekle") } else { format!("{} dk bekle", sn / 60) };
            ui.horizontal(|ui| {
                ui.label(RichText::new("Doğrulama sayfası çıkınca").strong());
                ui.add_enabled_ui(!bekliyor, |ui| {
                    egui::ComboBox::from_id_salt("dogrulama_bekle")
                        .selected_text(etiket(bekle))
                        .show_ui(ui, |ui| {
                            for sn in [0u32, 30, 60, 120, 300] {
                                ui.selectable_value(&mut bekle, sn, etiket(sn));
                            }
                        });
                });
            });
            if bekle != a.dogrulama_bekle_sn {
                govde = Some(serde_json::json!({ "dogrulama_bekle_sn": bekle }));
            }
            ui.label(
                RichText::new("Beklerken \"ben robot değilim\" kutusunu sen geçebilirsin; beklemezsen tarama hızlı biter, o site atlanır.")
                    .weak()
                    .small(),
            );
        });
        if let Some(g) = govde {
            self.paralel_gonder(ctx, g);
        }
    }

    /// Ayarlar → "Tarayıcı gizliliği ve hız" (15.11)
    fn gizlilik_karti(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let p = tema::palet(ui.visuals());
        let mut degisen: Option<(&'static str, bool)> = None;
        let bekliyor = self.gizlilik_bekliyor;
        let ayar = self.gizlilik.clone();
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.label(RichText::new("Tarayıcı gizliliği ve hız").strong().size(16.0));
                if bekliyor {
                    ui.spinner();
                }
            });
            ui.label(
                RichText::new(
                    "Kazı Brave'i senin kişisel Brave'inden ayrı, kalıcı bir profilde çalışır. Analiz, bildirim ve \
açılır pencere ayarları Brave'in bir sonraki açılışında; çerez reddi hemen geçerli olur.",
                )
                .weak()
                .size(12.5),
            );
            ui.add_space(6.0);
            let Some(a) = ayar else {
                ui.label(RichText::new("Ayarlar okunuyor…").color(p.uyari));
                return;
            };
            let satirlar: [(&'static str, bool, &str, &str); 4] = [
                ("cerez_reddet", a.cerez_reddet, "Çerezleri reddet",
                 "Site açılınca çerez penceresinde \"Tümünü reddet / Yalnızca gerekli\" seçilir; seçim hatırlanır, pencere bir daha çıkmaz."),
                ("bildirim_engelle", a.bildirim_engelle, "Site bildirimlerini engelle",
                 "Siteler \"bildirim göndermek istiyor\" diye izin soramaz."),
                ("popup_engelle", a.popup_engelle, "Açılır pencereleri engelle",
                 "Sitelerin yeni pencere/sekme açması engellenir."),
                ("analiz_kapali", a.analiz_kapali, "Brave'e veri gönderme",
                 "Brave'in ürün analizi (P3A), günlük kullanım sinyali ve çökme raporu sorusu kapalı; uyarı bir daha çıkmaz."),
            ];
            for (alan, deger, baslik, aciklama) in satirlar {
                let mut v = deger;
                let yanit = ui.add_enabled(!bekliyor, egui::Checkbox::new(&mut v, RichText::new(baslik).strong()));
                if yanit.changed() {
                    degisen = Some((alan, v));
                }
                ui.indent(alan, |ui| {
                    ui.label(RichText::new(aciklama).weak().small());
                });
                ui.add_space(2.0);
            }
        });
        if let Some((alan, v)) = degisen {
            self.gizlilik_gonder(ctx, alan, v);
        }
    }

    /// Ayarlar → "Aranan siteler": kullanıcı istemediği mağazayı aramalardan çıkarır (15.11)
    fn aranan_siteler_karti(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let p = tema::palet(ui.visuals());
        let mut siteler: Vec<SiteSaglik> =
            self.siteler.iter().filter(|s| s.tur.as_deref() != Some("ucus")).cloned().collect();
        siteler.sort_by_key(|s| (!s.etkin, bicim::site_okunur(&s.ad).to_lowercase()));
        let kapali = siteler.iter().filter(|s| !s.etkin).count();
        let mut degisen: Option<(String, bool)> = None;
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.label(RichText::new("Aranan siteler").strong().size(16.0));
                ui.label(
                    RichText::new(format!("{} / {} sitede aranıyor", siteler.len() - kapali, siteler.len()))
                        .weak()
                        .size(12.5),
                );
            });
            ui.label(
                RichText::new(
                    "İşareti kaldırılan site taranmaz; Akakçe/Cimri üzerinden gelen teklifleri de elenir ve kayıtlı \
teklifleri listelerde, ortalamada ve Telegram raporunda görünmez (silinmez — yeniden işaretleyince geri gelir). \
Uçuş kaynakları bu listede değil.",
                )
                .weak()
                .size(12.5),
            );
            ui.add_space(6.0);
            if siteler.is_empty() {
                ui.label(RichText::new("Site listesi okunuyor…").color(p.uyari));
                return;
            }
            let bekleyen = self.site_islem_bekliyor.clone();
            egui::ScrollArea::horizontal().id_salt("aranan_siteler_kaydir").show(ui, |ui| {
                egui::Grid::new("aranan_siteler_grid")
                    .num_columns(4)
                    .striped(true)
                    .min_col_width(110.0)
                    .spacing([16.0, 6.0])
                    .show(ui, |ui| {
                        for baslik in ["Site", "Yöntem", "Son 24 saat", "Son başarılı"] {
                            ui.label(RichText::new(baslik).weak().strong().size(12.5));
                        }
                        ui.end_row();
                        for s in &siteler {
                            let ad = bicim::site_okunur(&s.ad);
                            ui.horizontal(|ui| {
                                ui.set_min_width(200.0);
                                let mut acik = s.etkin;
                                let bu_bekliyor = bekleyen.as_deref() == Some(s.ad.as_str());
                                let metin = if s.etkin {
                                    RichText::new(&ad)
                                } else {
                                    RichText::new(&ad).strikethrough().color(p.uyari)
                                };
                                let yanit = ui.add_enabled(bekleyen.is_none(), egui::Checkbox::new(&mut acik, metin));
                                let yanit = yanit.on_hover_text(if s.etkin {
                                    "İşareti kaldır: bu site aramalardan çıkar"
                                } else {
                                    "İşaretle: bu site yeniden aranır"
                                });
                                if yanit.changed() {
                                    degisen = Some((s.ad.clone(), acik));
                                }
                                if bu_bekliyor {
                                    ui.spinner();
                                } else if !s.etkin {
                                    ui.label(RichText::new("aramadan çıkarıldı").small().color(p.uyari));
                                }
                            });
                            let yontem = match s.katman_tercihi.as_deref() {
                                _ if !s.dogrudan => "Toplayıcı üzerinden",
                                Some("K0") => "Doğrudan (HTTP)",
                                Some("K1") => "Chromium",
                                Some("K2") => "Brave",
                                Some("K3") => "Brave + doğrulama",
                                _ => "—",
                            };
                            ui.label(RichText::new(yontem).weak());
                            let son = s.son_24s.clone().unwrap_or_default();
                            ui.label(
                                RichText::new(format!("{} teklif · {} hata", son.teklif, son.hatali)).weak().small(),
                            );
                            ui.label(
                                RichText::new(s.son_basari.as_deref().map(bicim::iso_tarih).unwrap_or_else(|| "—".into()))
                                    .weak()
                                    .small(),
                            );
                            ui.end_row();
                        }
                    });
            });
        });
        if let Some((ad, etkin)) = degisen {
            self.site_etkin_gonder(ctx, ad, etkin);
        }
    }

    /// Ayarlar → "Telegram" (token arayüzde açık yazılmaz)
    fn telegram_karti(&mut self, ctx: &egui::Context, ui: &mut egui::Ui) {
        let p = tema::palet(ui.visuals());
        if !self.telegram_formu.istendi {
            self.telegram_ayar_iste(ctx, None);
        }
        let mut kaydet = false;
        let mut test = false;
        let mut rapor = false;
        tema::kart_cerceve(ui).show(ui, |ui| {
            ui.set_min_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.label(RichText::new("Telegram").strong().size(16.0));
                if self.telegram_formu.bekliyor {
                    ui.spinner();
                }
            });
            let f = &mut self.telegram_formu;
            let Some(yuklu) = f.yuklu.clone() else {
                match &f.mesaj {
                    Some((_, m)) => ui.label(RichText::new(m).color(p.hata)),
                    None => ui.label(RichText::new("Telegram ayarı okunuyor…").weak()),
                };
                return;
            };
            ui.label(
                RichText::new(match yuklu.kaynak.as_deref() {
                    Some("evren-asistan") => "Ayar evren-asistan dosyasından okunuyor (kaydedince kazi ayarı olur)",
                    Some("kazi") => "kazi ayarı",
                    _ => "Henüz Telegram ayarı yok",
                })
                .weak()
                .size(12.5),
            );
            ui.add_space(6.0);
            egui::Grid::new("telegram_grid").num_columns(2).spacing([18.0, 10.0]).show(ui, |ui| {
                ui.label("Bot:");
                ui.label(yuklu.bot.as_deref().map(|b| format!("@{b}")).unwrap_or_else(|| "—".into()));
                ui.end_row();

                ui.label("Chat ID:");
                ui.add(egui::TextEdit::singleline(&mut f.chat_id).desired_width(180.0));
                ui.end_row();

                ui.label("Token:");
                ui.horizontal(|ui| {
                    if f.token_degistir {
                        ui.add(
                            egui::TextEdit::singleline(&mut f.yeni_token)
                                .password(true)
                                .desired_width(260.0)
                                .hint_text("yeni token (boş = değişmez)"),
                        );
                        if ui.small_button("Vazgeç").clicked() {
                            f.token_degistir = false;
                            f.yeni_token.clear();
                        }
                    } else {
                        ui.label(
                            RichText::new(if yuklu.token_var {
                                yuklu.token_maskeli.clone().unwrap_or_else(|| "••••".into())
                            } else {
                                "yok".into()
                            })
                            .monospace(),
                        );
                        if ui.small_button("Değiştir").clicked() {
                            f.token_degistir = true;
                        }
                    }
                });
                ui.end_row();

                ui.label("Bildirimler:");
                ui.checkbox(&mut f.etkin, "açık (alarm ve rapor mesajları)");
                ui.end_row();

                ui.label("Düzenli rapor:");
                let araliklar = if yuklu.aralik_secenekleri.is_empty() {
                    vec![0, 30, 60, 120, 180, 360]
                } else {
                    yuklu.aralik_secenekleri.clone()
                };
                let etiket = |dk: u32| if dk == 0 { "Kapalı".to_string() } else { format!("{dk} dk'da bir") };
                egui::ComboBox::from_id_salt("rapor_aralik")
                    .selected_text(etiket(f.rapor_aralik_dk))
                    .show_ui(ui, |ui| {
                        for dk in araliklar {
                            ui.selectable_value(&mut f.rapor_aralik_dk, dk, etiket(dk));
                        }
                    });
                ui.end_row();

                ui.label("Sessiz saatler:");
                ui.vertical(|ui| {
                    ui.horizontal(|ui| {
                        let saatler = ta::saat_secenekleri();
                        for (ad, deger) in [("sessiz_bas", &mut f.sessiz_bas), ("sessiz_bit", &mut f.sessiz_bit)] {
                            egui::ComboBox::from_id_salt(ad)
                                .width(70.0)
                                .selected_text(deger.clone())
                                .show_ui(ui, |ui| {
                                    for s in &saatler {
                                        ui.selectable_value(deger, s.clone(), s);
                                    }
                                });
                            if ad == "sessiz_bas" {
                                ui.label("→");
                            }
                        }
                    });
                    ui.label(
                        RichText::new("Sessiz saatlerde özet gitmez, ALARM mesajları yine gider.")
                            .weak()
                            .small(),
                    );
                });
                ui.end_row();
            });
            ui.add_space(8.0);
            ui.horizontal_wrapped(|ui| {
                let hazir = !f.bekliyor;
                kaydet = ui.add_enabled(hazir, egui::Button::new("💾 Kaydet")).clicked();
                test = ui.add_enabled(hazir, egui::Button::new("✉ Test mesajı gönder")).clicked();
                rapor = ui.add_enabled(hazir, egui::Button::new("📊 Raporu şimdi gönder")).clicked();
            });
            if let Some((iyi, m)) = &f.mesaj {
                ui.add_space(4.0);
                ui.label(RichText::new(m).color(if *iyi { p.basari } else { p.hata }));
            }
        });
        if kaydet {
            match self.telegram_formu.istek() {
                Ok(istek) => {
                    self.telegram_formu.mesaj = None;
                    self.telegram_ayar_iste(ctx, Some(istek));
                }
                Err(e) => self.telegram_formu.mesaj = Some((false, format!("✗ {e}"))),
            }
        }
        if test {
            self.telegram_formu.mesaj = None;
            self.telegram_gonder(ctx, false);
        }
        if rapor {
            self.telegram_formu.mesaj = None;
            self.telegram_gonder(ctx, true);
        }
    }
}

impl KaziArayuzApp {
    fn klavye_kisayollarini_isle(&mut self, ctx: &egui::Context) {
        let mut f5 = false;
        let mut ctrl_f = false;
        let mut esc = false;
        let mut enter = false;
        let mut sekme_no: Option<u8> = None;

        ctx.input(|i| {
            f5 = i.key_pressed(egui::Key::F5);
            esc = i.key_pressed(egui::Key::Escape);
            enter = i.key_pressed(egui::Key::Enter);
            if i.modifiers.command || i.modifiers.ctrl {
                ctrl_f = i.key_pressed(egui::Key::F);
                let tuslar = [
                    (egui::Key::Num1, 1),
                    (egui::Key::Num2, 2),
                    (egui::Key::Num3, 3),
                    (egui::Key::Num4, 4),
                    (egui::Key::Num5, 5),
                    (egui::Key::Num6, 6),
                    (egui::Key::Num7, 7),
                    (egui::Key::Num8, 8),
                ];
                for (k, no) in tuslar {
                    if i.key_pressed(k) {
                        sekme_no = Some(no);
                    }
                }
            }
        });

        if f5 {
            self.genel_yenileme_baslat(ctx);
        }
        if ctrl_f {
            self.arama_odak_iste = true;
        }
        if let Some(no) = sekme_no {
            self.aktif_sekme = match no {
                1 => Sekme::TakipListesi,
                2 => Sekme::UrunDetayi,
                3 => Sekme::SeyahatPlanla,
                4 => Sekme::Ucuslar,
                5 => Sekme::SiteSagligi,
                6 => Sekme::AraTanimla,
                7 => Sekme::Ayarlar,
                8 => Sekme::Kayitlar,
                _ => self.aktif_sekme,
            };
        }
        if esc && self.alarm_duzenleme.is_none() && self.takip_cikar_onayi.is_none() {
            if self.acik_taksit_penceresi.is_some() {
                self.acik_taksit_penceresi = None;
            } else {
                match self.aktif_sekme {
                    Sekme::TakipListesi => self.takip_arama.clear(),
                    Sekme::UrunDetayi => self.detay_arama.clear(),
                    Sekme::Ucuslar => self.ucus_arama.clear(),
                    Sekme::SiteSagligi => self.site_arama.clear(),
                    _ => {}
                }
            }
        }
        if enter && !ctx.egui_wants_keyboard_input() {
            if self.aktif_sekme == Sekme::TakipListesi {
                if let Some(id) = self.secili_urun_id.clone() {
                    self.aktif_sekme = Sekme::UrunDetayi;
                    self.urun_detay_yukle(ctx, id);
                }
            } else if self.aktif_sekme == Sekme::UrunDetayi {
                if let Some(url) = self.secili_teklif_id.and_then(|sid| {
                    self.urun_detay.as_ref().and_then(|d| {
                        d.teklifler
                            .iter()
                            .find(|t| t.teklif_id == Some(sid))
                            .and_then(|t| t.url.clone())
                    })
                }) {
                    if !url.is_empty() {
                        baglanti_ac(&url);
                    }
                }
            }
        }

        // Yakınlaştırma (Ctrl + / Ctrl - / Ctrl 0 egui zoom_factor üzerinden) değiştiyse kaydet
        let aktif_zoom = (ctx.zoom_factor() * 100.0).round() / 100.0;
        let hedef_zoom = aktif_zoom.clamp(0.5, 2.5);
        if (hedef_zoom - self.ayarlar.yakinlastirma).abs() > 0.01 {
            self.ayarlar.yakinlastirma = hedef_zoom;
            if self.goruntu_hedefi.is_none() {
                let _ = self.ayarlar.kaydet();
            }
        }
    }

    fn toasts_ciz(&mut self, ctx: &egui::Context) {
        self.toastlar
            .retain(|t| t.olusturulma.elapsed() < Duration::from_secs(4));
        if self.toastlar.is_empty() {
            return;
        }
        let p = tema::palet(&ctx.global_style().visuals);
        egui::Area::new(egui::Id::unique("kazi_toast_alani"))
            .anchor(egui::Align2::RIGHT_BOTTOM, [-16.0, -16.0])
            .order(egui::Order::Foreground)
            .interactable(false)
            .show(ctx, |ui| {
                ui.with_layout(egui::Layout::bottom_up(egui::Align::Max), |ui| {
                    ui.spacing_mut().item_spacing.y = 6.0;
                    for toast in self.toastlar.iter().rev() {
                        let (dolgu, kenar, yazi) = match toast.tur {
                            ToastTuru::Basari => (p.basari_dolgu, p.basari, p.basari),
                            ToastTuru::Hata => (p.hata_dolgu, p.hata, p.hata),
                            ToastTuru::Bilgi => (p.kart_arka, p.vurgu, p.vurgu),
                        };
                        egui::Frame::NONE
                            .fill(dolgu)
                            .stroke(Stroke::new(1.2, kenar))
                            .corner_radius(8)
                            .inner_margin(egui::Margin::symmetric(14, 10))
                            .shadow(p.golge)
                            .show(ui, |ui| {
                                ui.set_max_width(360.0);
                                ui.label(RichText::new(&toast.metin).color(yazi).size(13.5).strong());
                            });
                    }
                });
            });
    }
}

impl eframe::App for KaziArayuzApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        self.mesajlari_isle(&ctx);
        self.goruntu_isle(&ctx);
        self.klavye_kisayollarini_isle(&ctx);

        // Ekran verisi arka planda 30 sn'de bir sessizce okunur (gerçek tarama zamanlayıcıda)
        if self.son_yenileme_ani.elapsed() >= Duration::from_secs(ta::VERI_OKUMA_SN) {
            self.genel_yenileme_baslat(&ctx);
        }
        // Tarama planı: 30 sn'de bir; bir tarama sürerken / sırası gelmişken 10 sn'de bir
        let plan_sik = self.tarama_plani.as_ref().is_some_and(|p| {
            p.kategoriler.iter().any(|k| {
                k.durum != "bekliyor" || ta::kategori_kalan_sn(k, self.tarama_plani_ani.elapsed()) == 0
            })
        });
        let plan_aralik = if plan_sik { ta::PLAN_OKUMA_CALISIRKEN_SN } else { ta::PLAN_OKUMA_SN };
        if self.son_plan_istegi.elapsed() >= Duration::from_secs(plan_aralik) {
            self.tarama_plani_ani_tazele_iste(&ctx);
        }
        if let Some(m) = self.http_turu.tazele_gerekirse(&ctx, &self.veri_kaynagi) {
            self.toast_ekle(ToastTuru::Basari, m);
            self.genel_yenileme_baslat(&ctx);
        }
        ctx.request_repaint_after(Duration::from_secs(1));

        let pencere_genisligi = ctx.input(|i| i.content_rect().width());
        let duzen = duyarli_duzen_hesapla(pencere_genisligi);

        // Üst Çubuk
        egui::Panel::top("ust_cubuk_panel")
            .frame(
                egui::Frame::side_top_panel(&ctx.global_style())
                    .inner_margin(egui::Margin::symmetric(14, 8)),
            )
            .show(ui, |ui| {
                self.ust_cubuk_ciz(&ctx, ui);
            });

        // Sol Kenar Çubuğu (Sekmeler)
        egui::Panel::left("sol_sekme_paneli")
            .resizable(false)
            .exact_size(duzen.yan_menu_genisligi)
            .frame(
                egui::Frame::side_top_panel(&ctx.global_style())
                    .inner_margin(egui::Margin::symmetric(8, 10)),
            )
            .show(ui, |ui| {
                let p = tema::palet(ui.visuals());
                if !duzen.yan_menu_dar {
                    ui.label(RichText::new("EKRANLAR").weak().size(11.5).strong());
                    ui.add_space(4.0);
                }

                let sekmeler = [
                    (Sekme::TakipListesi, "📋", "Takip Listesi", "Ctrl+1"),
                    (Sekme::UrunDetayi, "🔎", "Ürün Detayı", "Ctrl+2"),
                    (Sekme::SeyahatPlanla, "🗺", "Seyahat Planla", "Ctrl+3"),
                    (Sekme::Ucuslar, "✈", "Uçuşlar", "Ctrl+4"),
                    (Sekme::SiteSagligi, "🛡", "Site Sağlığı", "Ctrl+5"),
                    (Sekme::AraTanimla, "➕", "Ara / Tanımla", "Ctrl+6"),
                    (Sekme::Ayarlar, "⚙", "Ayarlar", "Ctrl+7"),
                    (Sekme::Kayitlar, "📜", "Kayıtlar", "Ctrl+8"),
                ];

                for (sekme, simge, ad, kisayol) in sekmeler {
                    let secili = self.aktif_sekme == sekme;
                    let metin = if duzen.yan_menu_dar {
                        simge.to_string()
                    } else {
                        format!("{simge}  {ad}")
                    };
                    let mut dugme = egui::Button::new(
                        RichText::new(metin)
                            .size(14.0)
                            .color(if secili {
                                p.vurgu_yazi
                            } else {
                                ui.visuals().text_color()
                            })
                            .strong(),
                    )
                    .corner_radius(16.0);
                    if secili {
                        dugme = dugme
                            .fill(p.vurgu_dolgu)
                            .stroke(Stroke::new(1.0, p.vurgu));
                    } else {
                        dugme = dugme.fill(Color32::TRANSPARENT).stroke(Stroke::NONE);
                    }
                    let resp = ui
                        .add_sized([ui.available_width(), 34.0], dugme)
                        .on_hover_text(format!("{ad} ({kisayol})"));
                    if resp.clicked() {
                        self.aktif_sekme = sekme;
                    }
                    ui.add_space(3.0);
                }
            });

        // Sağ "Tarayıcılar" paneli
        if self.tarayici_paneli.gorunur {
            egui::Panel::right("tarayici_paneli")
                .resizable(true)
                .default_size(320.0)
                .min_size(240.0)
                .frame(
                    egui::Frame::side_top_panel(&ctx.global_style())
                        .inner_margin(egui::Margin::symmetric(10, 10)),
                )
                .show(ui, |ui| {
                    self.tarayici_paneli.ciz(&ctx, ui, &self.veri_kaynagi);
                });
        }

        // Ana İçerik Paneli
        egui::CentralPanel::default()
            .frame(
                egui::Frame::central_panel(&ctx.global_style())
                    .inner_margin(egui::Margin::symmetric(16, 12)),
            )
            .show(ui, |ui| match self.aktif_sekme {
                Sekme::TakipListesi => self.ekran_takip_listesi(&ctx, ui),
                Sekme::UrunDetayi => self.ekran_urun_detayi(&ctx, ui),
                Sekme::SeyahatPlanla => {
                    let eylem = self.plan_durumu.ciz(&ctx, ui, &self.veri_kaynagi);
                    if eylem.seyahati_yenile {
                        self.genel_yenileme_baslat(&ctx);
                    }
                    if eylem.ucuslara_git {
                        self.aktif_sekme = Sekme::Ucuslar;
                    }
                }
                Sekme::Ucuslar => self.ekran_ucuslar(ui),
                Sekme::SiteSagligi => self.ekran_site_sagligi(ui),
                Sekme::AraTanimla => self.ekran_ara_tanimla(&ctx, ui),
                Sekme::Ayarlar => {
                    let mut kaydirma = egui::ScrollArea::vertical()
                        .id_salt("ayarlar_kaydir")
                        .auto_shrink([false, false]);
                    if let Some(px) = self.baslangic_kaydir.take() {
                        kaydirma = kaydirma.vertical_scroll_offset(px);
                    }
                    kaydirma.show(ui, |ui| self.ekran_ayarlar(&ctx, ui));
                }
                Sekme::Kayitlar => self.kayit_ekrani.ciz(&ctx, ui, &self.veri_kaynagi),
            });

        self.taksit_penceresini_ciz(&ctx);
        self.tarayici_paneli.pencere_ciz(&ctx, &self.veri_kaynagi);
        self.toasts_ciz(&ctx);
    }
}

impl KaziArayuzApp {
    fn taksit_penceresini_ciz(&mut self, ctx: &egui::Context) {
        let Some(pencere) = self.acik_taksit_penceresi.clone() else {
            return;
        };
        if ctx.input(|i| i.key_pressed(egui::Key::Escape)) {
            self.acik_taksit_penceresi = None;
            return;
        }

        let kim = match &pencere.satici {
            Some(sat) if !sat.trim().is_empty() => format!("{} / {}", pencere.site, sat),
            _ => pencere.site.clone(),
        };
        let sepet_str = pencere
            .sepet_fiyati
            .map(bicim::tl)
            .unwrap_or_else(|| "—".to_string());
        let pencere_baslik = format!("{} — {} · {}", pencere.urun_ad, kim, sepet_str);

        let mut acik = true;
        egui::Window::new("Taksit ve Vade Farkı")
            .id(egui::Id::unique("taksit_vade_penceresi"))
            .open(&mut acik)
            .collapsible(false)
            .resizable(true)
            .default_width(580.0)
            .show(ctx, |ui| {
                ui.label(RichText::new(&pencere_baslik).strong().size(15.0));
                ui.add_space(6.0);

                taksit_tablosu_ciz(ui, &pencere.taksit, pencere.sepet_fiyati);

                if pencere.taksit.tablo.is_empty() {
                    if let Some(url) = &pencere.url {
                        if !url.is_empty() {
                            ui.add_space(6.0);
                            if ui.button("Sitede aç ↪").on_hover_text(url).clicked() {
                                baglanti_ac(url);
                            }
                        }
                    }
                }

                ui.add_space(8.0);
                ui.separator();
                let gozlem = pencere
                    .gozlem_zamani
                    .as_deref()
                    .map(bicim::iso_tarih)
                    .unwrap_or_else(|| "—".to_string());
                ui.label(
                    RichText::new(format!(
                        "Kaynak: {} ürün sayfası · gözlem {}",
                        pencere.site, gozlem
                    ))
                    .small()
                    .weak(),
                );
            });

        if !acik {
            self.acik_taksit_penceresi = None;
        }
    }
}

// ======================= Görsel Yardımcılar =======================

/// Yeniden boyutlandırılabilir tabloların sütun genişlikleri eframe hafızasında kalıcı tutulur; pencere daralınca
/// geniş pencereden kalan genişlikler tabloyu sağdan kırpıyordu. Tablo kimliğine 80 px'lik genişlik kovası eklenir:
/// her genişlikte sütunlar sığacak biçimde yeniden hesaplanır, kullanıcının elle ayarı o genişlik için hatırlanır.
fn genislik_kovasi(ui: &egui::Ui) -> i32 {
    (ui.available_width() / 80.0).floor() as i32
}

fn hedef_altinda_rozeti_ciz(ui: &mut egui::Ui) {
    ui.label(RichText::new("🎯").color(Color32::from_rgb(46, 204, 113)))
        .on_hover_text("Teyitli ve hedef fiyatın altında");
}

fn taksit_onizleme_metni(taksit: &TaksitBilgisi) -> String {
    if taksit.tablo.is_empty() {
        return match taksit.pesin_fiyatina.filter(|&p| p > 0) {
            Some(p) => format!("{p}× peşin fiyatına — kart tablosu için tıklayın"),
            None => "Kart bazlı taksit detayı için tıklayın".to_string(),
        };
    }
    let mut sirali = taksit.tablo.clone();
    taksit_satirlarini_sirala(&mut sirali);
    let mut satirlar: Vec<String> = sirali
        .iter()
        .take(4)
        .map(|s| {
            let banka = s.banka.as_deref().unwrap_or("Kart");
            let vade = match s.vade_farki_yuzde.filter(|v| v.is_finite()) {
                Some(v) if v <= 0.000_1 => "Vade farksız".to_string(),
                Some(v) => bicim::yuzde(v),
                None => "—".to_string(),
            };
            format!("{} {}× · {}", banka, s.taksit_sayisi, vade)
        })
        .collect();
    if taksit.tablo.len() > 4 {
        satirlar.push(format!("… (+{} seçenek)", taksit.tablo.len() - 4));
    }
    satirlar.push("(Tümünü görmek için tıklayın)".to_string());
    satirlar.join("\n")
}

fn taksit_ozet_hucre_ciz(ui: &mut egui::Ui, taksit: &TaksitBilgisi) -> bool {
    let ozet = taksit_ozeti(taksit);
    let renk = if ozet.contains("peşin") {
        Color32::from_rgb(46, 204, 113)
    } else if ozet.contains("vade") {
        Color32::from_rgb(243, 156, 18)
    } else {
        ui.visuals().weak_text_color()
    };
    let onizleme = taksit_onizleme_metni(taksit);
    ui.add(
        egui::Label::new(RichText::new(ozet).color(renk).underline())
            .truncate()
            .sense(Sense::click()),
    )
    .on_hover_cursor(egui::CursorIcon::PointingHand)
    .on_hover_text(onizleme)
    .clicked()
}

fn taksit_tablosu_ciz(ui: &mut egui::Ui, taksit: &TaksitBilgisi, sepet_fiyati: Option<f64>) {
    if taksit.tablo.is_empty() {
        let mesaj = match taksit.pesin_fiyatina.filter(|&p| p > 0) {
            Some(p) => format!(
                "Kart bazlı taksit tablosu henüz alınmadı. Bilinen: {}× peşin fiyatına (hangi kartlarla olduğu site sayfasında).",
                p
            ),
            None => {
                "Kart bazlı taksit tablosu henüz alınmadı (ayrıntılar site sayfasında).".to_string()
            }
        };
        ui.label(RichText::new(mesaj).weak());
        return;
    }

    let gruplar = taksit_satirlarini_bankaya_gore_grupla(&taksit.tablo);
    egui::Grid::new(ui.scope_id().with("taksit_tablo_grid"))
        .striped(true)
        .min_col_width(80.0)
        .spacing([14.0, 6.0])
        .show(ui, |ui| {
            ui.strong("Kart / Banka");
            ui.strong("Taksit");
            ui.strong("Aylık");
            ui.strong("Toplam");
            ui.strong("Vade farkı");
            ui.end_row();

            for (banka_ad, satirlar) in gruplar {
                for (i, satir) in satirlar.iter().enumerate() {
                    if i == 0 {
                        ui.strong(&banka_ad);
                    } else {
                        ui.label("");
                    }
                    ui.label(format!("{}×", satir.taksit_sayisi));
                    ui.label(satir.aylik.map(bicim::tl).unwrap_or_else(|| "—".to_string()));
                    ui.label(satir.toplam.map(bicim::tl).unwrap_or_else(|| "—".to_string()));
                    match satir.vade_farki_yuzde.filter(|v| v.is_finite()) {
                        Some(v) if v <= 0.000_1 => {
                            ui.label(
                                RichText::new("Vade farksız")
                                    .color(Color32::from_rgb(46, 204, 113))
                                    .strong(),
                            );
                        }
                        Some(v) => {
                            let yuzde_str = bicim::yuzde(v);
                            let tl_fark = taksit_vade_tl_farki(satir, sepet_fiyati)
                                .filter(|&f| f > 0.5)
                                .map(|f| format!(" (+{})", bicim::tl(f)))
                                .unwrap_or_default();
                            ui.label(
                                RichText::new(format!("{}{}", yuzde_str, tl_fark))
                                    .color(Color32::from_rgb(243, 156, 18)),
                            );
                        }
                        None => {
                            if let Some(fark) =
                                taksit_vade_tl_farki(satir, sepet_fiyati).filter(|&f| f > 0.5)
                            {
                                ui.label(
                                    RichText::new(format!("+{}", bicim::tl(fark)))
                                        .color(Color32::from_rgb(243, 156, 18)),
                                );
                            } else {
                                ui.label("—");
                            }
                        }
                    }
                    ui.end_row();
                }
            }
        });
}

fn siralanabilir_baslik<K: Copy + PartialEq>(
    ui: &mut egui::Ui,
    etiket: &str,
    sutun_kriter: K,
    aktif_kriter: &mut K,
    artan: &mut bool,
) -> bool {
    let secili = *aktif_kriter == sutun_kriter;
    let metin = if secili {
        if *artan {
            format!("{etiket} ▲")
        } else {
            format!("{etiket} ▼")
        }
    } else {
        etiket.to_string()
    };
    let yazi = if secili {
        RichText::new(metin)
            .strong()
            .color(Color32::from_rgb(100, 200, 255))
    } else {
        RichText::new(metin).strong()
    };
    let resp = ui
        .add(egui::Button::selectable(secili, yazi).frame(false))
        .on_hover_text(format!(
            "{etiket} sütununa göre sırala (ikinci tıklama yön değiştirir)"
        ));
    if resp.clicked() {
        if secili {
            *artan = !*artan;
        } else {
            *aktif_kriter = sutun_kriter;
            *artan = true;
        }
        true
    } else {
        false
    }
}

fn siralama_cubugu_ciz<K: Copy + PartialEq>(
    ui: &mut egui::Ui,
    id_salt: &str,
    secenekler: &[(K, &str)],
    aktif_kriter: &mut K,
    artan: &mut bool,
    mevcut_etiket: &str,
) -> bool {
    let mut degisti = false;
    ui.horizontal(|ui| {
        ui.strong("Sırala:");
        egui::ComboBox::from_id_salt(id_salt)
            .selected_text(mevcut_etiket)
            .show_ui(ui, |ui| {
                for &(kriter, etiket) in secenekler {
                    if ui.selectable_label(*aktif_kriter == kriter, etiket).clicked()
                        && *aktif_kriter != kriter
                    {
                        *aktif_kriter = kriter;
                        *artan = true;
                        degisti = true;
                    }
                }
            });
        let yon_etiket = if *artan {
            "▲ Azdan çoğa"
        } else {
            "▼ Çoktan aza"
        };
        if ui
            .button(yon_etiket)
            .on_hover_text("Sıralama yönü: Azdan çoğa / Çoktan aza")
            .clicked()
        {
            *artan = !*artan;
            degisti = true;
        }
    });
    degisti
}

fn ucus_anahtari(s: &UcusSecenek) -> String {
    if !s.id.is_empty() {
        s.id.clone()
    } else {
        format!(
            "{}|{}|{}|{}|{}",
            s.tarih, s.kalkis, s.kalkis_nokta, s.varis_nokta, s.firma
        )
    }
}

fn ucus_secenek_tablosu_ciz(
    ui: &mut egui::Ui,
    secenekler: &[UcusSecenek],
    aktif_siralama: &mut UcusSiralama,
    artan: &mut bool,
    secili_ucus_anahtari: &mut Option<String>,
    secili_ucus_bilgisi: &mut Option<UcusSecenek>,
    gizle_ikincil: bool,
) -> bool {
    let mut degisti = false;
    let ctx = ui.ctx().clone();
    let p = tema::palet(ui.visuals());

    ui.scope(|ui| {
        ui.set_max_height(195.0);
        egui::ScrollArea::horizontal()
            .auto_shrink([false, true])
            .show(ui, |ui| {
                ui.spacing_mut().item_spacing.x = 6.0;

                let kova = genislik_kovasi(ui);
                let mut tablo = TableBuilder::new(ui)
                    .id_salt(("ucus_tablosu", gizle_ikincil, kova))
                    .striped(true)
                    .resizable(!gizle_ikincil)
                    .sense(Sense::click())
                    .min_scrolled_height(95.0)
                    .max_scroll_height(165.0)
                    .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
                    .column(Column::exact(78.0)) // Tarih
                    .column(Column::exact(104.0)) // Kalkış → Varış
                    .column(Column::remainder().at_least(110.0).clip(true)) // Rota (Aktarma)
                    .column(Column::exact(62.0)) // Süre
                    .column(Column::initial(120.0).at_least(80.0).clip(true)) // Firma
                    .column(Column::exact(84.0)); // Kişi Başı
                if !gizle_ikincil {
                    tablo = tablo
                        .column(Column::exact(84.0)) // Toplam
                        .column(Column::exact(44.0)) // Uyarı
                        .column(Column::exact(70.0)) // Kaynak
                        .column(Column::exact(96.0)); // Son tarama
                }
                tablo = tablo.column(Column::exact(48.0)); // Link

                tablo
                    .header(26.0, |mut header| {
                        let mut sutunlar: Vec<(&str, UcusSiralama)> = vec![
                            ("Tarih", UcusSiralama::Tarih),
                            ("Kalkış→Varış", UcusSiralama::KalkisSaati),
                            ("Rota (Aktarma)", UcusSiralama::AktarmaSayisi),
                            ("Süre", UcusSiralama::ToplamSure),
                            ("Firma", UcusSiralama::Firma),
                            ("Kişi Başı", UcusSiralama::Fiyat),
                        ];
                        if !gizle_ikincil {
                            sutunlar.push(("Toplam", UcusSiralama::ToplamFiyat));
                            sutunlar.push(("Uyarı", UcusSiralama::UyariSayisi));
                            sutunlar.push(("Kaynak", UcusSiralama::Kaynak));
                            sutunlar.push(("Son tarama", UcusSiralama::SonTarama));
                        }
                        for (baslik, kriter) in sutunlar {
                            header.col(|ui| {
                                if siralanabilir_baslik(ui, baslik, kriter, aktif_siralama, artan) {
                                    degisti = true;
                                }
                            });
                        }
                        header.col(|ui| {
                            ui.strong("Link");
                        });
                    })
                    .body(|mut body| {
                        for s in secenekler {
                            let anahtar = ucus_anahtari(s);
                            let secili = secili_ucus_anahtari.as_deref() == Some(anahtar.as_str());
                            let link_opt = s.link.clone();

                            body.row(28.0, |mut row| {
                                row.set_selected(secili);

                                row.col(|ui| {
                                    let r = ui.add(
                                        egui::Label::new(bicim::iso_tarih(&s.tarih))
                                            .sense(Sense::click()),
                                    );
                                    if r.clicked() {
                                        *secili_ucus_anahtari = Some(anahtar.clone());
                                        *secili_ucus_bilgisi = Some(s.clone());
                                    }
                                });
                                row.col(|ui| {
                                    let r = ui.add(
                                        egui::Label::new(bicim::kalkis_varis_saat(
                                            &s.kalkis, &s.varis,
                                        ))
                                        .sense(Sense::click()),
                                    );
                                    if r.clicked() {
                                        *secili_ucus_anahtari = Some(anahtar.clone());
                                        *secili_ucus_bilgisi = Some(s.clone());
                                    }
                                });
                                row.col(|ui| {
                                    let tam_rota = if s.aktarmalar.is_empty() && s.aktarma_sayisi == 0 {
                                        format!("{}→{} (Direkt)", s.kalkis_nokta, s.varis_nokta)
                                    } else {
                                        format!(
                                            "{}→{}→{} ({} akt.)",
                                            s.kalkis_nokta,
                                            s.aktarmalar.join("→"),
                                            s.varis_nokta,
                                            s.aktarma_sayisi.max(s.aktarmalar.len() as u32)
                                        )
                                    };
                                    let mut hover_rota = tam_rota.clone();
                                    if s.ayri_bilet == Some(true) {
                                        hover_rota.push_str("\n• Ayrı bilet");
                                    }
                                    if let Some(beklemeler) = &s.aktarma_bekleme_dk {
                                        if !beklemeler.is_empty() {
                                            let top_dk: i64 = beklemeler.iter().sum();
                                            hover_rota.push_str(&format!(
                                                "\n• Aktarma bekleme: {} dk",
                                                top_dk
                                            ));
                                        }
                                    }
                                    let r = ui
                                        .add(
                                            egui::Label::new(&tam_rota)
                                                .truncate()
                                                .sense(Sense::click()),
                                        )
                                        .on_hover_text(hover_rota);
                                    if r.clicked() {
                                        *secili_ucus_anahtari = Some(anahtar.clone());
                                        *secili_ucus_bilgisi = Some(s.clone());
                                    }
                                    if r.double_clicked() {
                                        if let Some(l) = &link_opt {
                                            if !l.is_empty() {
                                                baglanti_ac(l);
                                            }
                                        }
                                    }
                                });
                                row.col(|ui| {
                                    ui.with_layout(
                                        egui::Layout::right_to_left(egui::Align::Center),
                                        |ui| {
                                            ui.label(
                                                RichText::new(bicim::dakika(s.sure_dk)).monospace(),
                                            );
                                        },
                                    );
                                });
                                row.col(|ui| {
                                    let tam_firma = bicim::firma_metni(s.arac.as_deref(), &s.firma);
                                    ui.add(egui::Label::new(&tam_firma).truncate())
                                        .on_hover_text(&s.firma);
                                });
                                row.col(|ui| {
                                    ui.with_layout(
                                        egui::Layout::right_to_left(egui::Align::Center),
                                        |ui| {
                                            ui.label(
                                                RichText::new(bicim::tl(s.kisi_basi))
                                                    .strong()
                                                    .monospace(),
                                            );
                                        },
                                    );
                                });
                                if !gizle_ikincil {
                                    row.col(|ui| {
                                        ui.with_layout(
                                            egui::Layout::right_to_left(egui::Align::Center),
                                            |ui| {
                                                ui.label(
                                                    RichText::new(bicim::tl(s.toplam)).monospace(),
                                                );
                                            },
                                        );
                                    });
                                    row.col(|ui| {
                                        if s.uyarilar.is_empty() {
                                            ui.label("—");
                                        } else {
                                            let birlesik = s.uyarilar.join("\n");
                                            ui.label(
                                                RichText::new(format!("⚠ {}", s.uyarilar.len()))
                                                    .color(p.uyari)
                                                    .strong(),
                                            )
                                            .on_hover_text(birlesik);
                                        }
                                    });
                                    row.col(|ui| {
                                        ui.add(egui::Label::new(&s.kaynak).truncate())
                                            .on_hover_text(&s.kaynak);
                                    });
                                    row.col(|ui| match s.gozlem_zamani.as_deref() {
                                        Some(z) if !z.is_empty() => {
                                            ui.label(RichText::new(bicim::iso_tarih(z)).small())
                                                .on_hover_text(format!("Bu uçuş en son {} tarihinde görüldü", bicim::iso_tarih(z)));
                                        }
                                        _ => {
                                            ui.label("—");
                                        }
                                    });
                                }
                                row.col(|ui| {
                                    if let Some(link) = &s.link {
                                        if !link.is_empty()
                                            && ui.small_button("Aç").on_hover_text(link).clicked()
                                        {
                                            baglanti_ac(link);
                                        }
                                    }
                                });

                                let resp = row.response();
                                if resp.clicked() {
                                    *secili_ucus_anahtari = Some(anahtar.clone());
                                    *secili_ucus_bilgisi = Some(s.clone());
                                }
                                if resp.double_clicked() {
                                    if let Some(l) = &link_opt {
                                        if !l.is_empty() {
                                            baglanti_ac(l);
                                        }
                                    }
                                }
                                resp.context_menu(|ui| {
                                    let url_var =
                                        link_opt.as_deref().is_some_and(|u| !u.is_empty());
                                    if ui
                                        .add_enabled(url_var, egui::Button::new("🌐 Sitede aç"))
                                        .clicked()
                                    {
                                        if let Some(u) = &link_opt {
                                            baglanti_ac(u);
                                        }
                                        ui.close();
                                    }
                                    if ui
                                        .add_enabled(url_var, egui::Button::new("📋 Linki kopyala"))
                                        .clicked()
                                    {
                                        if let Some(u) = &link_opt {
                                            ctx.copy_text(u.clone());
                                        }
                                        ui.close();
                                    }
                                    if ui
                                        .button("↪ Bu uçuşu seç → yan tur çakışmasını göster")
                                        .clicked()
                                    {
                                        *secili_ucus_anahtari = Some(anahtar.clone());
                                        *secili_ucus_bilgisi = Some(s.clone());
                                        ui.close();
                                    }
                                });
                            });
                        }
                    });
            });
    });
    degisti
}

fn katman_rozeti_ciz(ui: &mut egui::Ui, katman: &str) {
    let renk = tema::katman_rengi(ui.visuals(), katman);
    ui.label(RichText::new(katman).color(renk).strong());
}

fn teyit_durumu_ciz(ui: &mut egui::Ui, durum: &str) {
    let p = tema::palet(ui.visuals());
    let (metin, renk) = match durum {
        "teyitli" => ("✓ teyitli", p.basari),
        "bekliyor" => ("⏳ bekliyor", p.uyari),
        "supheli" => ("⚠ şüpheli", p.hata),
        "urun_farkli" => ("⚠ ürün farklı", p.hata),
        "stok_yok" => ("⚠ stok yok", p.hata),
        "fiyat_degisti" => ("⚠ fiyat değişti", p.uyari),
        d => (d, ui.visuals().text_color()),
    };
    ui.label(RichText::new(metin).color(renk));
}

fn baglanti_ac(url: &str) {
    let _ = Command::new("xdg-open").arg(url).spawn();
}

/// WS /olaylar dinleyicisi: kopunca 5 sn bekleyip yeniden bağlanır; ayar değişince yeni adrese geçer.
fn ws_dinleyici_baslat(
    hedef: Arc<Mutex<(u64, String)>>,
    tx: Sender<ArkaPlanMesaji>,
    ctx: egui::Context,
) {
    thread::spawn(move || {
        loop {
            let (epoch, url) = {
                let g = hedef.lock().unwrap_or_else(|e| e.into_inner());
                (g.0, g.1.clone())
            };
            if url.trim().is_empty() {
                thread::sleep(Duration::from_secs(2));
                continue;
            }
            match tungstenite::connect(url.as_str()) {
                Ok((mut socket, _)) => {
                    if let tungstenite::stream::MaybeTlsStream::Plain(tcp) = socket.get_mut() {
                        let _ = tcp.set_read_timeout(Some(Duration::from_secs(2)));
                    }
                    let _ = tx.send(ArkaPlanMesaji::WsDurum(true));
                    ctx.request_repaint();

                    loop {
                        let guncel_epoch = {
                            let g = hedef.lock().unwrap_or_else(|e| e.into_inner());
                            g.0
                        };
                        if guncel_epoch != epoch {
                            let _ = socket.close(None);
                            break;
                        }
                        match socket.read() {
                            Ok(tungstenite::Message::Text(metin)) => {
                                if let Ok(olay) = serde_json::from_str::<OlayMesaji>(metin.as_str()) {
                                    let _ = tx.send(ArkaPlanMesaji::WsOlay(olay));
                                    ctx.request_repaint();
                                }
                            }
                            Ok(tungstenite::Message::Ping(v)) => {
                                let _ = socket.send(tungstenite::Message::Pong(v));
                            }
                            Ok(tungstenite::Message::Close(_)) => {
                                break;
                            }
                            Ok(_) => {}
                            Err(tungstenite::Error::Io(ref io_err))
                                if matches!(
                                    io_err.kind(),
                                    std::io::ErrorKind::WouldBlock
                                        | std::io::ErrorKind::TimedOut
                                        | std::io::ErrorKind::Interrupted
                                ) =>
                            {
                                continue;
                            }
                            Err(_) => break,
                        }
                    }

                    let _ = tx.send(ArkaPlanMesaji::WsDurum(false));
                    ctx.request_repaint();
                }
                Err(_) => {
                    let _ = tx.send(ArkaPlanMesaji::WsDurum(false));
                    ctx.request_repaint();
                }
            }
            // Yeniden bağlanmadan önce 5 sn bekle (ancak hedef URL değiştiyse hemen dene)
            for _ in 0..10 {
                thread::sleep(Duration::from_millis(500));
                let guncel_epoch = {
                    let g = hedef.lock().unwrap_or_else(|e| e.into_inner());
                    g.0
                };
                if guncel_epoch != epoch {
                    break;
                }
            }
        }
    });
}

fn simdiki_saat_dizgesi() -> String {
    if let Ok(cikti) = Command::new("date").arg("+%d.%m.%Y %H:%M").output() {
        if cikti.status.success() {
            let s = String::from_utf8_lossy(&cikti.stdout).trim().to_string();
            if !s.is_empty() {
                return s;
            }
        }
    }
    "şimdi".to_string()
}

// ======================= Yazı Tipi Yükleme (fc-list + bilinen yollar) =======================

fn yazi_tiplerini_yukle(ctx: &egui::Context) {
    let mut fonts = FontDefinitions::default();
    let mut aday_yollar: Vec<PathBuf> = vec![
        PathBuf::from("/usr/share/fonts/noto/NotoSans-Regular.ttf"),
        PathBuf::from("/usr/share/fonts/noto/NotoSansSymbols-Regular.ttf"),
        PathBuf::from("/usr/share/fonts/noto/NotoSansSymbols2-Regular.ttf"),
        PathBuf::from("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        PathBuf::from("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        PathBuf::from("/usr/share/fonts/truetype/noto/NotoSansSymbols-Regular.ttf"),
        PathBuf::from("/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf"),
        PathBuf::from("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ];

    // fc-list ile sistemdeki Noto Sans / Noto Sans Symbols / DejaVu Sans yollarını da sorgula
    if let Ok(cikti) = Command::new("fc-list")
        .args([":", "file", "family"])
        .output()
    {
        if cikti.status.success() {
            let metin = String::from_utf8_lossy(&cikti.stdout);
            for satir in metin.lines() {
                let kucuk = satir.to_lowercase();
                if (kucuk.contains("notosans-regular.ttf")
                    || kucuk.contains("notosanssymbols-regular.ttf")
                    || kucuk.contains("notosanssymbols2-regular.ttf")
                    || kucuk.contains("dejavusans.ttf"))
                    && satir.contains(':')
                {
                    if let Some(dosya) = satir.split(':').next() {
                        let p = PathBuf::from(dosya.trim());
                        if !aday_yollar.contains(&p) {
                            aday_yollar.push(p);
                        }
                    }
                }
            }
        }
    }

    for (i, yol) in aday_yollar.iter().enumerate() {
        if let Ok(bytes) = fs::read(yol) {
            let ad = format!("sistem_yedek_font_{}", i);
            fonts
                .font_data
                .insert(ad.clone(), FontData::from_owned(bytes).into());
            if let Some(ailesi) = fonts.families.get_mut(&FontFamily::Proportional) {
                ailesi.push(ad.clone());
            }
            if let Some(ailesi) = fonts.families.get_mut(&FontFamily::Monospace) {
                ailesi.push(ad);
            }
        }
    }

    ctx.set_fonts(fonts);
}

// ======================= Komut Satırı ve Başlangıç =======================

fn main() -> eframe::Result<()> {
    let mut ayarlar = UygulamaAyarlar::yukle();
    let mut sahte_klasor: Option<PathBuf> = None;
    let mut ilk_sekme: Option<Sekme> = None;
    let mut ilk_urun: Option<String> = None;
    let mut taksit_ac: Option<String> = None;
    let mut kaydir: Option<f32> = None;
    let mut tarama_sekmesi: u8 = 0;
    let mut goruntu: Option<(PathBuf, Duration)> = None;
    let mut genislik: Option<f32> = None;
    let mut yukseklik: Option<f32> = None;

    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--api" => {
                if let Some(deger) = args.next() {
                    ayarlar.api_adresi = deger;
                }
            }
            "--sahte" => {
                if let Some(klasor) = args.next() {
                    sahte_klasor = Some(PathBuf::from(klasor));
                }
            }
            "--sekme" => {
                ilk_sekme = match args.next().as_deref() {
                    Some("takip") => Some(Sekme::TakipListesi),
                    Some("detay") => Some(Sekme::UrunDetayi),
                    Some("plan") => Some(Sekme::SeyahatPlanla),
                    Some("ucuslar") => Some(Sekme::Ucuslar),
                    Some("siteler") => Some(Sekme::SiteSagligi),
                    Some("ara") => Some(Sekme::AraTanimla),
                    Some("ayarlar") => Some(Sekme::Ayarlar),
                    Some("kayitlar") => Some(Sekme::Kayitlar),
                    _ => None,
                };
            }
            "--urun" => ilk_urun = args.next(),
            "--taksit-ac" => taksit_ac = args.next(),
            "--kaydir" => kaydir = args.next().and_then(|s| s.parse::<f32>().ok()),
            // Ayarlar'daki tarama sekmesi: "http" = Hızlı HTTP turu (görüntü denetimi için)
            "--tarama-sekmesi" => tarama_sekmesi = u8::from(args.next().as_deref() == Some("http")),
            "--genislik" => {
                if let Some(g) = args.next().and_then(|s| s.parse::<f32>().ok()) {
                    genislik = Some(g.max(640.0));
                }
            }
            "--yukseklik" => {
                if let Some(y) = args.next().and_then(|s| s.parse::<f32>().ok()) {
                    yukseklik = Some(y.max(480.0));
                }
            }
            "--goruntu" => {
                if let Some(yol) = args.next() {
                    goruntu = Some((PathBuf::from(yol), Duration::from_secs(6)));
                }
            }
            "--help" | "-h" => {
                println!(
                    "Kullanım: kazi-arayuz [--api http://127.0.0.1:8770] [--sahte <klasör>] [--sekme takip|detay|plan|ucuslar|siteler|ara|ayarlar] [--urun <id>] [--taksit-ac <urun_id>] [--kaydir <px>] [--genislik <px>] [--yukseklik <px>] [--goruntu <dosya.ppm>]"
                );
                return Ok(());
            }
            _ => {}
        }
    }

    let veri_kaynagi = if let Some(klasor) = sahte_klasor {
        VeriKaynagi::Sahte(klasor)
    } else {
        VeriKaynagi::Http(ayarlar.api_adresi.clone())
    };

    let mut viewport = egui::ViewportBuilder::default()
        .with_title("Kazı Asistan v0.0.1")
        .with_inner_size([genislik.unwrap_or(1240.0), yukseklik.unwrap_or(780.0)])
        .with_min_inner_size([640.0, 480.0]);
    if let Some(g) = genislik {
        viewport = viewport.with_inner_size([g, 780.0]);
        if goruntu.is_some() {
            viewport = viewport
                .with_max_inner_size([g, 1200.0])
                .with_resizable(false);
        }
    }
    let secenekler = eframe::NativeOptions {
        viewport,
        persist_window: genislik.is_none() && goruntu.is_none(),
        ..Default::default()
    };

    eframe::run_native(
        "Kazı Asistan",
        secenekler,
        Box::new(move |cc| {
            let mut uyg = KaziArayuzApp::new(cc, ayarlar, veri_kaynagi);
            uyg.goruntu_hedefi = goruntu;
            uyg.baslangic_taksit_ac = taksit_ac;
            uyg.baslangic_kaydir = kaydir;
            uyg.tarama_ayar_sekmesi = tarama_sekmesi;
            if let Some(id) = ilk_urun {
                uyg.urun_detay_yukle(&cc.egui_ctx, id);
            }
            if let Some(sekme) = ilk_sekme {
                uyg.aktif_sekme = sekme;
            }
            Ok(Box::new(uyg))
        }),
    )
}

/// İşaretli ürünlerin en iyi fiyat toplamı (çekirdek yanıtı gelene kadar anında gösterim için).
fn yerel_toplam_hesapla(urunler: &[PanoUrun]) -> SistemToplami {
    let mut toplam = SistemToplami::default();
    for u in urunler.iter().filter(|u| u.toplamda.unwrap_or(u.sistemde)) {
        toplam.secili.push(u.id.clone());
        match u.en_iyi.as_ref().and_then(|e| e.sepet_fiyati) {
            Some(f) => toplam.tutar += f,
            None => toplam.eksik.push(u.ad.clone()),
        }
    }
    toplam.adet = Some(toplam.secili.len() as u32);
    toplam
}

#[cfg(test)]
mod tests {
    use super::*;


    #[test]
    fn test_yerel_toplam_yalniz_isaretlileri_toplar() {
        let urun = |id: &str, fiyat: Option<f64>, sistemde: bool, toplamda: Option<bool>| PanoUrun {
            id: id.into(), ad: id.to_uppercase(), sistemde, toplamda,
            en_iyi: fiyat.map(|f| crate::api::PanoEnIyiTeklif { sepet_fiyati: Some(f), ..Default::default() }),
            ..Default::default()
        };
        let liste = vec![
            urun("gpu", Some(100.0), true, None),          // seçim yok → sistemde
            urun("cpu1", Some(20.0), true, Some(false)),   // kullanıcı çıkardı
            urun("cpu2", Some(30.0), false, Some(true)),   // kullanıcı ekledi
            urun("ssd", None, false, Some(true)),          // fiyatsız → eksik
        ];
        let t = yerel_toplam_hesapla(&liste);
        assert_eq!(t.tutar, 130.0);
        assert_eq!(t.secili, vec!["gpu", "cpu2", "ssd"]);
        assert_eq!(t.eksik, vec!["SSD"]);
        assert_eq!(t.adet, Some(3));
    }
    use crate::api::TaksitBilgisi;

    fn ornek_teklif(id: i64, site: &str, sepet: Option<f64>) -> DetayTeklif {
        DetayTeklif {
            teklif_id: Some(id),
            site: site.to_string(),
            sepet_fiyati: sepet,
            stok: Some(true),
            ..Default::default()
        }
    }

    fn ornek_ucus(firma: &str, kisi_basi: f64) -> UcusSecenek {
        UcusSecenek {
            tarih: "2026-08-22".to_string(),
            firma: firma.to_string(),
            kalkis: "2026-08-22T10:00".to_string(),
            varis: "2026-08-22T14:00".to_string(),
            sure_dk: 240,
            aktarma_sayisi: 0,
            kisi_basi,
            toplam: kisi_basi * 2.0,
            ayri_bilet: Some(false),
            kaynak: "google_flights".to_string(),
            ..Default::default()
        }
    }

    #[test]
    fn test_teklif_siralama_sepet_fiyati() {
        let t1 = ornek_teklif(1, "a", Some(110_000.0));
        let t2 = ornek_teklif(2, "b", Some(105_000.0));
        let t3 = ornek_teklif(3, "c", None);

        let girdi = vec![t3, t1, t2];

        let sepet_artan = teklifleri_sirala(&girdi, TeklifSiralama::SepetFiyati, true);
        assert_eq!(
            sepet_artan.iter().map(|t| t.teklif_id).collect::<Vec<_>>(),
            vec![Some(2), Some(1), Some(3)]
        );

        let sepet_azalan = teklifleri_sirala(&girdi, TeklifSiralama::SepetFiyati, false);
        assert_eq!(
            sepet_azalan.iter().map(|t| t.teklif_id).collect::<Vec<_>>(),
            vec![Some(1), Some(2), Some(3)]
        );
    }

    #[test]
    fn test_teklif_siralama_liste_fiyati() {
        let mut t1 = ornek_teklif(1, "a", Some(110_000.0));
        t1.liste_fiyati = Some(120_000.0);
        let mut t2 = ornek_teklif(2, "b", Some(105_000.0));
        t2.liste_fiyati = Some(125_000.0);
        let mut t3 = ornek_teklif(3, "c", Some(95_000.0));
        t3.liste_fiyati = None;

        let girdi = vec![t3, t1, t2];

        let liste_artan = teklifleri_sirala(&girdi, TeklifSiralama::ListeFiyati, true);
        assert_eq!(
            liste_artan.iter().map(|t| t.teklif_id).collect::<Vec<_>>(),
            vec![Some(1), Some(2), Some(3)]
        );

        let liste_azalan = teklifleri_sirala(&girdi, TeklifSiralama::ListeFiyati, false);
        assert_eq!(
            liste_azalan.iter().map(|t| t.teklif_id).collect::<Vec<_>>(),
            vec![Some(2), Some(1), Some(3)]
        );
    }

    #[test]
    fn test_teklif_siralama_indirim_yuzdesi() {
        // %10 indirim (100k -> 90k)
        let mut t1 = ornek_teklif(1, "a", Some(90_000.0));
        t1.liste_fiyati = Some(100_000.0);
        // %20 indirim (100k -> 80k)
        let mut t2 = ornek_teklif(2, "b", Some(80_000.0));
        t2.liste_fiyati = Some(100_000.0);
        // Liste fiyatı yok -> None (en sona)
        let t3 = ornek_teklif(3, "c", Some(70_000.0));

        let girdi = vec![t3, t1, t2];
        let artan = teklifleri_sirala(&girdi, TeklifSiralama::IndirimYuzdesi, true);
        assert_eq!(
            artan.iter().map(|t| t.teklif_id).collect::<Vec<_>>(),
            vec![Some(1), Some(2), Some(3)]
        );
        let azalan = teklifleri_sirala(&girdi, TeklifSiralama::IndirimYuzdesi, false);
        assert_eq!(
            azalan.iter().map(|t| t.teklif_id).collect::<Vec<_>>(),
            vec![Some(2), Some(1), Some(3)]
        );
    }

    #[test]
    fn test_teklif_siralama_pesin_taksit() {
        let mut t1 = ornek_teklif(1, "vatan", Some(109_000.0));
        t1.taksit = Some(TaksitBilgisi {
            pesin_fiyatina: Some(3),
            tablo: vec![],
        });
        let mut t2 = ornek_teklif(2, "hepsiburada", Some(108_499.0));
        t2.taksit = Some(TaksitBilgisi {
            pesin_fiyatina: Some(6),
            tablo: vec![],
        });
        let mut t3 = ornek_teklif(3, "amazon", Some(107_000.0));
        t3.taksit = Some(TaksitBilgisi {
            pesin_fiyatina: Some(0),
            tablo: vec![],
        });

        let girdi = vec![t3, t1, t2];
        let taksit_artan = teklifleri_sirala(&girdi, TeklifSiralama::PesinTaksit, true);
        assert_eq!(
            taksit_artan.iter().map(|t| t.teklif_id).collect::<Vec<_>>(),
            vec![Some(1), Some(2), Some(3)]
        );
        let taksit_azalan = teklifleri_sirala(&girdi, TeklifSiralama::PesinTaksit, false);
        assert_eq!(
            taksit_azalan
                .iter()
                .map(|t| t.teklif_id)
                .collect::<Vec<_>>(),
            vec![Some(2), Some(1), Some(3)]
        );
    }

    #[test]
    fn test_teklif_siralama_gozlem_zamani_ve_sutunlar() {
        let mut t1 = ornek_teklif(1, "vatan", Some(109_000.0));
        t1.gozlem_zamani = Some("2026-11-14T15:01:00".to_string());
        t1.satici = Some("Vatan".to_string());
        t1.kaynak_katmani = Some("K1".to_string());

        let mut t2 = ornek_teklif(2, "hepsiburada", Some(108_499.0));
        t2.gozlem_zamani = Some("2026-11-14T15:05:00".to_string());
        t2.satici = Some("HB".to_string());
        t2.kaynak_katmani = Some("K0".to_string());

        let t3 = ornek_teklif(3, "amazon", Some(107_000.0));

        let girdi = vec![t3, t1, t2];
        let gozlem_artan = teklifleri_sirala(&girdi, TeklifSiralama::GozlemZamani, true);
        assert_eq!(
            gozlem_artan.iter().map(|t| t.teklif_id).collect::<Vec<_>>(),
            vec![Some(1), Some(2), Some(3)]
        );
        let gozlem_azalan = teklifleri_sirala(&girdi, TeklifSiralama::GozlemZamani, false);
        assert_eq!(
            gozlem_azalan
                .iter()
                .map(|t| t.teklif_id)
                .collect::<Vec<_>>(),
            vec![Some(2), Some(1), Some(3)]
        );

        let satici_artan = teklifleri_sirala(&girdi, TeklifSiralama::Satici, true);
        assert_eq!(
            satici_artan.iter().map(|t| t.teklif_id).collect::<Vec<_>>(),
            vec![Some(2), Some(1), Some(3)]
        );
        let katman_artan = teklifleri_sirala(&girdi, TeklifSiralama::Katman, true);
        assert_eq!(
            katman_artan.iter().map(|t| t.teklif_id).collect::<Vec<_>>(),
            vec![Some(2), Some(1), Some(3)]
        );
    }

    fn ucus_test_verisi() -> Vec<UcusSecenek> {
        let mut u1 = ornek_ucus("Pegasus", 28_000.0);
        u1.sure_dk = 665;
        u1.aktarma_sayisi = 1;
        u1.aktarmalar = vec!["SAW".to_string()];
        u1.aktarma_bekleme_dk = Some(vec![175]);
        u1.kalkis = "2026-08-22T20:10".to_string();
        u1.varis = "2026-08-23T06:15".to_string();

        let mut u2 = ornek_ucus("THY", 36_400.0);
        u2.sure_dk = 380;
        u2.aktarma_sayisi = 0;
        u2.aktarma_bekleme_dk = None; // Aktarma bekleme yok -> None (sona)
        u2.kalkis = "2026-08-22T01:45".to_string();
        u2.varis = "2026-08-22T07:05".to_string();

        let mut u3 = ornek_ucus("AJet", 29_500.0);
        u3.sure_dk = 0; // Geçersiz/bilinmeyen süre -> None (sona)
        u3.aktarma_sayisi = 1;
        u3.aktarma_bekleme_dk = Some(vec![95]);
        u3.kalkis = "".to_string(); // Boş kalkış -> None (sona)
        u3.varis = "".to_string(); // Boş varış -> None (sona)

        vec![u3, u2, u1]
    }

    #[test]
    fn test_ucus_siralama_fiyat() {
        let mut girdi = ucus_test_verisi();
        girdi.push(ornek_ucus("Bilinmeyen", 0.0)); // 0.0 -> None (sona)
        let s_fiyat = ucuslari_sirala(&girdi, UcusSiralama::Fiyat, true);
        assert_eq!(
            s_fiyat.iter().map(|u| u.firma.as_str()).collect::<Vec<_>>(),
            vec!["Pegasus", "AJet", "THY", "Bilinmeyen"]
        );
        let s_fiyat_az = ucuslari_sirala(&girdi, UcusSiralama::Fiyat, false);
        assert_eq!(
            s_fiyat_az
                .iter()
                .map(|u| u.firma.as_str())
                .collect::<Vec<_>>(),
            vec!["THY", "AJet", "Pegasus", "Bilinmeyen"]
        );
    }

    #[test]
    fn test_ucus_siralama_toplam_sure() {
        let girdi = ucus_test_verisi();
        let s_sure = ucuslari_sirala(&girdi, UcusSiralama::ToplamSure, true);
        assert_eq!(
            s_sure.iter().map(|u| u.firma.as_str()).collect::<Vec<_>>(),
            vec!["THY", "Pegasus", "AJet"]
        );
        let s_sure_az = ucuslari_sirala(&girdi, UcusSiralama::ToplamSure, false);
        assert_eq!(
            s_sure_az
                .iter()
                .map(|u| u.firma.as_str())
                .collect::<Vec<_>>(),
            vec!["Pegasus", "THY", "AJet"]
        );
    }

    #[test]
    fn test_ucus_siralama_aktarma_sayisi() {
        let girdi = ucus_test_verisi();
        // u1 ve u3 eşit: 1 aktarma -> ikincil anahtar fiyat ▲: Pegasus 28k < AJet 29.5k
        let s_aktarma = ucuslari_sirala(&girdi, UcusSiralama::AktarmaSayisi, true);
        assert_eq!(
            s_aktarma
                .iter()
                .map(|u| u.firma.as_str())
                .collect::<Vec<_>>(),
            vec!["THY", "Pegasus", "AJet"]
        );
    }

    #[test]
    fn test_ucus_siralama_kalkis_saati() {
        let girdi = ucus_test_verisi();
        let s_kalkis = ucuslari_sirala(&girdi, UcusSiralama::KalkisSaati, true);
        assert_eq!(
            s_kalkis.iter().map(|u| u.firma.as_str()).collect::<Vec<_>>(),
            vec!["THY", "Pegasus", "AJet"]
        );
    }

    #[test]
    fn test_ucus_siralama_varis_saati() {
        let girdi = ucus_test_verisi();
        let s_varis = ucuslari_sirala(&girdi, UcusSiralama::VarisSaati, true);
        assert_eq!(
            s_varis.iter().map(|u| u.firma.as_str()).collect::<Vec<_>>(),
            vec!["Pegasus", "THY", "AJet"]
        );
    }

    #[test]
    fn test_ucus_siralama_aktarma_bekleme() {
        let girdi = ucus_test_verisi();
        // AJet 95dk < Pegasus 175dk, THY None -> en sonda
        let s_bekleme = ucuslari_sirala(&girdi, UcusSiralama::AktarmaBekleme, true);
        assert_eq!(
            s_bekleme
                .iter()
                .map(|u| u.firma.as_str())
                .collect::<Vec<_>>(),
            vec!["AJet", "Pegasus", "THY"]
        );
        let s_bekleme_az = ucuslari_sirala(&girdi, UcusSiralama::AktarmaBekleme, false);
        assert_eq!(
            s_bekleme_az
                .iter()
                .map(|u| u.firma.as_str())
                .collect::<Vec<_>>(),
            vec!["Pegasus", "AJet", "THY"]
        );
    }

    #[test]
    fn test_taksit_ozeti() {
        let pesin = TaksitBilgisi {
            pesin_fiyatina: Some(6),
            tablo: vec![],
        };
        assert_eq!(taksit_ozeti(&pesin), "6× peşin");

        let vade_farkli = TaksitBilgisi {
            pesin_fiyatina: None,
            tablo: vec![TaksitSatiri {
                banka: Some("Bonus".to_string()),
                taksit_sayisi: 12,
                aylik: Some(10_000.0),
                toplam: Some(120_000.0),
                vade_farki_yuzde: Some(8.5),
            }],
        };
        assert_eq!(taksit_ozeti(&vade_farkli), "12× %8,5 vade");

        let bos = TaksitBilgisi::default();
        assert_eq!(taksit_ozeti(&bos), "—");
    }

    #[test]
    fn test_taksit_satirlarini_sirala_ve_banka_gruplama() {
        let mut satirlar = vec![
            TaksitSatiri {
                banka: Some("Bonus".to_string()),
                taksit_sayisi: 12,
                aylik: Some(10_500.0),
                toplam: Some(126_000.0),
                vade_farki_yuzde: Some(13.0),
            },
            TaksitSatiri {
                banka: Some("World".to_string()),
                taksit_sayisi: 6,
                aylik: Some(19_521.0),
                toplam: Some(117_126.0),
                vade_farki_yuzde: Some(5.0),
            },
            TaksitSatiri {
                banka: Some("Bonus".to_string()),
                taksit_sayisi: 3,
                aylik: Some(37_183.0),
                toplam: Some(111_549.0),
                vade_farki_yuzde: Some(0.0),
            },
            TaksitSatiri {
                banka: Some("Maximum".to_string()),
                taksit_sayisi: 9,
                aylik: Some(12_394.34),
                toplam: Some(111_549.03),
                vade_farki_yuzde: Some(0.0),
            },
            TaksitSatiri {
                banka: Some("Bonus".to_string()),
                taksit_sayisi: 6,
                aylik: Some(18_591.5),
                toplam: Some(111_549.03),
                vade_farki_yuzde: Some(0.0),
            },
        ];

        taksit_satirlarini_sirala(&mut satirlar);
        let ozet_sira: Vec<(&str, u32)> = satirlar
            .iter()
            .map(|s| (s.banka.as_deref().unwrap_or(""), s.taksit_sayisi))
            .collect();
        // Önce vade farksızlar (9x > 6x > 3x), sonra vade farklılar (%5 < %13)
        assert_eq!(
            ozet_sira,
            vec![
                ("Maximum", 9),
                ("Bonus", 6),
                ("Bonus", 3),
                ("World", 6),
                ("Bonus", 12),
            ]
        );

        let gruplar = taksit_satirlarini_bankaya_gore_grupla(&satirlar);
        let grup_ozet: Vec<(String, Vec<u32>)> = gruplar
            .into_iter()
            .map(|(b, l)| (b, l.into_iter().map(|s| s.taksit_sayisi).collect()))
            .collect();
        assert_eq!(
            grup_ozet,
            vec![
                ("Maximum".to_string(), vec![9]),
                ("Bonus".to_string(), vec![6, 3, 12]),
                ("World".to_string(), vec![6]),
            ]
        );
    }

    #[test]
    fn test_taksit_vade_tl_farki() {
        let satir = TaksitSatiri {
            banka: Some("Bonus".to_string()),
            taksit_sayisi: 12,
            aylik: Some(10_500.0),
            toplam: Some(126_000.0),
            vade_farki_yuzde: Some(13.0),
        };
        let fark = taksit_vade_tl_farki(&satir, Some(111_549.03)).expect("fark hesaplanmalı");
        assert!((fark - 14_450.97).abs() < 0.01);
        assert!(taksit_vade_tl_farki(&satir, None).is_none());
    }

    #[test]
    fn test_eski_ayar_json_sessizce_varsayilana_doner() {
        let eski_json = format!(
            r#"{{
                "api_adresi": "http://127.0.0.1:8770",
                "yenileme_sn": 45,
                "koyu_tema": false,
                "teklif_siralama": "{ak}",
                "ucus_siralama": "{yi}",
                "yan_tur_siralama": "{ak}",
                "pano_siralama": "{ea}"
            }}"#,
            ak = concat!("ak", "illi"),
            yi = concat!("yil", "diz"),
            ea = concat!("en_ak", "illi_maliyet"),
        );
        let ayarlar: UygulamaAyarlar =
            serde_json::from_str(&eski_json).expect("Eski ayar JSON'u çökmemeli");
        // eski "yenileme_sn" alanı sessizce yok sayılır ve tekrar yazılmaz
        assert!(!serde_json::to_string(&ayarlar).unwrap().contains("yenileme_sn"));
        assert!(!ayarlar.koyu_tema);
        assert_eq!(ayarlar.teklif_siralama, TeklifSiralama::SepetFiyati);
        assert_eq!(ayarlar.ucus_siralama, UcusSiralama::Fiyat);
        assert_eq!(ayarlar.yan_tur_siralama, YanTurSiralama::Fiyat);
        assert_eq!(ayarlar.pano_siralama, PanoSiralama::UrunAdi);
    }

    #[test]
    fn test_kisaltma_fonksiyonu_kullanilmiyor() {
        let bicim_kaynak = include_str!("bicim.rs");
        let main_kaynak = include_str!("main.rs");
        let yasak_tanim = concat!("pub fn ", "kisalt");
        let yasak_cagri = concat!("bicim::", "kisalt");
        assert!(
            !bicim_kaynak.contains(yasak_tanim),
            "bicim.rs içinde sabit kisalt fonksiyonu olmamalı"
        );
        assert!(
            !main_kaynak.contains(yasak_cagri),
            "main.rs tablo hücrelerinde sabit kisalt kullanılmamalı"
        );
    }

    #[test]
    fn test_yan_tur_sirala_fiyat() {
        let c1 = YanTurCifti {
            gece: 2,
            gidis: UcusSecenek {
                tarih: "2026-08-25".to_string(),
                firma: "Qazaq Air".to_string(),
                kalkis: "07:20".to_string(),
                varis: "09:10".to_string(),
                sure_dk: 110,
                kisi_basi: 2_850.0,
                ..Default::default()
            },
            donus: UcusSecenek {
                tarih: "2026-08-27".to_string(),
                firma: "Air Astana".to_string(),
                kalkis: "19:30".to_string(),
                varis: "21:15".to_string(),
                sure_dk: 105,
                kisi_basi: 3_400.0,
                ..Default::default()
            },
            kisi_basi_toplam: 6_250.0,
            toplam_sure_dk: 215,
            uyarilar: vec![],
        };
        let mut c2 = c1.clone();
        c2.kisi_basi_toplam = 5_800.0;
        c2.toplam_sure_dk = 230;

        let sirali = yan_turlari_sirala(&[c1, c2], YanTurSiralama::Fiyat, true);
        assert_eq!(sirali[0].kisi_basi_toplam, 5_800.0);
    }

    #[test]
    fn test_site_durumu_kurallari_ve_kapali() {
        let mut s = SiteSaglik {
            ad: "itopya".to_string(),
            tur: Some("urun".to_string()),
            katman_tercihi: Some("K0".to_string()),
            etkin: true,
            dogrudan: true,
            son_basari: Some("2026-11-14T15:00:00Z".to_string()),
            son_hata: None,
            hata_turu: None,
            son_24s: Some(crate::api::SiteSon24Saat {
                basarili: 10,
                hatali: 0,
                teklif: 42,
            }),
        };
        assert_eq!(site_durumu(&s), SiteDurum::Yesil);

        // Hatalı > 0 ama başarılı da var -> Sarı
        s.son_24s.as_mut().unwrap().hatali = 2;
        assert_eq!(site_durumu(&s), SiteDurum::Sari);

        // Başarılı 0, hatalı > 0 -> Kırmızı
        s.son_24s.as_mut().unwrap().basarili = 0;
        assert_eq!(site_durumu(&s), SiteDurum::Kirmizi);

        // K3 + 0/32 olsa bile hata_turu "desteklenmiyor: ..." -> gri ⊘ Kapalı
        s.katman_tercihi = Some("K3".to_string());
        s.son_24s.as_mut().unwrap().hatali = 32;
        s.hata_turu = Some("desteklenmiyor: API anahtarı gerekli".to_string());
        assert_eq!(site_durumu(&s), SiteDurum::Kapali);
    }

    #[test]
    fn test_site_durum_filtresi_coklu_secim() {
        let mut f = SiteDurumFiltresi::default();
        assert!(f.hepsi_secili());

        // Tümü açıkken birine tıklanınca yalnız o açılır
        f.secimi_degistir(SiteDurum::Yesil);
        assert!(f.yesil && !f.sari && !f.kirmizi && !f.kapali);

        // İkinci bir durum daha seçilebilir (çoklu seçim)
        f.secimi_degistir(SiteDurum::Sari);
        assert!(f.yesil && f.sari && !f.kirmizi && !f.kapali);

        // Seçili iki durumdan biri kapatılıp tek kalan tekrar tıklanırsa Tümü açılır
        f.secimi_degistir(SiteDurum::Sari);
        assert!(f.yesil && !f.sari);
        f.secimi_degistir(SiteDurum::Yesil);
        assert!(f.hepsi_secili());
    }

    #[test]
    fn test_arama_eslesir_turkce_harf() {
        assert!(arama_eslesir(
            "istanbul iŞlemci",
            &["İSTANBUL Bilişim", "AMD İşlemci 9950X3D"]
        ));
        assert!(arama_eslesir("ısparta", &["ISPARTA Turizm"]));
        assert!(arama_eslesir("İnceHesap", &["incehesap"]));
        assert!(!arama_eslesir("itopya istanbul", &["itopya ankara"]));
    }

    #[test]
    fn test_duyarli_duzen_kirilim_noktalari() {
        let dar = duyarli_duzen_hesapla(880.0);
        assert!(dar.yan_menu_dar);
        assert!(dar.ikincil_sutunlari_gizle);
        assert!(dar.yan_menu_genisligi < 70.0);

        let orta = duyarli_duzen_hesapla(1000.0);
        assert!(orta.yan_menu_dar);
        assert!(!orta.ikincil_sutunlari_gizle);

        let genis = duyarli_duzen_hesapla(1240.0);
        assert!(!genis.yan_menu_dar);
        assert!(!genis.ikincil_sutunlari_gizle);
        assert!(genis.yan_menu_genisligi > 150.0);
    }

    #[test]
    fn test_tema_ve_filtre_ayar_kaydet_yukle() {
        let benzersiz = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let gecici = std::env::temp_dir().join(format!("kazi_test_ayar_{benzersiz}.json"));

        let mut ayarlar = UygulamaAyarlar::default();
        ayarlar.tema_tercihi = TemaTercihi::Acik;
        ayarlar.yakinlastirma = 1.25;
        ayarlar.site_durum_filtresi.secimi_degistir(SiteDurum::Yesil);
        ayarlar.kaydet_yola(&gecici).expect("ayarlar yazılmalı");

        let okunan = UygulamaAyarlar::yukle_yoldan(&gecici);
        assert_eq!(okunan.tema_tercihi, TemaTercihi::Acik);
        assert!((okunan.yakinlastirma - 1.25).abs() < 0.001);
        assert!(okunan.site_durum_filtresi.yesil);
        assert!(!okunan.site_durum_filtresi.kapali);

        let _ = fs::remove_file(gecici);
    }

    #[test]
    fn test_ucus_yan_tur_cakismasi() {
        let yan = YanTurCifti {
            gidis: UcusSecenek {
                tarih: "2026-08-25".to_string(),
                ..Default::default()
            },
            donus: UcusSecenek {
                tarih: "2026-08-28".to_string(),
                ..Default::default()
            },
            ..Default::default()
        };
        let icinde = UcusSecenek {
            tarih: "2026-08-26".to_string(),
            ..Default::default()
        };
        let disinda = UcusSecenek {
            tarih: "2026-08-24".to_string(),
            ..Default::default()
        };
        assert!(ucus_yan_tur_cakismasi(&icinde, &yan).is_some());
        assert!(ucus_yan_tur_cakismasi(&disinda, &yan).is_none());
    }
}

