//! G1i: kategori bazlı tarama geri sayımı, elle tarama kilidi, alarm fiyatı girişi, takipten çıkarma ve
//! Telegram formu için ekrandan bağımsız (birim testli) mantık.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use crate::api::{PanoUrun, TaramaKategori, TaramaPlani, TelegramAyarIstek, TelegramAyari};

/// Elle tarama kilidi (çekirdekteki `manuel_bekleme_dk` gelmezse).
pub const ELLE_KILIT_SN: u64 = 600;
/// Ekran verisi arka planda bu sıklıkla sessizce okunur.
pub const VERI_OKUMA_SN: u64 = 30;
/// Tarama planı bu sıklıkla tazelenir (tarama sürerken daha sık).
pub const PLAN_OKUMA_SN: u64 = 30;
pub const PLAN_OKUMA_CALISIRKEN_SN: u64 = 10;

/// `kalan_sn` → "23:41" (dk:sn); bir saati aşınca "1:20:00".
pub fn geri_sayim(sn: u64) -> String {
    let (sa, dk, s) = (sn / 3600, (sn % 3600) / 60, sn % 60);
    if sa > 0 {
        format!("{sa}:{dk:02}:{s:02}")
    } else {
        format!("{dk}:{s:02}")
    }
}

/// Tarama süresi: "45 sn", "3 dk 30 sn", "1 sa 5 dk".
pub fn sure_metni(sn: f64) -> String {
    let sn = sn.max(0.0).round() as u64;
    if sn < 60 {
        format!("{sn} sn")
    } else if sn < 3600 {
        if sn % 60 == 0 {
            format!("{} dk", sn / 60)
        } else {
            format!("{} dk {} sn", sn / 60, sn % 60)
        }
    } else {
        format!("{} sa {} dk", sn / 3600, (sn % 3600) / 60)
    }
}

/// Üst çubuktaki geri sayımın bağlamı: hangi ekrandayız, seçili ürünün kategorisi ne?
#[derive(Debug, Clone, PartialEq)]
pub enum EtkinBaglam {
    /// Ürün Detayı ya da Takip Listesi'nde tıklanmış satır (kategori bilinmiyorsa None)
    Urun(Option<String>),
    /// Uçuşlar / Seyahat Planla
    Ucus,
    /// Diğer ekranlar ya da tıklanmış satır yok → sıradaki taranacak kategori
    Siradaki,
}

/// Sıradaki taranacak kategori: önce çalışan, sonra kuyruktaki, sonra kalan süresi en az olan.
pub fn siradaki_kategori(plan: &TaramaPlani) -> Option<&TaramaKategori> {
    let durum_onceligi = |k: &TaramaKategori| match k.durum.as_str() {
        "calisiyor" => 0,
        "kuyrukta" => 1,
        _ => 2,
    };
    plan.kategoriler
        .iter()
        .min_by_key(|k| (durum_onceligi(k), k.kalan_sn.unwrap_or(i64::MAX)))
}

/// Bağlama göre üst çubukta gösterilecek kategori.
pub fn etkin_kategori<'a>(plan: &'a TaramaPlani, baglam: &EtkinBaglam) -> Option<&'a TaramaKategori> {
    let bul = |id: &str| plan.kategoriler.iter().find(|k| k.id == id);
    match baglam {
        EtkinBaglam::Ucus => bul("ucus").or_else(|| siradaki_kategori(plan)),
        EtkinBaglam::Urun(Some(kat)) => bul(kat).or_else(|| siradaki_kategori(plan)),
        EtkinBaglam::Urun(None) | EtkinBaglam::Siradaki => siradaki_kategori(plan),
    }
}

/// Planın okunduğu andan beri geçen süreyle sonraki taramaya kalan saniye.
pub fn kategori_kalan_sn(k: &TaramaKategori, plan_yasi: Duration) -> u64 {
    let kalan = k.kalan_sn.unwrap_or(0).max(0) as u64;
    kalan.saturating_sub(plan_yasi.as_secs())
}

/// Elle tarama kilidi: çekirdeğin söylediği (`manuel_kalan_sn`, plan yaşı düşülür) ile bu arayüzün kendi
/// tıklamasından/429'dan doğan yerel kilidin BÜYÜĞÜ.
pub fn elle_kilit_kalan_sn(
    k: &TaramaKategori,
    plan_yasi: Duration,
    yerel_kilit_bitis: Option<Instant>,
    simdi: Instant,
) -> u64 {
    let cekirdek = (k.manuel_kalan_sn.unwrap_or(0).max(0) as u64).saturating_sub(plan_yasi.as_secs());
    let yerel = yerel_kilit_bitis
        .map(|b| b.saturating_duration_since(simdi).as_secs_f64().ceil() as u64)
        .unwrap_or(0);
    cekirdek.max(yerel)
}

/// Yerel kilit tablosu: kategori → kilidin bittiği an.
#[derive(Debug, Default, Clone)]
pub struct ElleKilitler(pub HashMap<String, Instant>);

impl ElleKilitler {
    pub fn kilitle(&mut self, kategori_id: &str, sn: u64, simdi: Instant) {
        self.0
            .insert(kategori_id.to_string(), simdi + Duration::from_secs(sn));
    }
    pub fn bitis(&self, kategori_id: &str) -> Option<Instant> {
        self.0.get(kategori_id).copied()
    }
}

/// İki plan arasında yeni biten kategori taramaları → "Ekran kartı tarandı · 33 teklif · 3 dk 30 sn".
pub fn bitmis_taramalar(eski: &TaramaPlani, yeni: &TaramaPlani) -> Vec<String> {
    let mut mesajlar = Vec::new();
    for k in &yeni.kategoriler {
        let Some(bitis) = k.son_bitis.as_deref() else { continue };
        let Some(onceki) = eski.kategoriler.iter().find(|e| e.id == k.id) else { continue };
        if onceki.son_bitis.as_deref() == Some(bitis) {
            continue;
        }
        let mut m = format!("{} tarandı", k.ad);
        if let Some(s) = &k.son_sonuc {
            if let Some(t) = s.teklif {
                m.push_str(&format!(" · {t} teklif"));
            }
            if let Some(h) = s.hata.filter(|h| *h > 0) {
                m.push_str(&format!(" · {h} hata"));
            }
            if let Some(sure) = s.sure_sn {
                m.push_str(&format!(" · {}", sure_metni(sure)));
            }
        }
        mesajlar.push(m);
    }
    mesajlar
}

/// Ayarlar satırındaki "son tarama" özeti: "33 teklif, 1 hata".
pub fn son_sonuc_metni(k: &TaramaKategori) -> String {
    match &k.son_sonuc {
        None => "henüz taranmadı".to_string(),
        Some(s) => {
            let mut parca = Vec::new();
            if let Some(t) = s.teklif {
                parca.push(format!("{t} teklif"));
            }
            if let Some(h) = s.hata {
                parca.push(format!("{h} hata"));
            }
            if parca.is_empty() {
                "sonuç yok".to_string()
            } else {
                parca.join(", ")
            }
        }
    }
}

/// Alarm fiyatı girişi: "110.000", "110000", "110.000 TL", "109.999,50" → Some; "" → None (alarmı kaldır).
pub fn alarm_girdisi_ayristir(metin: &str) -> Result<Option<f64>, String> {
    let temiz: String = metin
        .trim()
        .trim_end_matches("TL")
        .trim_end_matches("tl")
        .chars()
        .filter(|c| !c.is_whitespace() && *c != '₺')
        .collect();
    if temiz.is_empty() {
        return Ok(None);
    }
    // Türkçe biçim: nokta binlik ayraç, virgül ondalık
    let sayi = temiz.replace('.', "").replace(',', ".");
    match sayi.parse::<f64>() {
        Ok(f) if f.is_finite() && f > 0.0 => Ok(Some(f)),
        Ok(_) => Err("Alarm fiyatı sıfırdan büyük olmalı".to_string()),
        Err(_) => Err(format!("Sayı değil: \"{}\"", metin.trim())),
    }
}

/// Düzenleme kutusuna ilk yazılan değer: 110000 → "110.000" (boş = alarm yok).
pub fn alarm_kutusu_metni(f: Option<f64>) -> String {
    match f {
        None => String::new(),
        Some(f) => {
            let tam = f.round() as i64;
            let s = tam.abs().to_string();
            let mut out = String::new();
            for (i, c) in s.chars().enumerate() {
                if i > 0 && (s.len() - i) % 3 == 0 {
                    out.push('.');
                }
                out.push(c);
            }
            if tam < 0 {
                format!("-{out}")
            } else {
                out
            }
        }
    }
}

/// Ürünün alarm fiyatı (yoksa geriye uyum için hedef).
pub fn urun_alarm_fiyati(u: &PanoUrun) -> Option<f64> {
    u.alarm_fiyat.or(u.hedef_fiyat).filter(|f| *f > 0.0)
}

/// En iyi geçerli fiyat alarm fiyatına eşit ya da altında mı?
pub fn alarm_ulasti(u: &PanoUrun) -> bool {
    let Some(en_iyi) = &u.en_iyi else { return false };
    if en_iyi.alarm_altinda {
        return true;
    }
    match (en_iyi.sepet_fiyati, urun_alarm_fiyati(u)) {
        (Some(f), Some(a)) => f > 0.0 && f <= a,
        _ => false,
    }
}

/// Takipten çıkarma onay metni: "RTX 5080, Kasa … takip listesinden çıkarılsın mı? Geri alabilirsiniz."
pub fn takip_cikar_onay_metni(adlar: &[String]) -> String {
    let liste = if adlar.len() <= 4 {
        adlar.join(", ")
    } else {
        format!("{} ve {} ürün daha", adlar[..3].join(", "), adlar.len() - 3)
    };
    format!("{liste} takip listesinden çıkarılsın mı? Geri alabilirsiniz.")
}

/// Ayarlar → Telegram kartının düzenlenebilir hâli.
#[derive(Debug, Clone, Default)]
pub struct TelegramFormu {
    pub yuklu: Option<TelegramAyari>,
    pub chat_id: String,
    pub token_degistir: bool,
    pub yeni_token: String,
    pub etkin: bool,
    pub rapor_aralik_dk: u32,
    pub sessiz_bas: String,
    pub sessiz_bit: String,
    pub bekliyor: bool,
    pub mesaj: Option<(bool, String)>,
    pub istendi: bool,
}

impl TelegramFormu {
    pub fn yukle(&mut self, a: TelegramAyari) {
        self.chat_id = a.chat_id.map(|c| c.to_string()).unwrap_or_default();
        self.etkin = a.etkin;
        self.rapor_aralik_dk = a.rapor_aralik_dk;
        self.sessiz_bas = a.sessiz_saatler.first().cloned().unwrap_or_else(|| "01:00".into());
        self.sessiz_bit = a.sessiz_saatler.get(1).cloned().unwrap_or_else(|| "07:00".into());
        self.token_degistir = false;
        self.yeni_token.clear();
        self.yuklu = Some(a);
    }

    /// PUT gövdesi. Token yalnız "Değiştir" açık ve kutu doluysa gönderilir.
    pub fn istek(&self) -> Result<TelegramAyarIstek, String> {
        let chat = self.chat_id.trim();
        let chat_id = if chat.is_empty() {
            None
        } else {
            Some(chat.parse::<i64>().map_err(|_| "Chat ID sayı olmalı".to_string())?)
        };
        let token = self.yeni_token.trim();
        Ok(TelegramAyarIstek {
            token: (self.token_degistir && !token.is_empty()).then(|| token.to_string()),
            chat_id,
            etkin: Some(self.etkin),
            rapor_aralik_dk: Some(self.rapor_aralik_dk),
            sessiz_saatler: Some(vec![self.sessiz_bas.clone(), self.sessiz_bit.clone()]),
        })
    }
}

/// Sessiz saat seçenekleri: "00:00" … "23:30" (yarım saatlik).
pub fn saat_secenekleri() -> Vec<String> {
    (0..48).map(|i| format!("{:02}:{:02}", i / 2, (i % 2) * 30)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::{PanoEnIyiTeklif, TaramaSonOzet};

    fn kat(id: &str, ad: &str, kalan: i64, durum: &str) -> TaramaKategori {
        TaramaKategori {
            id: id.into(),
            ad: ad.into(),
            kalan_sn: Some(kalan),
            durum: durum.into(),
            aralik_dk: 60,
            ..Default::default()
        }
    }

    fn plan() -> TaramaPlani {
        TaramaPlani {
            secenekler_dk: (30..=120).step_by(5).collect(),
            min_aralik_dk: 2,
            manuel_bekleme_dk: 10,
            simdi: None,
            kategoriler: vec![
                kat("gpu", "Ekran kartı", 4800, "bekliyor"),
                kat("ram", "Bellek (RAM)", 600, "bekliyor"),
                kat("ucus", "Uçuşlar", 4200, "bekliyor"),
            ],
        }
    }

    #[test]
    fn test_geri_sayim_bicimi() {
        assert_eq!(geri_sayim(0), "0:00");
        assert_eq!(geri_sayim(59), "0:59");
        assert_eq!(geri_sayim(599), "9:59");
        assert_eq!(geri_sayim(1421), "23:41");
        assert_eq!(geri_sayim(4800), "1:20:00");
    }

    #[test]
    fn test_sure_metni() {
        assert_eq!(sure_metni(45.0), "45 sn");
        assert_eq!(sure_metni(210.0), "3 dk 30 sn");
        assert_eq!(sure_metni(711.0), "11 dk 51 sn");
        assert_eq!(sure_metni(120.0), "2 dk");
        assert_eq!(sure_metni(3900.0), "1 sa 5 dk");
    }

    #[test]
    fn test_etkin_kategori_secimi() {
        let p = plan();
        // ürün bağlamı → kendi kategorisi
        assert_eq!(etkin_kategori(&p, &EtkinBaglam::Urun(Some("gpu".into()))).unwrap().id, "gpu");
        // uçuş ekranları → ucus
        assert_eq!(etkin_kategori(&p, &EtkinBaglam::Ucus).unwrap().id, "ucus");
        // seçim yok → sıradaki (kalan süresi en az)
        assert_eq!(etkin_kategori(&p, &EtkinBaglam::Siradaki).unwrap().id, "ram");
        // planda olmayan kategori → sıradaki
        assert_eq!(etkin_kategori(&p, &EtkinBaglam::Urun(Some("kasa".into()))).unwrap().id, "ram");
        assert_eq!(etkin_kategori(&p, &EtkinBaglam::Urun(None)).unwrap().id, "ram");
        // çalışan kategori sıradakinin önüne geçer
        let mut p2 = p.clone();
        p2.kategoriler[0].durum = "calisiyor".into();
        assert_eq!(siradaki_kategori(&p2).unwrap().id, "gpu");
        assert!(etkin_kategori(&TaramaPlani::default(), &EtkinBaglam::Siradaki).is_none());
    }

    #[test]
    fn test_kalan_sure_plan_yasi_ile_azalir() {
        let k = kat("gpu", "Ekran kartı", 100, "bekliyor");
        assert_eq!(kategori_kalan_sn(&k, Duration::from_secs(0)), 100);
        assert_eq!(kategori_kalan_sn(&k, Duration::from_secs(30)), 70);
        assert_eq!(kategori_kalan_sn(&k, Duration::from_secs(500)), 0);
    }

    #[test]
    fn test_elle_kilit_hesabi() {
        let simdi = Instant::now();
        let mut k = kat("gpu", "Ekran kartı", 100, "bekliyor");
        assert_eq!(elle_kilit_kalan_sn(&k, Duration::ZERO, None, simdi), 0);
        // yerel tıklama: 10 dk kilit
        let mut kilit = ElleKilitler::default();
        kilit.kilitle("gpu", ELLE_KILIT_SN, simdi);
        assert_eq!(elle_kilit_kalan_sn(&k, Duration::ZERO, kilit.bitis("gpu"), simdi), 600);
        assert_eq!(
            elle_kilit_kalan_sn(&k, Duration::ZERO, kilit.bitis("gpu"), simdi + Duration::from_secs(1)),
            599
        );
        assert_eq!(
            elle_kilit_kalan_sn(&k, Duration::ZERO, kilit.bitis("gpu"), simdi + Duration::from_secs(700)),
            0
        );
        // çekirdeğin kilidi daha uzunsa o geçerli (başka pencereden/arayüzden basılmış)
        k.manuel_kalan_sn = Some(800);
        assert_eq!(elle_kilit_kalan_sn(&k, Duration::from_secs(100), kilit.bitis("gpu"), simdi), 700);
        assert!(kilit.bitis("ram").is_none());
    }

    #[test]
    fn test_bitmis_tarama_bildirimi() {
        let eski = plan();
        let mut yeni = plan();
        assert!(bitmis_taramalar(&eski, &yeni).is_empty());
        yeni.kategoriler[0].son_bitis = Some("2026-11-14T20:00:00+03:00".into());
        yeni.kategoriler[0].son_sonuc =
            Some(TaramaSonOzet { teklif: Some(33), hata: Some(0), sure_sn: Some(210.0) });
        assert_eq!(bitmis_taramalar(&eski, &yeni), vec!["Ekran kartı tarandı · 33 teklif · 3 dk 30 sn"]);
        // aynı bitiş ikinci kez bildirilmez
        assert!(bitmis_taramalar(&yeni, &yeni).is_empty());
        assert_eq!(son_sonuc_metni(&yeni.kategoriler[0]), "33 teklif, 0 hata");
        assert_eq!(son_sonuc_metni(&eski.kategoriler[0]), "henüz taranmadı");
    }

    #[test]
    fn test_alarm_girdisi_ayristirma() {
        assert_eq!(alarm_girdisi_ayristir("110.000"), Ok(Some(110000.0)));
        assert_eq!(alarm_girdisi_ayristir("110000"), Ok(Some(110000.0)));
        assert_eq!(alarm_girdisi_ayristir(" 110.000 TL "), Ok(Some(110000.0)));
        assert_eq!(alarm_girdisi_ayristir("109.999,50"), Ok(Some(109999.5)));
        assert_eq!(alarm_girdisi_ayristir(""), Ok(None));
        assert_eq!(alarm_girdisi_ayristir("   "), Ok(None));
        assert!(alarm_girdisi_ayristir("abc").is_err());
        assert!(alarm_girdisi_ayristir("0").is_err());
        assert!(alarm_girdisi_ayristir("-5").is_err());
        assert_eq!(alarm_kutusu_metni(Some(110000.0)), "110.000");
        assert_eq!(alarm_kutusu_metni(Some(5300.0)), "5.300");
        assert_eq!(alarm_kutusu_metni(None), "");
    }

    #[test]
    fn test_alarm_ulasti() {
        let mut u = PanoUrun {
            id: "gpu".into(),
            alarm_fiyat: Some(110000.0),
            en_iyi: Some(PanoEnIyiTeklif { sepet_fiyati: Some(111000.0), ..Default::default() }),
            ..Default::default()
        };
        assert!(!alarm_ulasti(&u));
        u.en_iyi.as_mut().unwrap().sepet_fiyati = Some(110000.0); // eşit → ulaştı
        assert!(alarm_ulasti(&u));
        u.alarm_fiyat = None;
        u.hedef_fiyat = Some(100000.0); // alarm yoksa eski hedef
        assert!(!alarm_ulasti(&u));
        u.en_iyi.as_mut().unwrap().alarm_altinda = true; // çekirdeğin bayrağı öncelikli
        assert!(alarm_ulasti(&u));
        u.en_iyi = None;
        assert!(!alarm_ulasti(&u));
    }

    #[test]
    fn test_takipten_cikar_istek_govdesi_ve_onay_metni() {
        let istek = crate::api::TakipIstek { urun_idler: vec!["gpu_rtx5080".into(), "kasa_lancool".into()] };
        assert_eq!(
            serde_json::to_value(&istek).unwrap(),
            serde_json::json!({"urun_idler": ["gpu_rtx5080", "kasa_lancool"]})
        );
        let alarm = crate::api::AlarmIstek { alarm_fiyat: None };
        assert_eq!(serde_json::to_value(&alarm).unwrap(), serde_json::json!({"alarm_fiyat": null}));
        assert_eq!(
            takip_cikar_onay_metni(&["RTX 5080".into(), "Kasa".into()]),
            "RTX 5080, Kasa takip listesinden çıkarılsın mı? Geri alabilirsiniz."
        );
        let cok: Vec<String> = (1..=6).map(|i| format!("Ü{i}")).collect();
        assert_eq!(
            takip_cikar_onay_metni(&cok),
            "Ü1, Ü2, Ü3 ve 3 ürün daha takip listesinden çıkarılsın mı? Geri alabilirsiniz."
        );
    }

    #[test]
    fn test_telegram_formu_token_bos_gitmez() {
        let mut f = TelegramFormu::default();
        f.yukle(TelegramAyari {
            etkin: true,
            chat_id: Some(1682280056),
            token_maskeli: Some("9129…B9Ss".into()),
            token_var: true,
            rapor_aralik_dk: 60,
            sessiz_saatler: vec!["01:00".into(), "07:00".into()],
            ..Default::default()
        });
        let govde = serde_json::to_value(f.istek().unwrap()).unwrap();
        assert!(govde.get("token").is_none(), "token alanı gönderilmemeli: {govde}");
        assert_eq!(govde["chat_id"], 1682280056);
        assert_eq!(govde["sessiz_saatler"], serde_json::json!(["01:00", "07:00"]));
        // "Değiştir" açık ama kutu boş → yine gönderilmez
        f.token_degistir = true;
        assert!(serde_json::to_value(f.istek().unwrap()).unwrap().get("token").is_none());
        f.yeni_token = " 123:ABC ".into();
        assert_eq!(f.istek().unwrap().token.as_deref(), Some("123:ABC"));
        f.chat_id = "abc".into();
        assert!(f.istek().is_err());
        assert_eq!(saat_secenekleri().len(), 48);
        assert_eq!(saat_secenekleri()[2], "01:00");
    }
}
