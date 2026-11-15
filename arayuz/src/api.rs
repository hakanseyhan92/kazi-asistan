use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};

fn varsayilan_dogru() -> bool {
    true
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct HataGovdesi {
    pub hata: Option<String>,
    pub ayrinti: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct SaglikYanit {
    pub surum: Option<u32>,
    pub calisma_suresi_sn: Option<u64>,
    pub db_yolu: Option<String>,
    pub cekirdek_surumu: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct FiyatOzet {
    pub ortalama: Option<f64>,
    pub medyan: Option<f64>,
    pub adet: u32,
}

// ======================= GET /pano =======================

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct PanoYanit {
    pub guncelleme: Option<String>,
    pub urunler: Vec<PanoUrun>,
    pub sistem_toplami: Option<SistemToplami>,
    /// takipten çıkarılan ürünler (geri alma listesi)
    pub takip_disi: Vec<TakipDisiOge>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TakipDisiOge {
    pub id: String,
    pub ad: String,
    pub zaman: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct PanoUrun {
    pub id: String,
    pub ad: String,
    pub kategori: Option<String>,
    pub sistemde: bool,
    /// kullanıcının kutucuk seçimi (yoksa `sistemde`)
    pub toplamda: Option<bool>,
    pub hedef_fiyat: Option<f64>,
    /// kullanıcının girdiği alarm fiyatı (yoksa çekirdek eski hedefi verir)
    pub alarm_fiyat: Option<f64>,
    pub ozet: FiyatOzet,
    pub en_iyi: Option<PanoEnIyiTeklif>,
    pub son_tarama: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct PanoEnIyiTeklif {
    #[serde(alias = "id")]
    pub teklif_id: Option<i64>,
    pub site: String,
    pub satici: Option<String>,
    pub baslik: Option<String>,
    pub sepet_fiyati: Option<f64>,
    pub liste_fiyati: Option<f64>,
    #[serde(default)]
    pub stok: Option<bool>,
    pub taksit: Option<TaksitBilgisi>,
    pub kaynak_katmani: Option<String>,
    pub url: Option<String>,
    pub gozlem_zamani: Option<String>,
    pub teyit_durumu: Option<String>,
    pub hedef_altinda: bool,
    /// sepet fiyatı ≤ alarm fiyatı
    pub alarm_altinda: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct SistemToplami {
    pub tutar: f64,
    pub eksik: Vec<String>,
    /// toplama dahil (kutucuğu işaretli) ürün sayısı ve kimlikleri
    pub adet: Option<u32>,
    pub secili: Vec<String>,
}

/// PUT /toplam gövdesi
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ToplamSecimIstek {
    pub secimler: BTreeMap<String, bool>,
}

/// GET/PUT /ayarlar/tarama
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TaramaAyari {
    pub brave_gorunur: bool,
    pub brave: Vec<BraveDurumu>,
    /// Gizlilik/hız (15.11): Brave ürün analizi kapalı, site bildirimi ve açılır pencere engeli, çerezlerde "Tümünü reddet"
    #[serde(default = "varsayilan_dogru")]
    pub analiz_kapali: bool,
    #[serde(default = "varsayilan_dogru")]
    pub bildirim_engelle: bool,
    #[serde(default = "varsayilan_dogru")]
    pub popup_engelle: bool,
    #[serde(default = "varsayilan_dogru")]
    pub cerez_reddet: bool,
    /// Paralel tarama ve hız (15.11)
    #[serde(default = "varsayilan_sekme")]
    pub sekme_sayisi: u32,
    #[serde(default = "varsayilan_kategori")]
    pub kategori_esz: u32,
    #[serde(default = "varsayilan_dogru")]
    pub reklam_engelle: bool,
    #[serde(default)]
    pub gorsel_engelle: bool,
    /// 15.11: video/ses, web yazı tipi, canlı destek/bildirim betikleri ayrı ayrı; yedek katmanlar; doğrulama beklemesi
    #[serde(default = "varsayilan_dogru")]
    pub medya_engelle: bool,
    #[serde(default = "varsayilan_dogru")]
    pub yazi_tipi_engelle: bool,
    #[serde(default = "varsayilan_dogru")]
    pub widget_engelle: bool,
    /// 15.11: animasyon, GIF, gömülü içerik, otomatik oynatma; bota düşen sitede engelleri gevşet
    #[serde(default = "varsayilan_dogru")]
    pub animasyon_kapat: bool,
    #[serde(default = "varsayilan_dogru")]
    pub gif_engelle: bool,
    #[serde(default = "varsayilan_dogru")]
    pub gomulu_engelle: bool,
    #[serde(default = "varsayilan_dogru")]
    pub otomatik_oynatma_kapat: bool,
    #[serde(default = "varsayilan_dogru")]
    pub bot_muafiyeti: bool,
    #[serde(default = "varsayilan_dogru")]
    pub chromium_yedek: bool,
    #[serde(default = "varsayilan_dogru")]
    pub yerel_profil_yedek: bool,
    #[serde(default)]
    pub dogrulama_bekle_sn: u32,
    #[serde(default)]
    pub sistem: Option<SistemBilgisi>,
}

fn varsayilan_sekme() -> u32 {
    3
}

fn varsayilan_kategori() -> u32 {
    1
}

/// GET /ayarlar/tarama → "sistem": makine kaynakları ve önerilen sekme sayısı
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct SistemBilgisi {
    pub ram_gb: f64,
    pub bos_ram_gb: f64,
    pub cpu_cekirdek: u32,
    pub sekme_basi_mb: f64,
    pub brave_taban_mb: f64,
    pub onerilen_sekme: u32,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct BraveDurumu {
    pub ad: String,
    pub port: u16,
    pub calisiyor: bool,
    pub pencereler: Vec<String>,
    pub hata: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
struct BraveGorunurIstek {
    brave_gorunur: bool,
}

// ======================= GET /urunler/{id}/detay =======================

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct UrunDetayYanit {
    pub urun: DetayUrunBilgisi,
    pub ozet: FiyatOzet,
    pub teklifler: Vec<DetayTeklif>,
    pub gecmis: Vec<FiyatGecmisNoktasi>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct DetayUrunBilgisi {
    pub id: String,
    #[serde(alias = "kanonik_ad")]
    pub ad: String,
    pub kategori: Option<String>,
    pub hedef_fiyat: Option<f64>,
    pub alarm_fiyat: Option<f64>,
    pub kurallar: Option<UrunKurallar>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct UrunKurallar {
    pub dahil: Vec<String>,
    pub haric: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct DetayTeklif {
    #[serde(alias = "id")]
    pub teklif_id: Option<i64>,
    pub site: String,
    pub satici: Option<String>,
    pub baslik: Option<String>,
    pub url: Option<String>,
    pub sepet_fiyati: Option<f64>,
    pub liste_fiyati: Option<f64>,
    /// None = kaynak stok bilgisi vermiyor (arama listeleri)
    #[serde(default)]
    pub stok: Option<bool>,
    pub taksit: Option<TaksitBilgisi>,
    pub kaynak_katmani: Option<String>,
    pub gozlem_zamani: Option<String>,
    pub teyit_durumu: Option<String>,
    pub teyit_zamani: Option<String>,
    pub kanit_goruntusu: Option<String>,
    pub hedef_altinda: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TaksitBilgisi {
    pub pesin_fiyatina: Option<u32>,
    pub tablo: Vec<TaksitSatiri>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TaksitSatiri {
    pub banka: Option<String>,
    pub taksit_sayisi: u32,
    pub aylik: Option<f64>,
    pub toplam: Option<f64>,
    pub vade_farki_yuzde: Option<f64>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct FiyatGecmisNoktasi {
    pub gun: String,
    pub en_dusuk: f64,
    pub ortalama: Option<f64>,
}

// ======================= GET /siteler =======================

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct SiteSaglik {
    pub ad: String,
    pub tur: Option<String>,
    pub katman_tercihi: Option<String>,
    pub son_basari: Option<String>,
    pub son_hata: Option<String>,
    pub hata_turu: Option<String>,
    #[serde(default = "varsayilan_dogru")]
    pub etkin: bool,
    pub son_24s: Option<SiteSon24Saat>,
    /// false = kendi bağdaştırıcısı yok, yalnız toplayıcı (Akakçe/Cimri) üzerinden görülen mağaza
    #[serde(default = "varsayilan_dogru")]
    pub dogrudan: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct SiteSon24Saat {
    pub basarili: u32,
    pub hatali: u32,
    pub teklif: u32,
}

// ======================= GET /seyahat =======================

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct SeyahatYanit {
    pub guncelleme: Option<String>,
    pub yolcular: Vec<Yolcu>,
    pub bacaklar: Vec<UcusBacak>,
    pub yan_turlar: Vec<YanTur>,
    /// 15.11 (C1 #6): ana bacakların bağlantı payı denetlenmiş en iyi zincirleri
    pub zincirler: Vec<YolculukZinciri>,
    pub zincir_hatasi: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct YolculukZinciri {
    pub bacak_idler: Vec<String>,
    pub secenek_idler: Vec<String>,
    pub kisi_basi_toplam: f64,
    pub toplam: Option<f64>,
    pub uyarilar: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Yolcu {
    pub id: String,
    pub ad: String,
    pub uyruklar: Vec<String>,
    pub izinler: Vec<YolcuIzin>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct YolcuIzin {
    pub ulke: String,
    pub tur: String,
    pub bitis: Option<String>,
    pub durum: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub not: Option<String>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

pub type Izin = YolcuIzin;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct UcusBacak {
    pub id: String,
    pub baslik: String,
    pub en_erken: Option<String>,
    pub en_gec: Option<String>,
    pub yolcular: Vec<String>,
    pub secenekler: Vec<UcusSecenek>,
    pub elenen_sayisi: u32,
    pub elenen_ornekleri: Vec<ElenenOrnek>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct UcusSecenek {
    pub id: String,
    pub tarih: String,
    pub kalkis: String,
    pub varis: String,
    pub kalkis_nokta: String,
    pub varis_nokta: String,
    pub aktarmalar: Vec<String>,
    pub aktarma_sayisi: u32,
    pub sure_dk: u32,
    pub firma: String,
    pub kisi_basi: f64,
    pub toplam: f64,
    pub kaynak: String,
    pub link: Option<String>,
    pub gozlem_zamani: Option<String>,
    pub uyarilar: Vec<String>,
    pub arac: Option<String>,
    pub ayri_bilet: Option<bool>,
    pub aktarma_bekleme_dk: Option<Vec<i64>>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct ElenenOrnek {
    pub firma: String,
    pub rota: String,
    pub gerekce: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct YanTur {
    pub baslik: String,
    pub pencere: Vec<String>,
    pub yolcular: Vec<String>,
    pub ciftler: Vec<YanTurCifti>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct YanTurCifti {
    pub gidis: UcusSecenek,
    pub donus: UcusSecenek,
    pub gece: u32,
    pub kisi_basi_toplam: f64,
    pub toplam_sure_dk: u32,
    pub uyarilar: Vec<String>,
}

// ======================= Seyahat Planı (/seyahat/plan, /yerler, /ulkeler) =======================

fn varsayilan_plan_surum() -> u32 {
    1
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Plan {
    #[serde(default = "varsayilan_plan_surum")]
    pub surum: u32,
    pub ad: String,
    pub yolcular: Vec<Yolcu>,
    pub duraklar: Vec<Durak>,
    pub bacaklar: Vec<BacakAyar>,
    pub yan_turlar: Vec<YanTurAyar>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

impl Default for Plan {
    fn default() -> Self {
        Self {
            surum: 1,
            ad: String::new(),
            yolcular: Vec::new(),
            duraklar: Vec::new(),
            bacaklar: Vec::new(),
            yan_turlar: Vec::new(),
            ek: serde_json::Map::new(),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Durak {
    pub sehir: String,
    pub noktalar: Option<Vec<String>>,
    pub tarih: Option<String>,
    pub esneklik: u8,
    pub kalis_gece: Option<i32>,
    pub gecis_noktasi: bool,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct BacakAyar {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    pub yolcular: Vec<String>,
    pub araclar: Vec<String>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct YanTurAyar {
    pub ana_sehir: String,
    pub hedef_sehir: String,
    pub yolcular: Vec<String>,
    pub kalis_gece: u32,
    pub kalis_esneklik: u8,
    pub en_erken_gidis: Option<String>,
    pub en_gec_donus: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gidis_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub donus_id: Option<String>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Hata {
    pub alan: String,
    pub mesaj: String,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct YolcuDenetimi {
    pub yolcu: String,
    pub durum: String,
    pub mesaj: String,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct YerNokta {
    pub kod: String,
    pub ad: String,
    pub tur: String,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Yer {
    pub sehir: String,
    pub ulke: String,
    pub ulke_ad: String,
    pub kaynak: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub buyuk: Option<bool>,
    pub noktalar: Vec<YerNokta>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct OnizlemeBacak {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    pub sira: usize,
    pub baslik: String,
    pub kalkis_sehir: String,
    pub varis_sehir: String,
    pub en_erken: Option<String>,
    pub en_gec: Option<String>,
    pub yolcular: Vec<String>,
    pub araclar: Vec<String>,
    pub olasi_araclar: Vec<String>,
    pub kalkis_noktalari: Vec<YerNokta>,
    pub varis_noktalari: Vec<YerNokta>,
    pub gecis_sonrasi: bool,
    pub yolcu_denetimi: Vec<YolcuDenetimi>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct OnizlemeYanTur {
    pub baslik: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gidis_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub donus_id: Option<String>,
    pub pencere: Vec<String>,
    pub kalis_gece: u32,
    pub yolcu_denetimi: Vec<YolcuDenetimi>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Onizleme {
    pub hatalar: Vec<Hata>,
    pub bacaklar: Vec<OnizlemeBacak>,
    pub yan_turlar: Vec<OnizlemeYanTur>,
    pub uyarilar: Vec<String>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct PlanGetirYanit {
    pub plan: Plan,
    pub onizleme: Onizleme,
    pub kayitli: bool,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct EskiTarayiciAktarim {
    pub yazilan: Vec<String>,
    pub atlanan: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hata: Option<String>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct PlanKaydetYanit {
    pub plan: Plan,
    pub onizleme: Onizleme,
    pub eski_tarayici: Option<EskiTarayiciAktarim>,
    #[serde(flatten)]
    pub ek: serde_json::Map<String, serde_json::Value>,
}

// ======================= POST /tanimla & /onayla & /tara =======================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TanimlaIstek {
    pub metin: String,
    pub tur: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TanimlaYanit {
    pub istek_id: String,
    pub adaylar: Vec<TanimlaAday>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TanimlaAday {
    pub id: String,
    pub baslik: String,
    pub kaynak: Option<String>,
    pub url: Option<String>,
    pub gorsel: Option<String>,
    pub mpn: Option<String>,
    pub ozellikler: BTreeMap<String, serde_json::Value>,
    pub fiyat_araligi: Option<FiyatBandi>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct FiyatBandi {
    pub alt: Option<f64>,
    pub ust: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OnaylaIstek {
    pub aday_idler: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kanonik_ad: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mpn: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct OnaylaYanit {
    pub id: Option<String>,
    pub kanonik_ad: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaraIstek {
    pub urun_id: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct TaraYanit {
    pub gorev_id: String,
}

// ======================= WS /olaylar =======================

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct OlayMesaji {
    pub id: Option<i64>,
    pub tur: String,
    pub zaman: Option<String>,
    pub urun_id: Option<String>,
    pub gorev_id: Option<String>,
    pub mesaj: Option<String>,
    pub veri: serde_json::Value,
}

/// HTTP taban adresinden `ws://.../olaylar` (veya `wss://.../olaylar`) adresini üretir.
pub fn ws_olaylar_url(api_adresi: &str) -> String {
    let temiz = api_adresi.trim().trim_end_matches('/');
    let taban = if let Some(rest) = temiz.strip_prefix("https://") {
        format!("wss://{}", rest)
    } else if let Some(rest) = temiz.strip_prefix("http://") {
        format!("ws://{}", rest)
    } else if temiz.starts_with("ws://") || temiz.starts_with("wss://") {
        temiz.to_string()
    } else {
        format!("ws://{}", temiz)
    };
    format!("{}/olaylar", taban)
}

// ======================= Tarama planı, takip, alarm, Telegram (G1i) =======================

/// GET /tarama/plan
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct TaramaPlani {
    pub secenekler_dk: Vec<u32>,
    pub min_aralik_dk: u32,
    pub manuel_bekleme_dk: u32,
    pub simdi: Option<String>,
    pub kategoriler: Vec<TaramaKategori>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct TaramaKategori {
    pub id: String,
    pub ad: String,
    pub tur: String,
    pub urunler: Vec<String>,
    pub aralik_dk: u32,
    pub son_baslangic: Option<String>,
    pub son_bitis: Option<String>,
    pub son_sonuc: Option<TaramaSonOzet>,
    pub sonraki: Option<String>,
    pub kalan_sn: Option<i64>,
    /// bekliyor | kuyrukta | calisiyor
    pub durum: String,
    pub manuel_kilit_bitis: Option<String>,
    pub manuel_kalan_sn: Option<i64>,
    /// "Sıfırla" ile geri sayımın yeniden başladığı an (15.11)
    pub sayac_sifir: Option<String>,
}

/// POST /tarama/sifirla yanıtı: güncel plan + hangi kategoriler sıfırlandı / atlandı (taranıyordu)
#[derive(Debug, Clone, Default, Deserialize, PartialEq)]
#[serde(default)]
pub struct TaramaSifirYanit {
    #[serde(flatten)]
    pub plan: TaramaPlani,
    pub sifirlanan: Vec<String>,
    pub atlanan: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct TaramaSonOzet {
    pub teklif: Option<i64>,
    pub hata: Option<i64>,
    pub sure_sn: Option<f64>,
}

/// PUT /tarama/plan gövdesi
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TaramaPlanIstek {
    pub kategoriler: BTreeMap<String, u32>,
}

/// POST /tarama/kategori/{id}/simdi sonucu
#[derive(Debug, Clone, PartialEq)]
pub enum ElleTaramaYanit {
    /// 202: kuyruğa alındı
    Kuyrukta,
    /// 429: aynı kategori için elle tarama kilidi sürüyor
    Bekle(u64),
}

/// POST /takip/cikar ve /takip/geri_al gövdesi
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct TakipIstek {
    pub urun_idler: Vec<String>,
}

/// PUT /urunler/{id}/alarm gövdesi (null = alarmı kaldır)
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct AlarmIstek {
    pub alarm_fiyat: Option<f64>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct AlarmYanit {
    pub urun_id: String,
    pub alarm_fiyat: Option<f64>,
    pub hemen_gonderilen: Vec<serde_json::Value>,
}

/// GET/PUT /ayarlar/telegram (token ASLA açık gelmez)
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct TelegramAyari {
    pub etkin: bool,
    pub bot: Option<String>,
    pub chat_id: Option<i64>,
    pub token_maskeli: Option<String>,
    pub token_var: bool,
    /// kazi | evren-asistan | yok
    pub kaynak: Option<String>,
    pub ayar_dosyasi: Option<String>,
    pub rapor_aralik_dk: u32,
    pub sessiz_saatler: Vec<String>,
    pub aralik_secenekleri: Vec<u32>,
}

/// PUT /ayarlar/telegram gövdesi: yalnız dolu alanlar gönderilir; boş token gönderilmez (mevcut korunur)
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct TelegramAyarIstek {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub token: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub chat_id: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub etkin: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rapor_aralik_dk: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sessiz_saatler: Option<Vec<String>>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct GonderimYanit {
    pub gonderildi: bool,
}

// ======================= İstemci (HTTP veya Sahte Klasör) =======================


// ---------------------------------------------------------------- kayıt defteri (GET /kayitlar, 15.11)
#[derive(Debug, Clone, Deserialize, Default, PartialEq)]
pub struct Kayit {
    pub id: i64,
    pub zaman: String,
    pub seviye: String,
    pub kaynak: String,
    #[serde(default)]
    pub olay: Option<String>,
    #[serde(default)]
    pub kategori: Option<String>,
    #[serde(default)]
    pub site: Option<String>,
    #[serde(default)]
    pub tarayici: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub adet: Option<i64>,
    #[serde(default)]
    pub sure_sn: Option<f64>,
    pub mesaj: String,
    #[serde(default)]
    pub veri: serde_json::Value,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct KayitYanit {
    #[serde(default)]
    pub kayitlar: Vec<Kayit>,
    #[serde(default)]
    pub son_id: i64,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct KayitOzeti {
    #[serde(default)]
    pub seviye: std::collections::HashMap<String, i64>,
    #[serde(default)]
    pub siteler: Vec<String>,
    #[serde(default)]
    pub kaynaklar: Vec<String>,
    #[serde(default)]
    pub kategoriler: Vec<String>,
}

// ---------------------------------------------------------------- tarayıcı izleme (GET /tarayicilar, 15.11)
#[derive(Debug, Clone, Deserialize, Default, PartialEq)]
#[serde(default)]
pub struct TarayiciSekmesi {
    pub id: String,
    pub url: String,
    pub baslik: String,
    pub site: String,
    pub bot: bool,
}

#[derive(Debug, Clone, Deserialize, Default, PartialEq)]
#[serde(default)]
pub struct BotDurumu {
    pub site: String,
    pub url: String,
    pub zaman: String,
    pub bekliyor: bool,
    pub kalan_sn: Option<i64>,
    pub sekme_id: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Default, PartialEq)]
#[serde(default)]
pub struct KisaKayit {
    pub zaman: String,
    pub seviye: String,
    pub site: Option<String>,
    pub mesaj: String,
}

#[derive(Debug, Clone, Deserialize, Default, PartialEq)]
#[serde(default)]
pub struct TarayiciMotoru {
    pub ad: String,
    pub etiket: String,
    pub kurulu: bool,
    pub destekli: bool,
    pub durum: String,
    pub renk: String,
    pub calisiyor: bool,
    pub cdp_port: Option<u16>,
    pub sekmeler: Vec<TarayiciSekmesi>,
    pub is_ozeti: String,
    pub son_kayitlar: Vec<KisaKayit>,
    pub bot: Option<BotDurumu>,
    pub aciklama: String,
}

#[derive(Debug, Clone, Deserialize, Default, PartialEq)]
#[serde(default)]
pub struct TarayiciDurumu {
    pub zaman: String,
    pub motorlar: Vec<TarayiciMotoru>,
    pub dogrulama_istekleri: std::collections::HashMap<String, i64>,
}

#[derive(Debug, Clone)]
pub enum VeriKaynagi {
    Http(String),
    Sahte(PathBuf),
}

impl VeriKaynagi {
    pub fn saglik_getir(&self) -> Result<SaglikYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                http_get(&format!("{}/saglik", taban.trim_end_matches('/')))
            }
            VeriKaynagi::Sahte(_) => Ok(SaglikYanit {
                surum: Some(1),
                calisma_suresi_sn: Some(12),
                db_yolu: Some("~/.local/share/kazi/kazi.db".to_string()),
                cekirdek_surumu: Some("0.0.1".to_string()),
            }),
        }
    }

    pub fn pano_getir(&self) -> Result<PanoYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => http_get(&format!("{}/pano", taban.trim_end_matches('/'))),
            VeriKaynagi::Sahte(klasor) => dosya_oku_json(&klasor.join("pano.json")),
        }
    }

    pub fn urun_detay_getir(&self, urun_id: &str) -> Result<UrunDetayYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => http_get(&format!(
                "{}/urunler/{}/detay",
                taban.trim_end_matches('/'),
                urun_id
            )),
            VeriKaynagi::Sahte(klasor) => {
                let aday_yol = klasor.join(format!("detay_{}.json", urun_id));
                if aday_yol.exists() {
                    dosya_oku_json(&aday_yol)
                } else {
                    // Belirli ürün dosyası yoksa varsayılan detay_gpu_rtx5080.json dene
                    let yedek = klasor.join("detay_gpu_rtx5080.json");
                    if yedek.exists() {
                        dosya_oku_json(&yedek)
                    } else {
                        Err(format!("Sahte detay dosyası bulunamadı: {}", aday_yol.display()))
                    }
                }
            }
        }
    }

    pub fn siteler_getir(&self) -> Result<Vec<SiteSaglik>, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                http_get(&format!("{}/siteler", taban.trim_end_matches('/')))
            }
            VeriKaynagi::Sahte(klasor) => dosya_oku_json(&klasor.join("siteler.json")),
        }
    }

    /// PUT /siteler/{ad}/etkin — siteyi aramalara al (true) / aramalardan çıkar (false)
    pub fn site_etkin_yaz(&self, ad: &str, etkin: bool) -> Result<SiteSaglik, String> {
        match self {
            VeriKaynagi::Http(taban) => http_put(
                &format!("{}/siteler/{}/etkin", taban.trim_end_matches('/'), url_encode(ad)),
                &serde_json::json!({ "etkin": etkin }),
            ),
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde site ayarı değiştirilmez".to_string()),
        }
    }

    pub fn seyahat_getir(&self) -> Result<SeyahatYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                http_get(&format!("{}/seyahat", taban.trim_end_matches('/')))
            }
            VeriKaynagi::Sahte(klasor) => dosya_oku_json(&klasor.join("seyahat.json")),
        }
    }

    pub fn tanimla_gonder(&self, metin: &str, tur: &str) -> Result<TanimlaYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!("{}/tanimla", taban.trim_end_matches('/'));
                let istek = TanimlaIstek {
                    metin: metin.to_string(),
                    tur: tur.to_string(),
                };
                http_post(&url, &istek)
            }
            VeriKaynagi::Sahte(klasor) => {
                let tanimla_dosya = klasor.join("tanimla.json");
                if tanimla_dosya.exists() {
                    dosya_oku_json(&tanimla_dosya)
                } else {
                    // Sahte modda metne göre örnek aday ya da boş aday döndür
                    if metin.to_lowercase().contains("5080") {
                        Ok(TanimlaYanit {
                            istek_id: "sahte_istek_001".to_string(),
                            adaylar: vec![TanimlaAday {
                                id: "aday_rtx5080_inno3d".to_string(),
                                baslik: "Inno3D RTX 5080 X3 OC 16GB GDDR7 256Bit".to_string(),
                                kaynak: Some("akakce".to_string()),
                                url: Some("https://www.akakce.com/ekran-karti/en-ucuz-inno3d-rtx-5080-x3-fiyati.html".to_string()),
                                gorsel: None,
                                mpn: Some("N50803-16D7X-17603930".to_string()),
                                ozellikler: BTreeMap::from([
                                    ("bellek".to_string(), serde_json::json!("16 GB GDDR7")),
                                    ("seri".to_string(), serde_json::json!("RTX 5080")),
                                ]),
                                fiyat_araligi: Some(FiyatBandi {
                                    alt: Some(111549.0),
                                    ust: Some(138900.0),
                                }),
                            }],
                        })
                    } else {
                        Ok(TanimlaYanit {
                            istek_id: "sahte_bos".to_string(),
                            adaylar: vec![],
                        })
                    }
                }
            }
        }
    }

    pub fn aday_onayla(
        &self,
        istek_id: &str,
        aday_idler: Vec<String>,
        kanonik_ad: Option<String>,
        mpn: Option<String>,
    ) -> Result<OnaylaYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!(
                    "{}/tanimla/{}/onayla",
                    taban.trim_end_matches('/'),
                    istek_id
                );
                let istek = OnaylaIstek {
                    aday_idler,
                    kanonik_ad,
                    mpn,
                };
                http_post(&url, &istek)
            }
            VeriKaynagi::Sahte(_) => Ok(OnaylaYanit {
                id: Some("gpu_rtx5080".to_string()),
                kanonik_ad,
            }),
        }
    }

    /// Takip Listesi kutucukları: hangi ürünler sistem toplamına dahil.
    pub fn toplam_sec(&self, secimler: &BTreeMap<String, bool>) -> Result<SistemToplami, String> {
        match self {
            VeriKaynagi::Http(taban) => http_put(
                &format!("{}/toplam", taban.trim_end_matches('/')),
                &ToplamSecimIstek { secimler: secimler.clone() },
            ),
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde seçim kaydedilmez".to_string()),
        }
    }

    pub fn tarama_ayari_getir(&self) -> Result<TaramaAyari, String> {
        match self {
            VeriKaynagi::Http(taban) => http_get(&format!("{}/ayarlar/tarama", taban.trim_end_matches('/'))),
            VeriKaynagi::Sahte(_) => Ok(TaramaAyari { brave_gorunur: true, ..Default::default() }),
        }
    }

    /// PUT /ayarlar/tarama {alan: deger} — gizlilik/hız anahtarlarından biri (analiz_kapali, bildirim_engelle, popup_engelle, cerez_reddet)
    pub fn gizlilik_ayari_yaz(&self, alan: &str, deger: bool) -> Result<TaramaAyari, String> {
        match self {
            VeriKaynagi::Http(taban) => http_put(
                &format!("{}/ayarlar/tarama", taban.trim_end_matches('/')),
                &serde_json::json!({ alan: deger }),
            ),
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde tarayıcı ayarı değiştirilmez".to_string()),
        }
    }

    /// PUT /ayarlar/tarama — paralel tarama ayarı (sekme_sayisi, kategori_esz, reklam_engelle, gorsel_engelle)
    pub fn paralel_ayari_yaz(&self, govde: serde_json::Value) -> Result<TaramaAyari, String> {
        match self {
            VeriKaynagi::Http(taban) => http_put(&format!("{}/ayarlar/tarama", taban.trim_end_matches('/')), &govde),
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde tarayıcı ayarı değiştirilmez".to_string()),
        }
    }

    /// Brave penceresini göster (true) / gizle-simge durumuna küçült (false). Çekirdek çalışan Brave'lere anında uygular.
    pub fn brave_gorunur_yap(&self, gorunur: bool) -> Result<TaramaAyari, String> {
        match self {
            VeriKaynagi::Http(taban) => http_put(
                &format!("{}/ayarlar/tarama", taban.trim_end_matches('/')),
                &BraveGorunurIstek { brave_gorunur: gorunur },
            ),
            VeriKaynagi::Sahte(_) => Ok(TaramaAyari { brave_gorunur: gorunur, ..Default::default() }),
        }
    }

    pub fn tarama_baslat(&self, urun_id: &str) -> Result<TaraYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!("{}/tara", taban.trim_end_matches('/'));
                let istek = TaraIstek {
                    urun_id: urun_id.to_string(),
                };
                http_post(&url, &istek)
            }
            VeriKaynagi::Sahte(_) => Ok(TaraYanit {
                gorev_id: format!("sahte_gorev_{}", urun_id),
            }),
        }
    }

    /// GET /tarama/plan — zamanlayıcı henüz yoksa (404) `Ok(None)`.
    pub fn tarama_plani_getir(&self) -> Result<Option<TaramaPlani>, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!("{}/tarama/plan", taban.trim_end_matches('/'));
                let (kod, govde) = http_ham("GET", &url, None)?;
                tarama_plani_yaniti(kod, &govde)
            }
            VeriKaynagi::Sahte(klasor) => {
                let yol = klasor.join("tarama_plan.json");
                if yol.exists() {
                    dosya_oku_json(&yol).map(Some)
                } else {
                    Ok(None)
                }
            }
        }
    }

    /// PUT /tarama/plan — `{kategori_id: dakika}`
    pub fn tarama_plani_guncelle(&self, araliklar: BTreeMap<String, u32>) -> Result<TaramaPlani, String> {
        match self {
            VeriKaynagi::Http(taban) => http_put(
                &format!("{}/tarama/plan", taban.trim_end_matches('/')),
                &TaramaPlanIstek { kategoriler: araliklar },
            ),
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde tarama planı değiştirilmez".to_string()),
        }
    }

    /// GET /kayitlar — kayıt defteri (en yeni önce). `sorgu`: limit, sonra_id, once_id, seviye, kaynak, site, ara.
    pub fn kayitlar(&self, sorgu: &[(String, String)]) -> Result<KayitYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let qs: Vec<String> = sorgu
                    .iter()
                    .filter(|(_, d)| !d.is_empty())
                    .map(|(a, d)| format!("{a}={}", url_encode(d)))
                    .collect();
                http_get(&format!("{}/kayitlar?{}", taban.trim_end_matches('/'), qs.join("&")))
            }
            VeriKaynagi::Sahte(_) => Ok(KayitYanit::default()),
        }
    }

    /// GET /kayitlar/ozet — son 24 saatin seviye sayıları + süzgeç seçenekleri.
    pub fn kayit_ozeti(&self) -> Result<KayitOzeti, String> {
        match self {
            VeriKaynagi::Http(taban) => http_get(&format!("{}/kayitlar/ozet", taban.trim_end_matches('/'))),
            VeriKaynagi::Sahte(_) => Ok(KayitOzeti::default()),
        }
    }

    /// GET /tarayicilar — tarayıcı motorlarının durumu (yeşil/sarı/gri).
    pub fn tarayicilar(&self) -> Result<TarayiciDurumu, String> {
        match self {
            VeriKaynagi::Http(taban) => http_get(&format!("{}/tarayicilar", taban.trim_end_matches('/'))),
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde tarayıcı izlenmez".to_string()),
        }
    }

    /// GET /tarayicilar/{ad}/goruntu/{sekme_id}?genislik=N — sekmenin ham JPEG baytları.
    pub fn tarayici_goruntu(&self, ad: &str, sekme_id: &str, genislik: u32) -> Result<Vec<u8>, String> {
        use std::io::Read;
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!(
                    "{}/tarayicilar/{}/goruntu/{}?genislik={genislik}",
                    taban.trim_end_matches('/'),
                    url_encode(ad),
                    url_encode(sekme_id)
                );
                match http_agent().get(&url).call() {
                    Ok(y) => {
                        let mut bayt = Vec::new();
                        y.into_reader()
                            .take(16 * 1024 * 1024)
                            .read_to_end(&mut bayt)
                            .map_err(|e| format!("görüntü okunamadı ({url}): {e}"))?;
                        Ok(bayt)
                    }
                    Err(ureq::Error::Status(kod, y)) => Err(hata_metni(kod, &y.into_string().unwrap_or_default())),
                    Err(ureq::Error::Transport(t)) => Err(format!("Bağlantı hatası ({url}): {t}")),
                }
            }
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde görüntü yok".to_string()),
        }
    }

    /// POST /tarayicilar/dogrulama — elle doğrulama isteği; tarayıcı penceresi öne gelir.
    pub fn dogrulama_iste(
        &self,
        site: &str,
        sure_sn: i64,
        ad: &str,
        sekme_id: Option<&str>,
    ) -> Result<serde_json::Value, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let mut govde = serde_json::json!({ "site": site, "sure_sn": sure_sn });
                if !ad.is_empty() {
                    govde["ad"] = serde_json::json!(ad);
                }
                if let Some(s) = sekme_id.filter(|s| !s.is_empty()) {
                    govde["sekme_id"] = serde_json::json!(s);
                }
                http_post(&format!("{}/tarayicilar/dogrulama", taban.trim_end_matches('/')), &govde)
            }
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde doğrulama istenmez".to_string()),
        }
    }

    /// DELETE /tarayicilar/dogrulama/{site} — kullanıcı geçti ya da vazgeçti.
    pub fn dogrulama_iptal(&self, site: &str) -> Result<serde_json::Value, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!("{}/tarayicilar/dogrulama/{}", taban.trim_end_matches('/'), url_encode(site));
                let (kod, govde) = http_ham("DELETE", &url, None)?;
                if (200..300).contains(&kod) {
                    Ok(serde_json::from_str(&govde).unwrap_or(serde_json::Value::Null))
                } else {
                    Err(hata_metni(kod, &govde))
                }
            }
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde doğrulama iptal edilmez".to_string()),
        }
    }

    /// GET /tarama/http — hızlı HTTP turu ayarları + durum (404 → None: çekirdek eski).
    pub fn http_turu_getir(&self) -> Result<Option<HttpTuru>, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let (kod, govde) = http_ham("GET", &format!("{}/tarama/http", taban.trim_end_matches('/')), None)?;
                http_turu_yaniti(kod, &govde)
            }
            VeriKaynagi::Sahte(_) => Ok(None),
        }
    }

    /// PUT /tarama/http — {"etkin"?, "aralik_dk"?, "ucus"?, "tarayicida_atla"?}
    pub fn http_turu_guncelle(&self, govde: serde_json::Value) -> Result<HttpTuru, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let (kod, g) = http_ham("PUT", &format!("{}/tarama/http", taban.trim_end_matches('/')), Some(govde))?;
                http_turu_yaniti(kod, &g)?.ok_or_else(|| "çekirdekte /tarama/http yok".to_string())
            }
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde ayar değiştirilmez".to_string()),
        }
    }

    /// PUT /tarama/site/{ad}/tur — siteyi "http" ya da "tarayici" turuna taşı.
    pub fn site_tur_degistir(&self, site: &str, tur: &str) -> Result<HttpTuru, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!("{}/tarama/site/{}/tur", taban.trim_end_matches('/'), site);
                let (kod, g) = http_ham("PUT", &url, Some(serde_json::json!({ "tur": tur })))?;
                http_turu_yaniti(kod, &g)?.ok_or_else(|| "çekirdekte /tarama/site/…/tur yok".to_string())
            }
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde tur değiştirilmez".to_string()),
        }
    }

    /// POST /tarama/http/simdi — 202 kuyrukta, 429 zaten çalışıyor.
    pub fn http_turu_simdi(&self) -> Result<String, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!("{}/tarama/http/simdi", taban.trim_end_matches('/'));
                let (kod, g) = http_ham("POST", &url, Some(serde_json::json!({})))?;
                match kod {
                    200 | 202 => Ok("Hızlı tur sıraya alındı (15 sn içinde başlar)".to_string()),
                    429 => Err("Hızlı tur zaten çalışıyor".to_string()),
                    _ => Err(hata_metni(kod, &g)),
                }
            }
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde tarama yok".to_string()),
        }
    }

    /// POST /tarama/http/sifirla — geri sayım şimdiden yeniden başlar.
    pub fn http_turu_sifirla(&self) -> Result<HttpTuru, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!("{}/tarama/http/sifirla", taban.trim_end_matches('/'));
                let (kod, g) = http_ham("POST", &url, Some(serde_json::json!({})))?;
                http_turu_yaniti(kod, &g)?.ok_or_else(|| "çekirdekte /tarama/http yok".to_string())
            }
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde sayaç yok".to_string()),
        }
    }

    /// POST /tarama/sifirla — geri sayımı şimdiden, seçili süreyle yeniden başlat (boş liste = hepsi; taranan atlanır)
    pub fn tarama_sayac_sifirla(&self, kategoriler: Vec<String>) -> Result<TaramaSifirYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => http_post(
                &format!("{}/tarama/sifirla", taban.trim_end_matches('/')),
                &serde_json::json!({ "kategoriler": kategoriler }),
            ),
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde sayaç sıfırlanmaz".to_string()),
        }
    }

    /// POST /tarama/kategori/{id}/simdi — elle tarama (202 kuyrukta / 429 kilit).
    pub fn elle_tarama(&self, kategori_id: &str) -> Result<ElleTaramaYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!(
                    "{}/tarama/kategori/{}/simdi",
                    taban.trim_end_matches('/'),
                    url_encode(kategori_id)
                );
                let (kod, govde) = http_ham("POST", &url, Some(serde_json::json!({})))?;
                elle_tarama_yaniti(kod, &govde)
            }
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde tarama başlatılmaz".to_string()),
        }
    }

    /// POST /takip/cikar (cikar=true) ya da /takip/geri_al → güncel /pano
    pub fn takip_islem(&self, cikar: bool, urun_idler: Vec<String>) -> Result<PanoYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => http_post(
                &format!(
                    "{}/takip/{}",
                    taban.trim_end_matches('/'),
                    if cikar { "cikar" } else { "geri_al" }
                ),
                &TakipIstek { urun_idler },
            ),
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde takip listesi değiştirilmez".to_string()),
        }
    }

    /// PUT /urunler/{id}/alarm
    pub fn alarm_yaz(&self, urun_id: &str, alarm_fiyat: Option<f64>) -> Result<AlarmYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => http_put(
                &format!("{}/urunler/{}/alarm", taban.trim_end_matches('/'), url_encode(urun_id)),
                &AlarmIstek { alarm_fiyat },
            ),
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde alarm kaydedilmez".to_string()),
        }
    }

    pub fn telegram_ayari_getir(&self) -> Result<TelegramAyari, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                http_get(&format!("{}/ayarlar/telegram", taban.trim_end_matches('/')))
            }
            VeriKaynagi::Sahte(_) => Ok(TelegramAyari {
                etkin: true,
                bot: Some("ornek_bot".to_string()),
                chat_id: Some(123456),
                token_maskeli: Some("1234…MNOP".to_string()),
                token_var: true,
                kaynak: Some("kazi".to_string()),
                ayar_dosyasi: None,
                rapor_aralik_dk: 60,
                sessiz_saatler: vec!["01:00".to_string(), "07:00".to_string()],
                aralik_secenekleri: vec![0, 30, 60, 120, 180, 360],
            }),
        }
    }

    pub fn telegram_ayari_yaz(&self, istek: &TelegramAyarIstek) -> Result<TelegramAyari, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                http_put(&format!("{}/ayarlar/telegram", taban.trim_end_matches('/')), istek)
            }
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde Telegram ayarı kaydedilmez".to_string()),
        }
    }

    /// `rapor=false` → POST /ayarlar/telegram/test, `rapor=true` → POST /ayarlar/telegram/rapor
    pub fn telegram_gonder(&self, rapor: bool) -> Result<bool, String> {
        match self {
            VeriKaynagi::Http(taban) => http_post::<_, GonderimYanit>(
                &format!(
                    "{}/ayarlar/telegram/{}",
                    taban.trim_end_matches('/'),
                    if rapor { "rapor" } else { "test" }
                ),
                &serde_json::json!({}),
            )
            .map(|y| y.gonderildi),
            VeriKaynagi::Sahte(_) => Err("sahte veri kipinde Telegram'a gönderilmez".to_string()),
        }
    }

    pub fn plan_getir(&self) -> Result<PlanGetirYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                http_get(&format!("{}/seyahat/plan", taban.trim_end_matches('/')))
            }
            VeriKaynagi::Sahte(klasor) => {
                let yol = klasor.join("seyahat_plan.json");
                if yol.exists() {
                    dosya_oku_json(&yol)
                } else {
                    let yedek = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                        .join("../api/ornek/seyahat_plan.json");
                    dosya_oku_json(&yedek)
                }
            }
        }
    }

    pub fn plan_onizle(&self, plan: &Plan) -> Result<Onizleme, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!("{}/seyahat/plan/onizle", taban.trim_end_matches('/'));
                http_post(&url, plan)
            }
            VeriKaynagi::Sahte(_) => Ok(sahte_plan_onizle(plan)),
        }
    }

    pub fn plan_kaydet(&self, plan: &Plan) -> Result<PlanKaydetYanit, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!(
                    "{}/seyahat/plan?eski_tarayiciya=true",
                    taban.trim_end_matches('/')
                );
                http_put(&url, plan)
            }
            VeriKaynagi::Sahte(_) => {
                let onizleme = sahte_plan_onizle(plan);
                let yazilan = plan
                    .bacaklar
                    .iter()
                    .enumerate()
                    .filter(|(_, b)| b.araclar.iter().any(|a| a == "ucak"))
                    .map(|(i, b)| b.id.clone().unwrap_or_else(|| format!("bacak_{}", i + 1)))
                    .collect();
                Ok(PlanKaydetYanit {
                    plan: plan.clone(),
                    onizleme,
                    eski_tarayici: Some(EskiTarayiciAktarim {
                        yazilan,
                        atlanan: Vec::new(),
                        hata: None,
                        ek: serde_json::Map::new(),
                    }),
                    ek: serde_json::Map::new(),
                })
            }
        }
    }

    pub fn yer_ara(&self, q: &str) -> Result<Vec<Yer>, String> {
        let temiz = q.trim();
        if temiz.is_empty() {
            return Ok(Vec::new());
        }
        match self {
            VeriKaynagi::Http(taban) => {
                let url = format!(
                    "{}/yerler?q={}&azami=12",
                    taban.trim_end_matches('/'),
                    url_encode(temiz)
                );
                http_get(&url)
            }
            VeriKaynagi::Sahte(_) => Ok(sahte_yer_ara(temiz)),
        }
    }

    pub fn ulkeler(&self) -> Result<BTreeMap<String, String>, String> {
        match self {
            VeriKaynagi::Http(taban) => {
                http_get(&format!("{}/ulkeler", taban.trim_end_matches('/')))
            }
            VeriKaynagi::Sahte(_) => Ok(sahte_ulkeler()),
        }
    }
}

fn sahte_ulkeler() -> BTreeMap<String, String> {
    BTreeMap::from([
        ("AE".to_string(), "Birleşik Arap Emirlikleri".to_string()),
        ("CN".to_string(), "Çin".to_string()),
        ("DE".to_string(), "Almanya".to_string()),
        ("FR".to_string(), "Fransa".to_string()),
        ("GB".to_string(), "Birleşik Krallık".to_string()),
        ("IT".to_string(), "İtalya".to_string()),
        ("JP".to_string(), "Japonya".to_string()),
        ("KR".to_string(), "Güney Kore".to_string()),
        ("NL".to_string(), "Hollanda".to_string()),
        ("QA".to_string(), "Katar".to_string()),
        ("TR".to_string(), "Türkiye".to_string()),
        ("US".to_string(), "Amerika Birleşik Devletleri".to_string()),
    ])
}

fn sahte_yerler_listesi() -> Vec<Yer> {
    let nokta = |kod: &str, ad: &str, tur: &str| YerNokta {
        kod: kod.to_string(),
        ad: ad.to_string(),
        tur: tur.to_string(),
        ek: serde_json::Map::new(),
    };
    vec![
        Yer {
            sehir: "Ankara".to_string(),
            ulke: "TR".to_string(),
            ulke_ad: "Türkiye".to_string(),
            kaynak: "elle".to_string(),
            buyuk: None,
            noktalar: vec![
                nokta("ESB", "Esenboğa Havalimanı", "havalimani"),
                nokta("ANKARA_YHT", "Ankara YHT Garı", "yht"),
                nokta("ASTI", "AŞTİ", "otogar"),
            ],
            ek: serde_json::Map::new(),
        },
        Yer {
            sehir: "İstanbul".to_string(),
            ulke: "TR".to_string(),
            ulke_ad: "Türkiye".to_string(),
            kaynak: "elle".to_string(),
            buyuk: None,
            noktalar: vec![
                nokta("IST", "İstanbul Havalimanı", "havalimani"),
                nokta("SAW", "Sabiha Gökçen Havalimanı", "havalimani"),
                nokta("SOGUTLUCE", "Söğütlüçeşme YHT", "yht"),
                nokta("ESENLER", "Esenler Otogarı", "otogar"),
            ],
            ek: serde_json::Map::new(),
        },
        Yer {
            sehir: "Tokyo".to_string(),
            ulke: "JP".to_string(),
            ulke_ad: "Japonya".to_string(),
            kaynak: "elle".to_string(),
            buyuk: None,
            noktalar: vec![
                nokta("NRT", "Narita Airport", "havalimani"),
                nokta("HND", "Haneda Airport", "havalimani"),
            ],
            ek: serde_json::Map::new(),
        },
        Yer {
            sehir: "Seul".to_string(),
            ulke: "KR".to_string(),
            ulke_ad: "Güney Kore".to_string(),
            kaynak: "elle".to_string(),
            buyuk: None,
            noktalar: vec![
                nokta("ICN", "Incheon International Airport", "havalimani"),
                nokta("GMP", "Gimpo International Airport", "havalimani"),
            ],
            ek: serde_json::Map::new(),
        },
        Yer {
            sehir: "Osaka".to_string(),
            ulke: "JP".to_string(),
            ulke_ad: "Japonya".to_string(),
            kaynak: "elle".to_string(),
            buyuk: None,
            noktalar: vec![
                nokta("KIX", "Kansai International Airport", "havalimani"),
                nokta("ITM", "Itami Airport", "havalimani"),
            ],
            ek: serde_json::Map::new(),
        },
    ]
}

fn sahte_yer_ara(q: &str) -> Vec<Yer> {
    let ql = q.to_lowercase();
    sahte_yerler_listesi()
        .into_iter()
        .filter(|y| {
            y.sehir.to_lowercase().contains(&ql)
                || y.noktalar
                    .iter()
                    .any(|n| n.kod.to_lowercase().contains(&ql) || n.ad.to_lowercase().contains(&ql))
        })
        .collect()
}

fn sahte_plan_onizle(plan: &Plan) -> Onizleme {
    let mut hatalar = Vec::new();
    if plan.yolcular.is_empty() {
        hatalar.push(Hata {
            alan: "yolcular".to_string(),
            mesaj: "En az bir yolcu gerekli".to_string(),
            ek: serde_json::Map::new(),
        });
    }
    if plan.duraklar.len() < 2 {
        hatalar.push(Hata {
            alan: "duraklar".to_string(),
            mesaj: "En az iki şehir gerekli".to_string(),
            ek: serde_json::Map::new(),
        });
    }
    if let Some(d0) = plan.duraklar.first() {
        if d0.tarih.as_deref().unwrap_or("").is_empty() {
            hatalar.push(Hata {
                alan: "duraklar.0.tarih".to_string(),
                mesaj: "Yolculuk başlangıç tarihi gerekli".to_string(),
                ek: serde_json::Map::new(),
            });
        }
    }

    let yerler = sahte_yerler_listesi();
    let nokta_bul = |sehir: &str| -> Vec<YerNokta> {
        yerler
            .iter()
            .find(|y| y.sehir == sehir)
            .map(|y| y.noktalar.clone())
            .unwrap_or_default()
    };

    let mut bacaklar = Vec::new();
    for (i, b) in plan.bacaklar.iter().enumerate() {
        if i + 1 >= plan.duraklar.len() {
            break;
        }
        let d_a = &plan.duraklar[i];
        let d_b = &plan.duraklar[i + 1];
        let olasi = if (d_a.sehir == "Ankara" || d_a.sehir == "İstanbul")
            && (d_b.sehir == "Ankara" || d_b.sehir == "İstanbul")
        {
            vec![
                "ucak".to_string(),
                "yht".to_string(),
                "otobus".to_string(),
            ]
        } else {
            vec!["ucak".to_string()]
        };
        bacaklar.push(OnizlemeBacak {
            id: b.id.clone(),
            sira: i,
            baslik: format!("{} → {}", d_a.sehir, d_b.sehir),
            kalkis_sehir: d_a.sehir.clone(),
            varis_sehir: d_b.sehir.clone(),
            en_erken: d_a.tarih.clone(),
            en_gec: d_a.tarih.clone(),
            yolcular: b.yolcular.clone(),
            araclar: b.araclar.clone(),
            olasi_araclar: olasi,
            kalkis_noktalari: nokta_bul(&d_a.sehir),
            varis_noktalari: nokta_bul(&d_b.sehir),
            gecis_sonrasi: d_a.gecis_noktasi && i > 0,
            yolcu_denetimi: Vec::new(),
            ek: serde_json::Map::new(),
        });
    }

    Onizleme {
        hatalar,
        bacaklar,
        yan_turlar: Vec::new(),
        uyarilar: Vec::new(),
        ek: serde_json::Map::new(),
    }
}

fn url_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for byte in s.as_bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~') {
            out.push(*byte as char);
        } else {
            out.push_str(&format!("%{:02X}", byte));
        }
    }
    out
}

fn dosya_oku_json<T: for<'de> Deserialize<'de>>(yol: &Path) -> Result<T, String> {
    let icerik = fs::read_to_string(yol)
        .map_err(|e| format!("Dosya okunamadı ({}): {}", yol.display(), e))?;
    serde_json::from_str::<T>(&icerik)
        .map_err(|e| format!("JSON ayrıştırma hatası ({}): {}", yol.display(), e))
}

fn http_agent() -> ureq::Agent {
    ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(4))
        .timeout_read(Duration::from_secs(8))
        .timeout_write(Duration::from_secs(5))
        .build()
}

fn http_get<T: for<'de> Deserialize<'de>>(url: &str) -> Result<T, String> {
    let agent = http_agent();
    match agent.get(url).call() {
        Ok(yanit) => yanit
            .into_json::<T>()
            .map_err(|e| format!("JSON ayrıştırma hatası ({url}): {e}")),
        Err(ureq::Error::Status(kod, yanit)) => {
            let metin = yanit.into_string().unwrap_or_default();
            if let Ok(hata_govde) = serde_json::from_str::<HataGovdesi>(&metin) {
                if let Some(h) = hata_govde.hata {
                    return Err(format!("HTTP {kod}: {h}"));
                }
            }
            Err(format!("HTTP {kod} ({url}): {metin}"))
        }
        Err(ureq::Error::Transport(t)) => Err(format!("Bağlantı hatası ({url}): {t}")),
    }
}

fn http_gonder_json<B: Serialize, T: for<'de> Deserialize<'de>>(
    istek: ureq::Request,
    url: &str,
    govde: &B,
) -> Result<T, String> {
    match istek.send_json(govde) {
        Ok(yanit) => yanit
            .into_json::<T>()
            .map_err(|e| format!("JSON ayrıştırma hatası ({url}): {e}")),
        Err(ureq::Error::Status(kod, yanit)) => {
            let metin = yanit.into_string().unwrap_or_default();
            if let Ok(hata_govde) = serde_json::from_str::<HataGovdesi>(&metin) {
                if let Some(h) = hata_govde.hata {
                    return Err(format!("HTTP {kod}: {h}"));
                }
            }
            Err(format!("HTTP {kod} ({url}): {metin}"))
        }
        Err(ureq::Error::Transport(t)) => Err(format!("Bağlantı hatası ({url}): {t}")),
    }
}

fn http_post<B: Serialize, T: for<'de> Deserialize<'de>>(url: &str, govde: &B) -> Result<T, String> {
    let agent = http_agent();
    http_gonder_json(agent.post(url), url, govde)
}

fn http_put<B: Serialize, T: for<'de> Deserialize<'de>>(url: &str, govde: &B) -> Result<T, String> {
    let agent = http_agent();
    http_gonder_json(agent.put(url), url, govde)
}

/// Durum kodunu hata saymadan döndürür (404/429 gibi anlamlı yanıtlar için).
fn http_ham(yontem: &str, url: &str, govde: Option<serde_json::Value>) -> Result<(u16, String), String> {
    let agent = http_agent();
    let istek = agent.request(yontem, url);
    let sonuc = match govde {
        Some(g) => istek.send_json(g),
        None => istek.call(),
    };
    match sonuc {
        Ok(y) => {
            let kod = y.status();
            Ok((kod, y.into_string().unwrap_or_default()))
        }
        Err(ureq::Error::Status(kod, y)) => Ok((kod, y.into_string().unwrap_or_default())),
        Err(ureq::Error::Transport(t)) => Err(format!("Bağlantı hatası ({url}): {t}")),
    }
}

fn hata_metni(kod: u16, govde: &str) -> String {
    match serde_json::from_str::<HataGovdesi>(govde) {
        Ok(HataGovdesi { hata: Some(h), .. }) => format!("HTTP {kod}: {h}"),
        _ => format!("HTTP {kod}: {}", govde.chars().take(200).collect::<String>()),
    }
}

/// 200 → plan, 404 → zamanlayıcı yok (None), diğerleri hata.
// ---------------------------------------------------------------- hızlı HTTP turu (15.11, GET/PUT /tarama/http)
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct HttpTurSonuc {
    pub teklif: u64,
    pub ucus: u64,
    pub hata: u64,
    pub sure_sn: u64,
    pub siteler: Vec<String>,
    pub engelli: Vec<String>,
    /// hata eşiğini aşıp Tarayıcı turuna geçen siteler
    pub tasinan: Vec<String>,
}

/// Site → tur ataması (15.11): "http" = Hızlı HTTP turu, "tarayici" = Tarayıcı turu.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct SiteTurAtama {
    pub site: String,
    pub tur: String,
    /// "urun" | "ucus"
    pub tip: String,
    pub neden: Option<String>,
    pub zaman: Option<String>,
    pub http_hata: u32,
    /// false → site araması tarayıcıdaki kutudan; HTTP turunda çalışmaz
    pub http_arama: bool,
    pub etkin: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct HttpTuru {
    pub atamalar: Vec<SiteTurAtama>,
    pub hata_esigi: u32,
    pub etkin: bool,
    pub aralik_dk: u32,
    pub ucus: bool,
    pub tarayicida_atla: bool,
    /// false → zamanlayıcı Brave kategori taramalarını kendiliğinden başlatmaz (elle "Şimdi tara" çalışır)
    pub tarayici_otomatik: bool,
    pub secenekler_dk: Vec<u32>,
    pub urun_siteleri: Vec<String>,
    pub ucus_kaynaklari: Vec<String>,
    /// "bekliyor" | "calisiyor" | "kapali"
    pub durum: String,
    pub son_baslangic: Option<String>,
    pub son_bitis: Option<String>,
    pub son_sonuc: Option<HttpTurSonuc>,
    pub sonraki: Option<String>,
    pub kalan_sn: u64,
}

/// 200 → gövde; 404 → çekirdek eski (uç yok); diğer → hata metni.
pub fn http_turu_yaniti(kod: u16, govde: &str) -> Result<Option<HttpTuru>, String> {
    match kod {
        200 => serde_json::from_str(govde)
            .map(Some)
            .map_err(|e| format!("hızlı tur ayrıştırılamadı: {e}")),
        404 => Ok(None),
        _ => Err(hata_metni(kod, govde)),
    }
}

pub fn tarama_plani_yaniti(kod: u16, govde: &str) -> Result<Option<TaramaPlani>, String> {
    match kod {
        200 => serde_json::from_str(govde)
            .map(Some)
            .map_err(|e| format!("tarama planı ayrıştırılamadı: {e}")),
        404 => Ok(None),
        _ => Err(hata_metni(kod, govde)),
    }
}

/// 202/200 → kuyrukta, 429 → `kalan_sn` kadar bekle.
pub fn elle_tarama_yaniti(kod: u16, govde: &str) -> Result<ElleTaramaYanit, String> {
    match kod {
        200..=299 => Ok(ElleTaramaYanit::Kuyrukta),
        429 => {
            let kalan = serde_json::from_str::<serde_json::Value>(govde)
                .ok()
                .and_then(|v| v.get("kalan_sn").and_then(|k| k.as_f64()))
                .map(|k| k.max(1.0).ceil() as u64)
                .unwrap_or(600);
            Ok(ElleTaramaYanit::Bekle(kalan))
        }
        404 => Err("zamanlayıcı henüz yok ya da kategori bilinmiyor (404)".to_string()),
        _ => Err(hata_metni(kod, govde)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ornek_json_dosyalari_ayristirma() {
        let klasor = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("ornek");
        let kaynak = VeriKaynagi::Sahte(klasor);

        let saglik = kaynak.saglik_getir().expect("saglik okunamadı");
        assert_eq!(saglik.cekirdek_surumu.as_deref(), Some("0.0.1"));

        let pano = kaynak.pano_getir().expect("ornek/pano.json ayrıştırılamadı");
        assert!(!pano.urunler.is_empty(), "pano.json içinde ürün olmalı");
        assert_eq!(pano.urunler[0].id, "gpu_rtx5080");

        let detay = kaynak
            .urun_detay_getir("gpu_rtx5080")
            .expect("ornek/detay_gpu_rtx5080.json ayrıştırılamadı");
        assert_eq!(detay.urun.id, "gpu_rtx5080");
        assert!(!detay.teklifler.is_empty(), "detay teklifleri boş olmamalı");
        assert!(!detay.gecmis.is_empty(), "detay fiyat geçmişi boş olmamalı");

        let siteler = kaynak
            .siteler_getir()
            .expect("ornek/siteler.json ayrıştırılamadı");
        assert!(!siteler.is_empty(), "siteler.json boş olmamalı");

        let seyahat = kaynak
            .seyahat_getir()
            .expect("ornek/seyahat.json ayrıştırılamadı");
        assert!(!seyahat.yolcular.is_empty(), "yolcular boş olmamalı");
        assert!(!seyahat.bacaklar.is_empty(), "bacaklar boş olmamalı");
        assert!(!seyahat.yan_turlar.is_empty(), "yan_turlar boş olmamalı");
    }

    #[test]
    fn test_bilinmeyen_alanlar_yok_sayilir() {
        let json = r#"{
            "guncelleme": "2026-11-14T15:20:00+03:00",
            "bilinmeyen_ust_alan": 12345,
            "urunler": [
                {
                    "id": "gpu_rtx5080",
                    "ad": "Ekran kartı RTX 5080",
                    "ileride_eklenecek_alan": {"a": 1}
                }
            ]
        }"#;
        let sonuc: PanoYanit = serde_json::from_str(json).expect("Bilinmeyen alanlar hata vermemeli");
        assert_eq!(sonuc.urunler.len(), 1);
        assert_eq!(sonuc.urunler[0].id, "gpu_rtx5080");
    }

    /// Orkestra (14.11): çekirdeğin GERÇEK veriden ürettiği `api/ornek/` dosyaları da ayrışmalı.
    #[test]
    fn test_gercek_cekirdek_ciktisi_ayristirma() {
        let klasor = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../api/ornek");
        if !klasor.join("pano.json").exists() {
            eprintln!("api/ornek yok, atlandı");
            return;
        }
        let kaynak = VeriKaynagi::Sahte(klasor);
        let pano = kaynak.pano_getir().expect("gerçek pano.json ayrıştırılamadı");
        assert!(pano.urunler.len() >= 5);
        assert!(
            pano.urunler
                .iter()
                .any(|u| u.en_iyi.as_ref().and_then(|e| e.taksit.as_ref()).is_some()),
            "gerçek pano.json içinde taksit bilgisi bulunmalı"
        );
        for u in &pano.urunler {
            let d = kaynak
                .urun_detay_getir(&u.id)
                .unwrap_or_else(|e| panic!("detay_{} ayrıştırılamadı: {e}", u.id));
            if let Some(ilk) = d.teklifler.first() {
                assert!(
                    ilk.taksit.is_some(),
                    "detay_{} ilk teklifinde taksit alanı olmalı",
                    u.id
                );
            }
        }
        assert!(!kaynak.siteler_getir().expect("gerçek siteler.json").is_empty());
        let s = kaynak.seyahat_getir().expect("gerçek seyahat.json ayrıştırılamadı");
        assert!(!s.bacaklar.is_empty());
        assert!(
            !s.bacaklar[0].secenekler.is_empty(),
            "seyahat.json ilk bacakta seçenek olmalı"
        );
        if let Some(yt) = s.yan_turlar.first() {
            assert!(!yt.ciftler.is_empty(), "yan turda çift olmalı");
        }
    }

    #[test]
    fn test_plan_serde_gidis_donus() {
        let yol = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../api/ornek/seyahat_plan.json");
        let ham_metin = fs::read_to_string(&yol).expect("api/ornek/seyahat_plan.json okunamadı");
        let ham_deger: serde_json::Value =
            serde_json::from_str(&ham_metin).expect("ham JSON ayrıştırılamadı");
        let yanit: PlanGetirYanit =
            serde_json::from_str(&ham_metin).expect("PlanGetirYanit ayrıştırılamadı");

        let tekrar_deger = serde_json::to_value(&yanit).expect("PlanGetirYanit serileştirilemedi");
        assert_eq!(
            tekrar_deger, ham_deger,
            "Plan serde gidiş-dönüşü ek alanlar dahil birebir korunmalı"
        );

        let plan_json = r#"{
            "surum": 1,
            "ad": "Test",
            "gelecek_plan_alani": "korunmali",
            "yolcular": [{"id": "ali", "ad": "Ali", "uyruklar": ["TR"], "izinler": [], "ozel": 7}],
            "duraklar": [
                {"sehir": "Ankara", "noktalar": null, "tarih": "2026-12-26", "esneklik": 1, "kalis_gece": null, "gecis_noktasi": false, "not": "x"},
                {"sehir": "İstanbul", "noktalar": null, "tarih": null, "esneklik": 0, "kalis_gece": null, "gecis_noktasi": false}
            ],
            "bacaklar": [{"id": "ank_ist", "yolcular": ["ali"], "araclar": ["ucak", "yht"], "ek_bacak": true}],
            "yan_turlar": []
        }"#;
        let plan_ham: serde_json::Value = serde_json::from_str(plan_json).unwrap();
        let plan: Plan = serde_json::from_str(plan_json).unwrap();
        let plan_tekrar = serde_json::to_value(&plan).unwrap();
        assert_eq!(plan_tekrar, plan_ham);
    }

    #[test]
    fn test_tarama_plani_404_ve_429_yanitlari() {
        assert_eq!(tarama_plani_yaniti(404, "{\"detail\":\"Not Found\"}"), Ok(None));
        let plan = tarama_plani_yaniti(
            200,
            r#"{"secenekler_dk":[30,60],"min_aralik_dk":10,"kategoriler":[{"id":"gpu","ad":"Ekran kartı","kalan_sn":12,"durum":"bekliyor","yeni_alan":1}]}"#,
        )
        .unwrap()
        .unwrap();
        assert_eq!(plan.kategoriler[0].id, "gpu");
        assert_eq!(plan.kategoriler[0].kalan_sn, Some(12));
        assert!(tarama_plani_yaniti(500, "{\"hata\":\"bozuk\"}").unwrap_err().contains("bozuk"));
        assert_eq!(elle_tarama_yaniti(202, "{\"durum\":\"kuyrukta\"}"), Ok(ElleTaramaYanit::Kuyrukta));
        assert_eq!(
            elle_tarama_yaniti(429, "{\"hata\":\"bekle\",\"kalan_sn\":312}"),
            Ok(ElleTaramaYanit::Bekle(312))
        );
        assert_eq!(elle_tarama_yaniti(429, "bozuk"), Ok(ElleTaramaYanit::Bekle(600)));
        assert!(elle_tarama_yaniti(404, "").is_err());
    }

    /// Gerçek HTTP: zamanlayıcısı olmayan (404 dönen) çekirdekte arayüz çökmez, "plan yok" der.
    #[test]
    fn test_tarama_plani_404_gercek_http() {
        use std::io::{Read, Write};
        let dinleyici = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let adres = dinleyici.local_addr().unwrap();
        let sunucu = std::thread::spawn(move || {
            for _ in 0..2 {
                let (mut baglanti, _) = dinleyici.accept().unwrap();
                let mut tampon = [0u8; 2048];
                let n = baglanti.read(&mut tampon).unwrap();
                let istek = String::from_utf8_lossy(&tampon[..n]).to_string();
                let (durum, govde) = if istek.starts_with("POST") {
                    ("429 Too Many Requests", r#"{"hata":"bekle","kalan_sn":42}"#)
                } else {
                    ("404 Not Found", r#"{"detail":"Not Found"}"#)
                };
                let yanit = format!(
                    "HTTP/1.1 {durum}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{govde}",
                    govde.len()
                );
                baglanti.write_all(yanit.as_bytes()).unwrap();
            }
        });
        let kaynak = VeriKaynagi::Http(format!("http://{adres}"));
        assert_eq!(kaynak.tarama_plani_getir(), Ok(None));
        assert_eq!(kaynak.elle_tarama("gpu"), Ok(ElleTaramaYanit::Bekle(42)));
        sunucu.join().unwrap();
    }

    #[test]
    fn sayac_sifir_yaniti_plan_ve_listeleri_cozer() {
        let y: TaramaSifirYanit = serde_json::from_str(
            r#"{"secenekler_dk":[30,35],"min_aralik_dk":2,"kategoriler":[{"id":"gpu","ad":"Ekran kartı","aralik_dk":35,
            "durum":"bekliyor","sayac_sifir":"2026-11-15T03:00:00+03:00"}],"sifirlanan":["gpu"],"atlanan":["cpu"]}"#,
        )
        .unwrap();
        assert_eq!(y.plan.secenekler_dk, vec![30, 35]);
        assert_eq!(y.plan.kategoriler[0].sayac_sifir.as_deref(), Some("2026-11-15T03:00:00+03:00"));
        assert_eq!(y.sifirlanan, vec!["gpu".to_string()]);
        assert_eq!(y.atlanan, vec!["cpu".to_string()]);
    }
}
