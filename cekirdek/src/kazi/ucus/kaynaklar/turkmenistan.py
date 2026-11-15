"""Turkmenistan Airlines (turkmenistanairlines.tm) — DESTEKLENMİYOR (CAPTCHA).

14.11.2026 canlı incelemesi:
- Ana sayfa formu (dış hatlar sekmesi) GET ile
  `https://turkmenistanairlines.tm/tm/flights/search` adresine gidiyor;
  parametreler: `search_type=external, is_cship, departPort, arrivalPort,
  tripType, departDate (MM/DD/YYYY), arrivalDate, adult, child, infant`.
  `departPort/arrivalPort` IATA değil, `/tm/get_external_ports` sayısal
  kimlikleridir (IST=4, ICN=32, ASB=5).
- Form gönderiminde `#captchaPopup` zorunlu: `home.js` `submitCaptcha`
  düğmesi olmadan `form.submit()` yapmıyor. POST/GET `/refresh-captcha` ile
  resim doğrulaması isteniyor.
- Doğrudan arama URL'si (sayısal limanlar ve form `_token`'ı ile de) HTTP 302
  ile ana sayfaya dönüyor; sunucu oturumda çözülmüş CAPTCHA istiyor.
- CAPTCHA atlatma yasak olduğundan kaynak kapalıdır. Ayrıca dış hat liman
  listesinde Tokyo (NRT/HND) yok: IST→NRT bacağı bu havayolunda zaten yoktur,
  bu yüzden o bacakta `ara()` boş liste döndürür.

Sözleşme: `ara()` desteklenen hatta `BotEngeli` fırlatır.
"""
from __future__ import annotations

import logging

from ..model import BotEngeli, bacak_bilgi

log = logging.getLogger("ucus.turkmenistan")

KAYNAK = "turkmenistan"
SITE = "https://turkmenistanairlines.tm/"
DESTEKLENIYOR = False
NEDEN = ("arama formu zorunlu CAPTCHA (#captchaPopup / submitCaptcha) istiyor; "
         "doğrudan /tm/flights/search URL'si 302 ile ana sayfaya dönüyor "
         "(14.11.2026 canlı deneme, form _token'ı dahil). CAPTCHA atlatma yasak.")

# /tm/get_external_ports (14.11.2026 canlı yanıt) — IATA → sayısal liman kimliği
LIMANLAR = {
    "AUH": 23, "ASB": 5, "BKK": 2, "PEK": 1, "DEL": 11, "DXB": 7,
    "FRA": 26, "SGN": 28, "IST": 4, "JED": 29, "KZN": 16, "KUL": 30,
    "LGW": 31, "MXP": 27, "ICN": 32,
}
SEHIR_LIMAN = {
    "TYO": None, "SEL": "ICN", "IST": "IST",
}


def liman_id(kod: str) -> int | None:
    k = str(kod or "").upper().strip()
    k = SEHIR_LIMAN.get(k) or k
    return LIMANLAR.get(k)


def ymd_dmY(tarih: str) -> str:
    """2026-12-27 → 12/27/2026 (sitenin takvim gizli alan biçimi)."""
    y, m, d = str(tarih).split("-")
    return f"{int(m)}/{int(d)}/{y}"


def url_olustur(kalkis: str, varis: str, tarih: str, yolcu: int = 1,
                cocuk: int = 0, bebek: int = 0) -> str | None:
    o, d = liman_id(kalkis), liman_id(varis)
    if o is None or d is None:
        return None
    return ("https://turkmenistanairlines.tm/tm/flights/search?"
            "search_type=external&is_cship=1"
            f"&departPort={o}&arrivalPort={d}&tripType=oneway"
            f"&departDate={ymd_dmY(tarih)}&arrivalDate="
            f"&adult={int(yolcu)}&child={int(cocuk)}&infant={int(bebek)}")


def captcha_var(html: str) -> bool:
    """Sayfada zorunlu CAPTCHA akışı var mı (gerçek ana sayfa fixture'ı)."""
    h = html or ""
    return "captchaPopup" in h or 'id="captchaInput"' in h


def ara(t, bacak, tarih: str):
    b = bacak_bilgi(bacak)
    kalkis = str(b.get("kalkis") or "").upper()
    varis = str(b.get("varis") or "").upper()
    yolcu = max(int(b.get("yolcu") or 1), 1)
    url = url_olustur(kalkis, varis, tarih, yolcu)
    if url is None:
        # Bu havayolunun dış hat ağında rota yok (ör. Tokyo): gerçek boş sonuç.
        log.info("turkmenistan: %s→%s hatta yok (liman listesinde yok)",
                 kalkis, varis)
        return []
    raise BotEngeli("turkmenistanairlines.tm", url)
