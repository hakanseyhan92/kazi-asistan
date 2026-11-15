//! Ayarlar → "Paralel tarama ve hız" kartının saf yardımcıları (arayüzden bağımsız, test edilebilir).

use crate::api::SistemBilgisi;

/// Seçilen sekme sayısında tahmini toplam bellek (MB): Brave tabanı + sekme başına maliyet.
pub fn tahmini_ram_mb(sekme: u32, sistem: &SistemBilgisi) -> f64 {
    sistem.brave_taban_mb + f64::from(sekme) * sistem.sekme_basi_mb
}

#[derive(Debug, Clone, PartialEq)]
pub enum Uyari {
    Yok,
    Sari(String),
    Kirmizi(String),
}

/// "1,9 GB" biçimi (Türkçe ondalık virgül, tek basamak).
pub fn gb_metni(gb: f64) -> String {
    format!("{:.1} GB", gb).replace('.', ",")
}

fn mb_gb(mb: f64) -> f64 {
    mb / 1024.0
}

/// Sekme sayısı için bellek/işlemci uyarısı.
pub fn uyari(sekme: u32, sistem: &SistemBilgisi) -> Uyari {
    let tahmin_mb = tahmini_ram_mb(sekme, sistem);
    let bos_mb = sistem.bos_ram_gb * 1024.0;
    let tahmin = gb_metni(mb_gb(tahmin_mb));
    let bos = gb_metni(sistem.bos_ram_gb);

    if tahmin_mb > bos_mb * 0.8 {
        return Uyari::Kirmizi(format!(
            "Tahmini bellek {tahmin}, boş bellek {bos}: bilgisayar yavaşlayabilir veya Brave kapanabilir. Sekme sayısını azalt."
        ));
    }
    let cpu_asimi = sistem.cpu_cekirdek > 0 && f64::from(sekme) > f64::from(sistem.cpu_cekirdek) * 1.5;
    if tahmin_mb > bos_mb * 0.5 {
        return Uyari::Sari(format!(
            "Tahmini bellek {tahmin}, boş belleğin yarısından fazla ({bos} boş). Başka uygulamalar yavaşlayabilir."
        ));
    }
    if cpu_asimi {
        return Uyari::Sari(format!(
            "{sekme} sekme, {} işlemci çekirdeğine göre fazla; sayfalar yavaş açılabilir.",
            sistem.cpu_cekirdek
        ));
    }
    Uyari::Yok
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sistem() -> SistemBilgisi {
        SistemBilgisi {
            ram_gb: 15.5,
            bos_ram_gb: 11.3,
            cpu_cekirdek: 8,
            sekme_basi_mb: 250.0,
            brave_taban_mb: 700.0,
            onerilen_sekme: 10,
        }
    }

    #[test]
    fn tahmin_taban_artı_sekme() {
        assert_eq!(tahmini_ram_mb(0, &sistem()), 700.0);
        assert_eq!(tahmini_ram_mb(3, &sistem()), 1450.0);
        assert_eq!(tahmini_ram_mb(10, &sistem()), 3200.0);
    }

    #[test]
    fn az_sekme_uyarisiz() {
        assert_eq!(uyari(2, &sistem()), Uyari::Yok);
        assert_eq!(uyari(10, &sistem()), Uyari::Yok); // 3,1 GB < %50 * 11,3; 10 <= 12
    }

    #[test]
    fn bellek_sari() {
        let mut s = sistem();
        s.bos_ram_gb = 4.0; // 4096 MB; 3200 > 2048 ama < 3276,8
        match uyari(10, &s) {
            Uyari::Sari(m) => assert!(m.contains("3,1 GB") && m.contains("4,0 GB"), "{m}"),
            d => panic!("sarı beklenirdi: {d:?}"),
        }
    }

    #[test]
    fn bellek_kirmizi() {
        let mut s = sistem();
        s.bos_ram_gb = 3.0; // 3072 MB; 3200 > %80
        assert!(matches!(uyari(10, &s), Uyari::Kirmizi(_)));
    }

    #[test]
    fn cpu_asimi_sari() {
        let mut s = sistem();
        s.cpu_cekirdek = 4; // 10 > 6
        s.bos_ram_gb = 64.0;
        match uyari(10, &s) {
            Uyari::Sari(m) => assert!(m.contains("4 işlemci çekirdeği"), "{m}"),
            d => panic!("sarı beklenirdi: {d:?}"),
        }
        assert_eq!(uyari(6, &s), Uyari::Yok); // tam sınır aşılmaz
    }

    #[test]
    fn kirmizi_cpu_sariyi_ezer() {
        let s = SistemBilgisi { cpu_cekirdek: 2, bos_ram_gb: 1.0, ..sistem() };
        assert!(matches!(uyari(10, &s), Uyari::Kirmizi(_)));
    }

    #[test]
    fn gb_biçimi_virgullü() {
        assert_eq!(gb_metni(1.94), "1,9 GB");
        assert_eq!(gb_metni(11.3), "11,3 GB");
        assert_eq!(gb_metni(0.0), "0,0 GB");
    }

    #[test]
    fn bilinmeyen_cpu_sari_uretmez() {
        let s = SistemBilgisi { cpu_cekirdek: 0, bos_ram_gb: 64.0, ..sistem() };
        assert_eq!(uyari(10, &s), Uyari::Yok);
    }
}
