use std::collections::HashSet;

use chrono::{Duration as ChronoDuration, NaiveDate};

use crate::api::{BacakAyar, Durak, Hata, Onizleme, Plan, YolcuIzin};
use crate::bicim;

/// Türkçe karakterleri ASCII karşılıklarına katlar (arama ve kimlik üretimi için).
pub fn turkce_katla(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for ch in s.chars() {
        match ch {
            'ç' | 'Ç' => out.push('c'),
            'ğ' | 'Ğ' => out.push('g'),
            'ı' | 'İ' | 'I' => out.push('i'),
            'ö' | 'Ö' => out.push('o'),
            'ş' | 'Ş' => out.push('s'),
            'ü' | 'Ü' => out.push('u'),
            'â' | 'Â' => out.push('a'),
            'î' | 'Î' => out.push('i'),
            'û' | 'Û' => out.push('u'),
            _ => {
                for lc in ch.to_lowercase() {
                    out.push(lc);
                }
            }
        }
    }
    out
}

/// Yolcu adından küçük harfli, Türkçe harfsiz ve mevcut kimliklere göre tekil kimlik üretir.
pub fn yolcu_id_uret(ad: &str, mevcut: &[String]) -> String {
    let katlanmis = turkce_katla(ad.trim());
    let mut temiz = String::with_capacity(katlanmis.len());
    let mut son_alt_cizgi = false;
    for ch in katlanmis.chars() {
        if ch.is_ascii_alphanumeric() {
            temiz.push(ch);
            son_alt_cizgi = false;
        } else if !temiz.is_empty() && !son_alt_cizgi {
            temiz.push('_');
            son_alt_cizgi = true;
        }
    }
    while temiz.ends_with('_') {
        temiz.pop();
    }
    if temiz.is_empty() {
        temiz = "yolcu".to_string();
    }

    let kullanilan: HashSet<&str> = mevcut.iter().map(|s| s.as_str()).collect();
    if !kullanilan.contains(temiz.as_str()) {
        return temiz;
    }

    let mut sayac = 2usize;
    loop {
        let aday = format!("{}_{}", temiz, sayac);
        if !kullanilan.contains(aday.as_str()) {
            return aday;
        }
        sayac += 1;
    }
}

/// Bilinen TR şehirleri için kısa nokta türleri; yerel önizleme ve yeni bacak varsayılanları için.
fn bilinen_sehir_araclari(sehir: &str) -> (&'static str, &'static [&'static str]) {
    match sehir.trim() {
        "İstanbul" => ("TR", &["ucak", "yht", "otobus"]),
        "Ankara" => ("TR", &["ucak", "yht", "otobus"]),
        "Eskişehir" => ("TR", &["yht", "otobus"]),
        "Konya" => ("TR", &["yht", "otobus"]),
        "İzmir" => ("TR", &["ucak", "otobus"]),
        _ => ("", &["ucak"]),
    }
}

/// İki şehir arasındaki olası ulaşım araçlarını döndürür.
pub fn varsayilan_araclar(sehir_a: &str, sehir_b: &str) -> Vec<String> {
    let (ua, aa) = bilinen_sehir_araclari(sehir_a);
    let (ub, ab) = bilinen_sehir_araclari(sehir_b);
    if ua != "TR" || ub != "TR" {
        return vec!["ucak".to_string()];
    }
    let sonuc: Vec<String> = ["ucak", "yht", "otobus"]
        .iter()
        .filter(|arac| aa.contains(*arac) && ab.contains(*arac))
        .map(|s| (*s).to_string())
        .collect();
    if sonuc.is_empty() {
        vec!["ucak".to_string()]
    } else {
        sonuc
    }
}

/// Şehir adından bacak kimliği ön eki türetir (ör. `İstanbul` -> `ist`, `Tokyo` -> `tyo`).
fn sehir_kisa_kod(sehir: &str) -> String {
    match sehir.trim() {
        "İstanbul" => "ist".to_string(),
        "Tokyo" => "tyo".to_string(),
        "Seul" => "sel".to_string(),
        "Osaka" => "osa".to_string(),
        "Ankara" => "ank".to_string(),
        "İzmir" => "izm".to_string(),
        "Eskişehir" => "esk".to_string(),
        "Konya" => "kon".to_string(),
        diger => {
            let k: String = turkce_katla(diger)
                .chars()
                .filter(|c| c.is_ascii_alphanumeric())
                .take(3)
                .collect();
            if k.is_empty() {
                "shr".to_string()
            } else {
                k
            }
        }
    }
}

fn id_cifti_eslesir(bacak_id: &str, sehir_a: &str, sehir_b: &str) -> bool {
    let parcalar: Vec<&str> = bacak_id.split('_').collect();
    if parcalar.len() < 2 {
        return false;
    }
    let ka = sehir_kisa_kod(sehir_a);
    let kb = sehir_kisa_kod(sehir_b);
    parcalar[0] == ka && parcalar[1] == kb
}

/// Şehirler değiştirildikten sonra `bacaklar` dizisini `duraklar` ile uyumlu yeniden kurar.
/// Şehir çifti mevcut bir bacakla eşleşiyorsa ayarlarını (`id`, `yolcular`, `araclar`, `ek`) korur.
pub fn bacaklari_yeniden_kur(plan: &mut Plan) {
    if plan.duraklar.len() < 2 {
        plan.bacaklar.clear();
        return;
    }

    let tum_yolcular: Vec<String> = plan.yolcular.iter().map(|y| y.id.clone()).collect();
    let gecerli_yolcular: HashSet<&str> = tum_yolcular.iter().map(|s| s.as_str()).collect();
    let mut havuz: Vec<Option<BacakAyar>> = plan.bacaklar.drain(..).map(Some).collect();
    let yeni_sayisi = plan.duraklar.len() - 1;
    let mut yeni_bacaklar = Vec::with_capacity(yeni_sayisi);

    for i in 0..yeni_sayisi {
        let a = &plan.duraklar[i].sehir;
        let b = &plan.duraklar[i + 1].sehir;

        let eslesen_idx = havuz.iter().position(|aday| {
            aday.as_ref()
                .and_then(|x| x.id.as_deref())
                .is_some_and(|id| id_cifti_eslesir(id, a, b))
        });

        let bulunan = if let Some(idx) = eslesen_idx {
            havuz[idx].take()
        } else if havuz.len() == yeni_sayisi {
            havuz.get_mut(i).and_then(|slot| {
                if slot.as_ref().and_then(|x| x.id.as_ref()).is_none() {
                    slot.take()
                } else {
                    None
                }
            })
        } else {
            None
        };

        if let Some(mut mevcut) = bulunan {
            if !gecerli_yolcular.is_empty() {
                mevcut
                    .yolcular
                    .retain(|id| gecerli_yolcular.contains(id.as_str()));
                if mevcut.yolcular.is_empty() {
                    mevcut.yolcular = tum_yolcular.clone();
                }
            }
            if mevcut.araclar.is_empty() {
                mevcut.araclar = varsayilan_araclar(a, b);
            }
            yeni_bacaklar.push(mevcut);
        } else {
            yeni_bacaklar.push(BacakAyar {
                id: None,
                yolcular: tum_yolcular.clone(),
                araclar: varsayilan_araclar(a, b),
                ek: serde_json::Map::new(),
            });
        }
    }

    plan.bacaklar = yeni_bacaklar;
}

/// Duraklar değişmeden önceki `eski_duraklar` biliniyorken şehir çiftlerini birebir eşleştirerek bacakları yeniden kurar.
pub fn bacaklari_yeniden_kur_eski_ile(plan: &mut Plan, eski_duraklar: &[Durak]) {
    if plan.duraklar.len() < 2 {
        plan.bacaklar.clear();
        return;
    }
    if eski_duraklar.len() != plan.bacaklar.len() + 1 {
        bacaklari_yeniden_kur(plan);
        return;
    }

    let tum_yolcular: Vec<String> = plan.yolcular.iter().map(|y| y.id.clone()).collect();
    let gecerli_yolcular: HashSet<&str> = tum_yolcular.iter().map(|s| s.as_str()).collect();
    let mut eski_ciftler: Vec<Option<(String, String, BacakAyar)>> = plan
        .bacaklar
        .drain(..)
        .enumerate()
        .map(|(i, bacak)| {
            Some((
                eski_duraklar[i].sehir.clone(),
                eski_duraklar[i + 1].sehir.clone(),
                bacak,
            ))
        })
        .collect();

    let mut yeni_bacaklar = Vec::with_capacity(plan.duraklar.len() - 1);
    for i in 0..plan.duraklar.len() - 1 {
        let a = &plan.duraklar[i].sehir;
        let b = &plan.duraklar[i + 1].sehir;

        let eslesen_idx = eski_ciftler.iter().position(|item| {
            item.as_ref()
                .is_some_and(|(ea, eb, _)| ea == a && eb == b)
        });

        if let Some(idx) = eslesen_idx {
            if let Some((_, _, mut mevcut)) = eski_ciftler[idx].take() {
                if !gecerli_yolcular.is_empty() {
                    mevcut
                        .yolcular
                        .retain(|id| gecerli_yolcular.contains(id.as_str()));
                    if mevcut.yolcular.is_empty() {
                        mevcut.yolcular = tum_yolcular.clone();
                    }
                }
                if mevcut.araclar.is_empty() {
                    mevcut.araclar = varsayilan_araclar(a, b);
                }
                yeni_bacaklar.push(mevcut);
                continue;
            }
        }

        yeni_bacaklar.push(BacakAyar {
            id: None,
            yolcular: tum_yolcular.clone(),
            araclar: varsayilan_araclar(a, b),
            ek: serde_json::Map::new(),
        });
    }
    plan.bacaklar = yeni_bacaklar;
}

/// Rota zaten başladığı şehirde bitiyorsa true döndürür.
pub fn donus_var_mi(plan: &Plan) -> bool {
    if plan.duraklar.len() < 2 {
        return false;
    }
    let ilk = plan.duraklar.first().map(|d| d.sehir.trim()).unwrap_or("");
    let son = plan.duraklar.last().map(|d| d.sehir.trim()).unwrap_or("");
    !ilk.is_empty() && ilk == son
}

/// Rotanın tersini mevcut durakların sonuna ekler (ara duraklar geçiş noktası olur).
/// Dönüş tarihi mevcut son durağa yazılır; önceki bacakların yolcu/araç ayarları ters sırada aynalanır.
pub fn donus_ekle(plan: &mut Plan, tarih: &str, esneklik: u8) {
    if plan.duraklar.len() < 2 || donus_var_mi(plan) {
        return;
    }
    let esnek = esneklik.min(3);
    let gidis_sayisi = plan.duraklar.len();

    // Mevcut son durak artık dönüş başlangıcı olur
    if let Some(son) = plan.duraklar.last_mut() {
        son.tarih = Some(tarih.to_string());
        son.esneklik = esnek;
        son.gecis_noktasi = false;
        son.kalis_gece = None;
    }

    // Önceki durakları ters sırada ekle (ara duraklar geçiş noktası, en son durak varış)
    let onceki_duraklar: Vec<Durak> = plan.duraklar[..gidis_sayisi - 1].to_vec();
    let ters_uzunluk = onceki_duraklar.len();
    for (k, d) in onceki_duraklar.into_iter().rev().enumerate() {
        let son_varis_mi = k + 1 == ters_uzunluk;
        plan.duraklar.push(Durak {
            sehir: d.sehir,
            noktalar: d.noktalar,
            tarih: None,
            esneklik: if son_varis_mi { 0 } else { esnek },
            kalis_gece: None,
            gecis_noktasi: !son_varis_mi,
            ek: serde_json::Map::new(),
        });
    }

    // Gidiş bacaklarını ters sırada aynalayarak dönüş bacaklarını ekle
    let gidis_bacak_sayisi = gidis_sayisi - 1;
    let mevcut_bacaklar = plan.bacaklar.clone();
    for idx in (0..gidis_bacak_sayisi).rev() {
        if let Some(kaynak) = mevcut_bacaklar.get(idx) {
            plan.bacaklar.push(BacakAyar {
                id: None,
                yolcular: kaynak.yolcular.clone(),
                araclar: kaynak.araclar.clone(),
                ek: serde_json::Map::new(),
            });
        } else {
            let a = &plan.duraklar[plan.bacaklar.len()].sehir;
            let b = &plan.duraklar[plan.bacaklar.len() + 1].sehir;
            plan.bacaklar.push(BacakAyar {
                id: None,
                yolcular: plan.yolcular.iter().map(|y| y.id.clone()).collect(),
                araclar: varsayilan_araclar(a, b),
                ek: serde_json::Map::new(),
            });
        }
    }
}

/// API'den gelen `hatalar[].alan` değerini alan yoluyla (tam veya alt alan sınırı) eşleştirir.
pub fn hata_alani_eslesir(hata: &Hata, yol: &str) -> bool {
    if hata.alan == yol {
        return true;
    }
    hata.alan.starts_with(yol) && hata.alan.as_bytes().get(yol.len()) == Some(&b'.')
}

/// `"YYYY-MM-DD"` dizgesini `NaiveDate` olarak ayrıştırır.
pub fn tarih_ayristir(s: &str) -> Option<NaiveDate> {
    let temiz = s.trim();
    if temiz.len() < 10 {
        return None;
    }
    NaiveDate::parse_from_str(&temiz[..10], "%Y-%m-%d").ok()
}

/// Durağın etkin kalkış tarihini döndürür (tarihi yoksa ve ara duraksa önceki duraktan + kalış gecesinden hesaplar).
pub fn durak_etkin_tarihi(duraklar: &[Durak], idx: usize) -> Option<NaiveDate> {
    let d = duraklar.get(idx)?;
    if let Some(t) = d.tarih.as_deref().and_then(tarih_ayristir) {
        return Some(t);
    }
    if idx > 0 {
        let onceki = durak_etkin_tarihi(duraklar, idx - 1)?;
        let gece = if d.gecis_noktasi {
            0
        } else {
            d.kalis_gece.unwrap_or(0).max(0)
        };
        return Some(onceki + ChronoDuration::days(gece as i64));
    }
    None
}

/// Ara durak için bir önceki durağa göre gece sayısını hesaplar (geçiş noktası veya tarih yoksa `None`).
pub fn durak_kalis_gecesi(duraklar: &[Durak], idx: usize) -> Option<i64> {
    if idx == 0 || idx + 1 >= duraklar.len() {
        return None;
    }
    let d = &duraklar[idx];
    if d.gecis_noktasi {
        return None;
    }
    let simdiki = d.tarih.as_deref().and_then(tarih_ayristir)?;
    let onceki = durak_etkin_tarihi(duraklar, idx - 1)?;
    Some((simdiki - onceki).num_days())
}

/// Plan tarih aralığını hesaplar: `(ilk_tarih, son_tarih)`.
pub fn plan_tarih_araligi(plan: &Plan) -> Option<(NaiveDate, NaiveDate)> {
    let mut en_erken: Option<NaiveDate> = None;
    let mut en_gec: Option<NaiveDate> = None;
    for i in 0..plan.duraklar.len() {
        if let Some(t) = plan.duraklar[i].tarih.as_deref().and_then(tarih_ayristir) {
            en_erken = Some(en_erken.map_or(t, |e| e.min(t)));
            en_gec = Some(en_gec.map_or(t, |e| e.max(t)));
        }
    }
    match (en_erken, en_gec) {
        (Some(a), Some(b)) => Some((a, b)),
        _ => None,
    }
}

/// Üst şerit özeti için `"26.12 → 08.01 · 13 gün"` biçiminde toplam süre metni üretir.
pub fn plan_toplam_sure_metni(plan: &Plan) -> String {
    let Some((ilk, son)) = plan_tarih_araligi(plan) else {
        return "tarih belirlenmedi".to_string();
    };
    let ilk_s = bicim::kisa_tarih(&ilk.format("%Y-%m-%d").to_string());
    let son_s = bicim::kisa_tarih(&son.format("%Y-%m-%d").to_string());
    let gun = (son - ilk).num_days();
    if gun <= 0 {
        format!("{} · tek gün", ilk_s)
    } else {
        format!("{} → {} · {} gün", ilk_s, son_s, gun)
    }
}

/// İzin bitiş tarihinin yolculuk bitmeden (veya yolculuk içinde) dolup dolmadığını kontrol eder.
pub fn izin_yolculukta_bitiyor_mu(
    izin: &YolcuIzin,
    plan: &Plan,
    onizleme: Option<&Onizleme>,
    yolcu_id: &str,
) -> bool {
    if let Some(bitis) = izin.bitis.as_deref().and_then(tarih_ayristir) {
        if let Some((_, son_tarih)) = plan_tarih_araligi(plan) {
            if bitis <= son_tarih {
                return true;
            }
        }
    }
    if let Some(oniz) = onizleme {
        for b in &oniz.bacaklar {
            if let Some(eg) = b.en_gec.as_deref().and_then(tarih_ayristir) {
                if let Some(bitis) = izin.bitis.as_deref().and_then(tarih_ayristir) {
                    if bitis <= eg {
                        return true;
                    }
                }
            }
            for yd in &b.yolcu_denetimi {
                if yd.yolcu == yolcu_id && yd.mesaj.contains("bitiyor") {
                    return true;
                }
            }
        }
    }
    false
}

/// Bir yolcu silinmeden önce kaç bacakta ve yan turda yer aldığını döndürür.
pub fn yolcu_sil_etkisi(plan: &Plan, yolcu_id: &str) -> (usize, usize) {
    let bacak_sayisi = plan
        .bacaklar
        .iter()
        .filter(|b| b.yolcular.iter().any(|y| y == yolcu_id))
        .count();
    let yan_tur_sayisi = plan
        .yan_turlar
        .iter()
        .filter(|yt| yt.yolcular.iter().any(|y| y == yolcu_id))
        .count();
    (bacak_sayisi, yan_tur_sayisi)
}

/// Yolcuyu listeden ve tüm bacak / yan turlardan çıkarır.
pub fn yolcu_cikar(plan: &mut Plan, yolcu_idx: usize) {
    if yolcu_idx >= plan.yolcular.len() {
        return;
    }
    let silinen_id = plan.yolcular.remove(yolcu_idx).id;
    for b in &mut plan.bacaklar {
        b.yolcular.retain(|id| id != &silinen_id);
    }
    for yt in &mut plan.yan_turlar {
        yt.yolcular.retain(|id| id != &silinen_id);
    }
}

/// Nokta türü (`havalimani`, `yht`, `otogar`) için simge döndürür.
pub fn nokta_turu_simgesi(tur: &str) -> &'static str {
    match tur {
        "havalimani" => "✈",
        "yht" => "🚄",
        "otogar" => "🚌",
        _ => "📍",
    }
}

/// Araç kodu (`ucak`, `yht`, `otobus`) için simge ve kısa Türkçe etiket döndürür.
pub fn arac_etiketi(arac: &str) -> (&'static str, &'static str) {
    match arac {
        "ucak" => ("✈", "Uçak"),
        "yht" => ("🚄", "YHT"),
        "otobus" => ("🚌", "Otobüs"),
        _ => ("•", "Diğer"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::Yolcu;

    fn ornek_yolcular() -> Vec<Yolcu> {
        vec![
            Yolcu {
                id: "ali".to_string(),
                ad: "Ali".to_string(),
                uyruklar: vec!["TR".to_string()],
                izinler: vec![],
                ek: serde_json::Map::new(),
            },
            Yolcu {
                id: "ayse".to_string(),
                ad: "Ayşe".to_string(),
                uyruklar: vec!["TR".to_string()],
                izinler: vec![],
                ek: serde_json::Map::new(),
            },
        ]
    }

    fn ornek_durak(sehir: &str, tarih: Option<&str>, esneklik: u8, gecis: bool) -> Durak {
        Durak {
            sehir: sehir.to_string(),
            noktalar: None,
            tarih: tarih.map(str::to_string),
            esneklik,
            kalis_gece: None,
            gecis_noktasi: gecis,
            ek: serde_json::Map::new(),
        }
    }

    #[test]
    fn test_yolcu_id_uret_turkce_ve_tekil() {
        assert_eq!(yolcu_id_uret("Ali", &[]), "ali");
        assert_eq!(yolcu_id_uret("Ayşe", &[]), "ayse");
        assert_eq!(yolcu_id_uret("Çağrı Şen", &[]), "cagri_sen");
        assert_eq!(yolcu_id_uret("Ömer Işık Ünlü", &[]), "omer_isik_unlu");
        assert_eq!(yolcu_id_uret("   ", &[]), "yolcu");

        let mevcut = vec!["ali".to_string(), "ali_2".to_string()];
        assert_eq!(yolcu_id_uret("Ali", &mevcut), "ali_3");
    }

    #[test]
    fn test_bacaklari_yeniden_kur_ekle_sil_tasi() {
        let mut plan = Plan {
            surum: 1,
            ad: "Test".to_string(),
            yolcular: ornek_yolcular(),
            duraklar: vec![
                ornek_durak("İstanbul", Some("2026-12-27"), 2, false),
                ornek_durak("Tokyo", Some("2027-01-06"), 2, false),
                ornek_durak("İstanbul", None, 0, false),
            ],
            bacaklar: vec![
                BacakAyar {
                    id: Some("ist_tyo".to_string()),
                    yolcular: vec!["ali".to_string()],
                    araclar: vec!["ucak".to_string()],
                    ek: serde_json::Map::new(),
                },
                BacakAyar {
                    id: Some("tyo_ist".to_string()),
                    yolcular: vec!["ali".to_string(), "ayse".to_string()],
                    araclar: vec!["ucak".to_string()],
                    ek: serde_json::Map::new(),
                },
            ],
            yan_turlar: vec![],
            ek: serde_json::Map::new(),
        };

        // 1. Başa Ankara ekle: ist_tyo ve tyo_ist korunmalı, yeni ank_ist tüm yolcu + olası araçları almalı
        plan.duraklar
            .insert(0, ornek_durak("Ankara", Some("2026-12-26"), 1, false));
        bacaklari_yeniden_kur(&mut plan);
        assert_eq!(plan.bacaklar.len(), 3);
        assert_eq!(plan.bacaklar[0].id, None);
        assert_eq!(plan.bacaklar[0].yolcular, vec!["ali", "ayse"]);
        assert_eq!(plan.bacaklar[0].araclar, vec!["ucak", "yht", "otobus"]);
        assert_eq!(plan.bacaklar[1].id.as_deref(), Some("ist_tyo"));
        assert_eq!(plan.bacaklar[1].yolcular, vec!["ali"]); // özel ayar korundu
        assert_eq!(plan.bacaklar[2].id.as_deref(), Some("tyo_ist"));
        assert_eq!(plan.bacaklar[2].yolcular, vec!["ali", "ayse"]);

        // 2. Ankara'yı sil: ist_tyo ve tyo_ist hâlâ korunmalı
        plan.duraklar.remove(0);
        bacaklari_yeniden_kur(&mut plan);
        assert_eq!(plan.bacaklar.len(), 2);
        assert_eq!(plan.bacaklar[0].id.as_deref(), Some("ist_tyo"));
        assert_eq!(plan.bacaklar[0].yolcular, vec!["ali"]);
        assert_eq!(plan.bacaklar[1].id.as_deref(), Some("tyo_ist"));

        // 3. Durak taşı (eski_duraklar ile): Tokyo <-> Osaka araya girince mevcut ayarlar yine korunmalı
        let eski = plan.duraklar.clone();
        plan.duraklar.swap(1, 2); // [İstanbul, İstanbul, Tokyo]
        bacaklari_yeniden_kur_eski_ile(&mut plan, &eski);
        assert_eq!(plan.bacaklar.len(), 2);
        assert_eq!(plan.bacaklar[0].id, None); // İstanbul -> İstanbul yeni çift
        assert_eq!(plan.bacaklar[1].id.as_deref(), Some("ist_tyo"));
        assert_eq!(plan.bacaklar[1].yolcular, vec!["ali"]);
    }

    #[test]
    fn test_donus_ekle_ters_sira_ve_gecis() {
        let mut plan = Plan {
            surum: 1,
            ad: "Gidiş".to_string(),
            yolcular: ornek_yolcular(),
            duraklar: vec![
                ornek_durak("Ankara", Some("2026-12-26"), 1, false),
                ornek_durak("İstanbul", None, 0, true),
                ornek_durak("Tokyo", None, 0, false),
            ],
            bacaklar: vec![
                BacakAyar {
                    id: Some("ank_ist".to_string()),
                    yolcular: vec!["ali".to_string(), "ayse".to_string()],
                    araclar: vec!["ucak".to_string(), "yht".to_string()],
                    ek: serde_json::Map::new(),
                },
                BacakAyar {
                    id: Some("ist_tyo".to_string()),
                    yolcular: vec!["ali".to_string()],
                    araclar: vec!["ucak".to_string()],
                    ek: serde_json::Map::new(),
                },
            ],
            yan_turlar: vec![],
            ek: serde_json::Map::new(),
        };

        assert!(!donus_var_mi(&plan));
        donus_ekle(&mut plan, "2027-01-06", 2);
        assert!(donus_var_mi(&plan));

        let sehirler: Vec<&str> = plan.duraklar.iter().map(|d| d.sehir.as_str()).collect();
        assert_eq!(sehirler, vec!["Ankara", "İstanbul", "Tokyo", "İstanbul", "Ankara"]);
        assert_eq!(plan.duraklar[2].tarih.as_deref(), Some("2027-01-06"));
        assert_eq!(plan.duraklar[2].esneklik, 2);
        assert!(plan.duraklar[3].gecis_noktasi); // ara durak geçiş noktası
        assert!(!plan.duraklar[4].gecis_noktasi); // son varış
        assert_eq!(plan.duraklar[4].tarih, None);

        assert_eq!(plan.bacaklar.len(), 4);
        // Tokyo -> İstanbul, İstanbul -> Tokyo'nun aynası (yalnız Ali, uçak)
        assert_eq!(plan.bacaklar[2].yolcular, vec!["ali"]);
        assert_eq!(plan.bacaklar[2].araclar, vec!["ucak"]);
        // İstanbul -> Ankara, Ankara -> İstanbul'un aynası (Ali + Ayşe, uçak + yht)
        assert_eq!(plan.bacaklar[3].yolcular, vec!["ali", "ayse"]);
        assert_eq!(plan.bacaklar[3].araclar, vec!["ucak", "yht"]);
    }

    #[test]
    fn test_hata_alani_eslesir() {
        let h1 = Hata {
            alan: "duraklar.2.tarih".to_string(),
            mesaj: "Tarih gerekli".to_string(),
            ek: serde_json::Map::new(),
        };
        assert!(hata_alani_eslesir(&h1, "duraklar.2.tarih"));
        assert!(hata_alani_eslesir(&h1, "duraklar.2"));
        assert!(hata_alani_eslesir(&h1, "duraklar"));
        assert!(!hata_alani_eslesir(&h1, "duraklar.20"));
        assert!(!hata_alani_eslesir(&h1, "duraklar.1.tarih"));

        let h2 = Hata {
            alan: "bacaklar.1.yolcular".to_string(),
            mesaj: "En az bir yolcu".to_string(),
            ek: serde_json::Map::new(),
        };
        assert!(hata_alani_eslesir(&h2, "bacaklar.1.yolcular"));
        assert!(!hata_alani_eslesir(&h2, "bacaklar.1.araclar"));
    }

    #[test]
    fn test_kalis_gecesi_ve_toplam_sure() {
        let plan = Plan {
            surum: 1,
            ad: "Tokyo".to_string(),
            yolcular: ornek_yolcular(),
            duraklar: vec![
                ornek_durak("Ankara", Some("2026-12-26"), 1, false),
                ornek_durak("İstanbul", None, 0, true),
                ornek_durak("Tokyo", Some("2027-01-08"), 2, false),
                ornek_durak("Ankara", None, 0, false),
            ],
            bacaklar: vec![],
            yan_turlar: vec![],
            ek: serde_json::Map::new(),
        };
        assert_eq!(plan_toplam_sure_metni(&plan), "26.12 → 08.01 · 13 gün");
        assert_eq!(durak_kalis_gecesi(&plan.duraklar, 1), None); // geçiş noktası
        assert_eq!(durak_kalis_gecesi(&plan.duraklar, 2), Some(13));
    }
}
