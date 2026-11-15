/// Biçimlendirme yardımcıları (TL, yüzde, tarih/saat, dakika).

/// TL tutarını binlik ayırıcı nokta ile yuvarlayarak biçimlendirir.
/// Örnek: `tl(108499.4) == "108.499 TL"`
/// Site modül anahtarı → ekranda okunur mağaza adı (çekirdekteki site_adi.OKUNUR ile aynı)
pub fn site_okunur(ad: &str) -> String {
    match ad {
        "hepsiburada" => "Hepsiburada",
        "trendyol" => "Trendyol",
        "pttavm" => "PttAVM",
        "amazon" => "Amazon",
        "incehesap" => "İnceHesap",
        "mediamarkt" => "MediaMarkt",
        "teknosa" => "Teknosa",
        "pazarama" => "Pazarama",
        "n11" => "n11",
        "vatan" => "Vatan",
        "itopya" => "İtopya",
        "sinerji" => "Sinerji",
        "gaminggentr" => "Gaming.gen.tr",
        "idefix" => "idefix",
        "gamegaraj" => "GameGaraj",
        "akakce" => "Akakçe",
        "cimri" => "Cimri",
        "epey" => "Epey",
        diger => return diger.to_string(),
    }
    .to_string()
}

pub fn tl(tutar: f64) -> String {
    let yuvarlanmis = tutar.round() as i64;
    let negatif = yuvarlanmis < 0;
    let mut sayi = yuvarlanmis.unsigned_abs().to_string();

    let mut sonuc = String::new();
    let uzunluk = sayi.len();
    for (i, ch) in sayi.drain(..).enumerate() {
        if i > 0 && (uzunluk - i) % 3 == 0 {
            sonuc.push('.');
        }
        sonuc.push(ch);
    }

    if negatif {
        format!("−{} TL", sonuc)
    } else {
        format!("{} TL", sonuc)
    }
}

/// Yüzde değerini Türkçe biçimde (virgül ondalık, başında % ve Unicode eksi `−`) döndürür.
/// Örnek: `yuzde(-8.63) == "−%8,6"`
pub fn yuzde(deger: f64) -> String {
    let mutlak = deger.abs();
    let ondalikli = format!("{:.1}", mutlak).replace('.', ",");
    if deger < -0.000_1 {
        format!("−%{}", ondalikli)
    } else if deger > 0.000_1 {
        format!("+%{}", ondalikli)
    } else {
        "%0,0".to_string()
    }
}

/// İşaretsiz yüzde biçimi (ör. vade farkı için `%13,0`).
pub fn yuzde_isaretsiz(deger: f64) -> String {
    let ondalikli = format!("{:.1}", deger).replace('.', ",");
    format!("%{}", ondalikli)
}

/// ISO-8601 zaman dizgesini `GG.AA.YYYY SS:DD` biçimine çevirir.
/// Örnek: `"2026-11-14T15:07:00+03:00"` -> `"14.11.2026 15:07"`
pub fn iso_tarih(iso: &str) -> String {
    let temiz = iso.trim();
    if temiz.is_empty() || temiz == "?" || temiz == "..." {
        return "—".to_string();
    }

    // "YYYY-MM-DDTHH:MM..." veya "YYYY-MM-DD HH:MM..."
    if temiz.len() >= 16 {
        let tarih_kismi = &temiz[0..10];
        let saat_kismi = &temiz[11..16];
        let parcalar: Vec<&str> = tarih_kismi.split('-').collect();
        if parcalar.len() == 3
            && parcalar[0].len() == 4
            && parcalar[1].len() == 2
            && parcalar[2].len() == 2
            && saat_kismi.contains(':')
        {
            return format!(
                "{}.{}.{} {}",
                parcalar[2], parcalar[1], parcalar[0], saat_kismi
            );
        }
    }

    // Yalnızca tarih "YYYY-MM-DD"
    if temiz.len() == 10 {
        let parcalar: Vec<&str> = temiz.split('-').collect();
        if parcalar.len() == 3
            && parcalar[0].len() == 4
            && parcalar[1].len() == 2
            && parcalar[2].len() == 2
        {
            return format!("{}.{}.{}", parcalar[2], parcalar[1], parcalar[0]);
        }
    }

    temiz.to_string()
}

/// ISO-8601 zaman dizgesinden yalnızca `SS:DD` saat bilgisini çıkarır.
/// Örnek: `"2026-12-27T01:55:00+03:00"` -> `"01:55"`
pub fn iso_saat(iso: &str) -> String {
    let temiz = iso.trim();
    if temiz.len() >= 16 && (temiz.as_bytes()[10] == b'T' || temiz.as_bytes()[10] == b' ') {
        return temiz[11..16].to_string();
    }
    temiz.to_string()
}

/// `YYYY-MM-DD...` dizgesinden gün sayısını (Rata Die benzeri) hesaplar.
fn iso_gun_sayisi(iso: &str) -> Option<i64> {
    let temiz = iso.trim();
    if temiz.len() < 10 {
        return None;
    }
    let parcalar: Vec<&str> = temiz[0..10].split('-').collect();
    if parcalar.len() != 3 {
        return None;
    }
    let y: i64 = parcalar[0].parse().ok()?;
    let m: i64 = parcalar[1].parse().ok()?;
    let d: i64 = parcalar[2].parse().ok()?;
    // Howard Hinnant civil_from_days ters dönüşümü (days_from_civil)
    let y_adj = y - if m <= 2 { 1 } else { 0 };
    let era = if y_adj >= 0 { y_adj } else { y_adj - 399 } / 400;
    let yoe = y_adj - era * 400;
    let m_adj = m + if m > 2 { -3 } else { 9 };
    let doy = (153 * m_adj + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    Some(era * 146097 + doe)
}

/// Kalkış ve varış ISO zamanlarını `"16:20 → 14:30 +1"` biçiminde döndürür.
/// Varış tarihi kalkış tarihinden sonraki bir günse `+1` (veya `+N`) ekler.
pub fn kalkis_varis_saat(kalkis_iso: &str, varis_iso: &str) -> String {
    let k_saat = iso_saat(kalkis_iso);
    let v_saat = iso_saat(varis_iso);
    if let (Some(k_gun), Some(v_gun)) = (iso_gun_sayisi(kalkis_iso), iso_gun_sayisi(varis_iso)) {
        let fark = v_gun - k_gun;
        if fark > 0 {
            return format!("{} → {} +{}", k_saat, v_saat, fark);
        }
    }
    format!("{} → {}", k_saat, v_saat)
}

/// Dakika cinsinden süreyi `"19 sa 45 dk"` biçimine çevirir.
pub fn dakika(dk: u32) -> String {
    let saat = dk / 60;
    let kalan = dk % 60;
    match (saat, kalan) {
        (0, d) => format!("{} dk", d),
        (s, 0) => format!("{} sa", s),
        (s, d) => format!("{} sa {} dk", s, d),
    }
}

/// `"YYYY-MM-DD"` dizgesini `"GG.AA"` kısa biçimine çevirir (ör. `"2026-12-26"` -> `"26.12"`).
pub fn kisa_tarih(iso: &str) -> String {
    let temiz = iso.trim();
    if temiz.len() >= 10 {
        let parcalar: Vec<&str> = temiz[0..10].split('-').collect();
        if parcalar.len() == 3 && parcalar[1].len() == 2 && parcalar[2].len() == 2 {
            return format!("{}.{}", parcalar[2], parcalar[1]);
        }
    }
    "—".to_string()
}

/// Ay numarası (1..=12) için Türkçe kısa ay adı döndürür.
pub fn turkce_ay_kisa(ay: u32) -> &'static str {
    match ay {
        1 => "Oca",
        2 => "Şub",
        3 => "Mar",
        4 => "Nis",
        5 => "May",
        6 => "Haz",
        7 => "Tem",
        8 => "Ağu",
        9 => "Eyl",
        10 => "Eki",
        11 => "Kas",
        12 => "Ara",
        _ => "?",
    }
}

/// `"YYYY-MM-DD"` için Türkçe kısa gün adı (`Pzt`, `Sal`, `Çar`, `Per`, `Cum`, `Cmt`, `Paz`).
/// `"YYYY-MM-DD"` için Türkçe kısa gün adı (`Pzt`, `Sal`, `Çar`, `Per`, `Cum`, `Cmt`, `Paz`).
pub fn turkce_gun_kisa(iso: &str) -> Option<&'static str> {
    use chrono::{Datelike, NaiveDate, Weekday};
    let temiz = iso.trim();
    if temiz.len() < 10 {
        return None;
    }
    let d = NaiveDate::parse_from_str(&temiz[..10], "%Y-%m-%d").ok()?;
    Some(match d.weekday() {
        Weekday::Mon => "Pzt",
        Weekday::Tue => "Sal",
        Weekday::Wed => "Çar",
        Weekday::Thu => "Per",
        Weekday::Fri => "Cum",
        Weekday::Sat => "Cmt",
        Weekday::Sun => "Paz",
    })
}

/// `"YYYY-MM-DD"` dizgesini `"26 Ara Cmt"` biçiminde Türkçe gün/ay olarak döndürür.
pub fn turkce_gun_ay(iso: &str) -> String {
    let temiz = iso.trim();
    if temiz.len() >= 10 {
        let parcalar: Vec<&str> = temiz[0..10].split('-').collect();
        if parcalar.len() == 3 {
            if let (Ok(ay), Ok(gun)) = (parcalar[1].parse::<u32>(), parcalar[2].parse::<u32>()) {
                let haftanin_gunu = turkce_gun_kisa(temiz).unwrap_or("");
                if haftanin_gunu.is_empty() {
                    return format!("{} {}", gun, turkce_ay_kisa(ay));
                }
                return format!("{} {} {}", gun, turkce_ay_kisa(ay), haftanin_gunu);
            }
        }
    }
    "—".to_string()
}

/// İki harfli ISO ülke kodunu bayrak emojisine çevirir (ör. `"TR"` -> `"🇹🇷"`).
pub fn bayrak_emoji(iso2: &str) -> String {
    let temiz = iso2.trim().to_ascii_uppercase();
    let bytes = temiz.as_bytes();
    if bytes.len() == 2 && bytes[0].is_ascii_uppercase() && bytes[1].is_ascii_uppercase() {
        let c1 = char::from_u32(0x1F1E6 + (bytes[0] - b'A') as u32);
        let c2 = char::from_u32(0x1F1E6 + (bytes[1] - b'A') as u32);
        if let (Some(a), Some(b)) = (c1, c2) {
            return format!("{}{}", a, b);
        }
    }
    "🏳".to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tl_bicimi() {
        assert_eq!(tl(108499.4), "108.499 TL");
        assert_eq!(tl(110000.0), "110.000 TL");
        assert_eq!(tl(111549.03), "111.549 TL");
        assert_eq!(tl(1549.0), "1.549 TL");
        assert_eq!(tl(500.0), "500 TL");
        assert_eq!(tl(0.0), "0 TL");
        assert_eq!(tl(1234567.8), "1.234.568 TL");
    }

    #[test]
    fn test_yuzde_bicimi() {
        assert_eq!(yuzde(-8.63), "−%8,6");
        assert_eq!(yuzde(-10.31), "−%10,3");
        assert_eq!(yuzde(5.24), "+%5,2");
        assert_eq!(yuzde(0.0), "%0,0");
        assert_eq!(yuzde_isaretsiz(13.0), "%13,0");
    }

    #[test]
    fn test_iso_tarih_bicimi() {
        assert_eq!(iso_tarih("2026-11-14T15:07:00+03:00"), "14.11.2026 15:07");
        assert_eq!(iso_tarih("2026-11-14T14:35:07+03:00"), "14.11.2026 14:35");
        assert_eq!(iso_tarih("2026-12-27"), "27.12.2026");
        assert_eq!(iso_tarih("?"), "—");
        assert_eq!(iso_tarih(""), "—");
        assert_eq!(iso_saat("2026-12-27T01:55:00+03:00"), "01:55");
        assert_eq!(
            kalkis_varis_saat("2026-12-27T16:20:00+03:00", "2026-12-28T14:30:00+09:00"),
            "16:20 → 14:30 +1"
        );
        assert_eq!(
            kalkis_varis_saat("2027-01-02T11:50:00+09:00", "2027-01-02T14:50:00+09:00"),
            "11:50 → 14:50"
        );
    }

    #[test]
    fn test_dakika_bicimi() {
        assert_eq!(dakika(1185), "19 sa 45 dk");
        assert_eq!(dakika(230), "3 sa 50 dk");
        assert_eq!(dakika(180), "3 sa");
        assert_eq!(dakika(45), "45 dk");
        assert_eq!(dakika(0), "0 dk");
    }

    #[test]
    fn test_turkce_plan_tarih_ve_bayrak() {
        assert_eq!(kisa_tarih("2026-12-26"), "26.12");
        assert_eq!(kisa_tarih("2027-01-08"), "08.01");
        assert_eq!(turkce_gun_kisa("2026-11-14"), Some("Cmt"));
        assert_eq!(turkce_gun_ay("2026-12-26"), "26 Ara Cmt");
        assert_eq!(turkce_gun_ay("2027-01-06"), "6 Oca Çar");
        assert_eq!(bayrak_emoji("TR"), "🇹🇷");
        assert_eq!(bayrak_emoji("jp"), "🇯🇵");
    }
}

/// Taşıma türü simgesi (15.11): `arac` (ucak | yht | tren | otobus) yoksa firma adından tahmin.
const OTOBUS_FIRMALARI: &[&str] = &[
    "metro turizm", "kamil koç", "kamil koc", "pamukkale", "ulusoy", "varan", "nilüfer", "süha", "lüks karadeniz",
    "has turizm", "efe tur", "anadolu ulaşım", "flixbus", "obilet", "kontur", "uludağ turizm", "radar turizm",
    "sözer turizm", "tokat seyahat",
];

pub fn arac_simgesi(arac: Option<&str>, firma: &str) -> &'static str {
    let f = firma.to_lowercase();
    let kelime = |k: &str| f.split(|c: char| !c.is_alphanumeric()).any(|w| w == k);
    let otobus = ["otobüs", "otobus", "bus", "coach", "turizm", "seyahat"].iter().any(|k| kelime(k))
        || OTOBUS_FIRMALARI.iter().any(|o| f.contains(o));
    match arac.unwrap_or("") {
        "otobus" => "🚌",
        "yht" | "tren" => "🚄",
        "ucak" if !otobus => "✈",
        _ if otobus => "🚌",
        _ if kelime("tren") || kelime("yht") || kelime("tcdd") || kelime("train") => "🚄",
        _ => "✈",
    }
}

/// "🚌 Kamil Koç" / "✈ Türk Hava Yolları"; firma adı yoksa (ya da yalnız tür adıysa) en azından "🚌 Otobüs" / "✈ Havayolu".
pub fn firma_metni(arac: Option<&str>, firma: &str) -> String {
    let simge = arac_simgesi(arac, firma);
    let f = firma.trim();
    let bos = f.is_empty() || f == "?" || f == "-";
    let ad = if bos {
        match simge {
            "🚌" => "Otobüs · firma yok",
            "🚄" => "Tren · firma yok",
            _ => "Havayolu bilinmiyor",
        }
        .to_string()
    } else if simge == "🚌" && matches!(f.to_lowercase().as_str(), "otobüs" | "otobus" | "bus") {
        "Otobüs · firma yok".to_string()
    } else {
        f.to_string()
    };
    format!("{simge} {ad}")
}

#[cfg(test)]
mod firma_testleri {
    use super::*;

    #[test]
    fn simge_ve_metin() {
        assert_eq!(firma_metni(Some("ucak"), "Türk Hava Yolları"), "✈ Türk Hava Yolları");
        assert_eq!(firma_metni(Some("otobus"), "Kamil Koç"), "🚌 Kamil Koç");
        assert_eq!(firma_metni(Some("ucak"), "Otobüs"), "🚌 Otobüs · firma yok");
        assert_eq!(firma_metni(None, "Otobüs"), "🚌 Otobüs · firma yok");
        assert_eq!(firma_metni(Some("ucak"), "Metro Turizm"), "🚌 Metro Turizm");
        assert_eq!(firma_metni(Some("yht"), ""), "🚄 Tren · firma yok");
        assert_eq!(firma_metni(None, "?"), "✈ Havayolu bilinmiyor");
        assert_eq!(firma_metni(None, "Air Busan"), "✈ Air Busan");
    }
}
