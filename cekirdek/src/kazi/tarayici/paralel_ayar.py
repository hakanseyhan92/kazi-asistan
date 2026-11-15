"""Paralel tarama ve hız ayarları (15.11, kullanıcı: "aynı anda kaç sekmede arama, RAM/CPU'suna güvenen seçsin").

`~/.config/kazi/tarama.json` alanları (gorunurluk.ayar_oku/ayar_yaz):
- `sekme_sayisi`  : aynı anda arama yapan sekme sayısı (2–10). Her sekme AYRI bir sitede çalışır; bir site aynı anda tek sekmede.
- `kategori_esz`  : aynı anda işi havuza giren kategori sayısı (1–3)
- `reklam_engelle`: reklam/izleyici alan adlarına giden istekler iptal (sayfalar hızlanır)
- `gorsel_engelle`: ürün görselleri yüklenmez (en hızlı; fiyat/başlık metinden okunur)
- `medya_engelle` : video/ses yüklenmez (15.11)
- `yazi_tipi_engelle`: web yazı tipleri yüklenmez; sistem yazı tipi kullanılır (15.11)
- `widget_engelle`: canlı destek, anlık bildirim, kişiselleştirme ve anket pencerelerinin betikleri yüklenmez (15.11;
  Insider, Segmentify, Intercom, Zendesk, OneSignal… — açılır pencereler de azalır)
- `dogrulama_bekle_sn`: otomatik taramada "doğrulama / ben robot değilim" sayfası çıkınca beklenecek süre (0 = beklemeden
  sıradaki siteye geç; 15.11 öncesi her doğrulamada 5 dk bekleniyordu, tarama saatlerce "taranıyor" görünüyordu)
- `animasyon_kapat`: sayfaya "hareketi azalt" tercihi + CSS geçiş/animasyonları sıfır (15.11; erişilebilirlik tercihi,
  sıradan kullanıcı ayarı — parmak izi sahteleme değil)
- `gif_engelle`: hareketli GIF'ler yüklenmez (tüm görseller açıkken bile)
- `gomulu_engelle`: YouTube/Vimeo videoları, Google Haritalar, Facebook/Instagram/X/TikTok/Pinterest gömüleri yüklenmez
- `otomatik_oynatma_kapat`: video/ses kullanıcı tıklamadan başlamaz (Brave başlatma bayrağı; sonraki açılışta etkin)
- `bot_muafiyeti`: son 24 saatte doğrulama sayfası çıkan sitede YALNIZ reklam engeli kalır; görsel/yazı tipi/medya/GIF/
  gömülü/animasyon engelleri o sitede kapanır (sayfa sıradan bir ziyaretçideki gibi tam yüklenir → bot riski azalır)
- `chromium_yedek`: Brave'in engellendiği site için görünür Chromium'da bir kez daha dene (gorunur_chromium.py)
- `yerel_profil_yedek`: o da engellenirse son çare: kullanıcının yerel Brave profilinin klonuyla dene (yerel_profil.py)
`sistem_bilgisi()` arayüzdeki RAM/CPU uyarısı için: sekme başına ~250 MB varsayımıyla önerilen üst sınır.
"""
from __future__ import annotations

import os

from .gorunurluk import ayar_oku

VARSAYILAN = {"sekme_sayisi": 3, "kategori_esz": 1, "reklam_engelle": True, "gorsel_engelle": False,
             "chromium_yedek": True, "yerel_profil_yedek": True}
VARSAYILAN.update({"medya_engelle": True, "yazi_tipi_engelle": True, "widget_engelle": True, "dogrulama_bekle_sn": 0})
# 15.11: hızlı HTTP turu (tarayıcısız siteler N dk'da bir; zamanlayici/hizli.py)
VARSAYILAN.update({"http_etkin": True, "http_aralik_dk": 10, "http_ucus": True, "http_tarayicida_atla": True})
VARSAYILAN.update({"tarayici_otomatik": True})   # False: zamanlayıcı Brave kategori taramalarını kendiliğinden başlatmaz
VARSAYILAN.update({"animasyon_kapat": True, "gif_engelle": True, "gomulu_engelle": True, "otomatik_oynatma_kapat": True,
                   "bot_muafiyeti": True})
SINIRLAR = {"sekme_sayisi": (2, 10), "kategori_esz": (1, 3)}
SECENEKLER = {"dogrulama_bekle_sn": (0, 30, 60, 120, 300), "http_aralik_dk": (5, 10, 15, 20, 30, 45, 60)}
SEKME_BASI_MB = 250
BRAVE_TABAN_MB = 700


def paralel_ayarlar(ayar: dict | None = None) -> dict:
    a = ayar_oku() if ayar is None else ayar
    sonuc = {}
    for k, v in VARSAYILAN.items():
        deger = a.get(k, v)
        if k in SINIRLAR:
            alt, ust = SINIRLAR[k]
            deger = v if not isinstance(deger, int) or isinstance(deger, bool) else max(alt, min(ust, deger))
        elif k in SECENEKLER:
            deger = deger if deger in SECENEKLER[k] and not isinstance(deger, bool) else v
        else:
            deger = bool(deger)
        sonuc[k] = deger
    return sonuc


def dogrula(k: str, v) -> str | None:
    """Geçersizse hata metni."""
    if k in SINIRLAR:
        alt, ust = SINIRLAR[k]
        if not isinstance(v, int) or isinstance(v, bool) or not alt <= v <= ust:
            return f"{k}: {alt}–{ust} arası tam sayı"
    elif k in SECENEKLER:
        if isinstance(v, bool) or v not in SECENEKLER[k]:
            return f"{k}: {list(SECENEKLER[k])} seçeneklerinden biri"
    elif k in VARSAYILAN:
        if not isinstance(v, bool):
            return f"{k}: true | false"
    else:
        return f"{k}: bilinmeyen ayar"          # 15.11 (EVREN incelemesi): yazım hatası sessizce kabul edilmesin
    return None


def _meminfo() -> dict[str, int]:
    d = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for satir in f:
                ad, _, deger = satir.partition(":")
                d[ad] = int(deger.split()[0])     # kB
    except (OSError, ValueError, IndexError):
        pass
    return d


def sistem_bilgisi() -> dict:
    m = _meminfo()
    toplam_gb = round(m.get("MemTotal", 0) / 1048576, 1)
    bos_gb = round(m.get("MemAvailable", 0) / 1048576, 1)
    cekirdek = os.cpu_count() or 1
    # boş RAM'in yarısını taramaya ayır; CPU çekirdeği başına en çok 1,5 sekme
    ram_siniri = int(max(0, bos_gb * 1024 / 2 - BRAVE_TABAN_MB) // SEKME_BASI_MB)
    onerilen = max(SINIRLAR["sekme_sayisi"][0], min(SINIRLAR["sekme_sayisi"][1], ram_siniri, int(cekirdek * 1.5)))
    return {"ram_gb": toplam_gb, "bos_ram_gb": bos_gb, "cpu_cekirdek": cekirdek, "sekme_basi_mb": SEKME_BASI_MB,
            "brave_taban_mb": BRAVE_TABAN_MB, "onerilen_sekme": onerilen}


def dogrulama_bekle_sn(ayar: dict | None = None) -> float:
    """Otomatik taramada doğrulama sayfasında beklenecek süre (Tarayici.bekle_k3 varsayılanı)."""
    return float(paralel_ayarlar(ayar)["dogrulama_bekle_sn"])
