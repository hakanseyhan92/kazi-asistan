"""Qatar Airways (qatarairways.com) — DESTEKLENMİYOR (form/derin bağlantı).

14.11.2026 canlı denemesi:
- `/app/booking?tripType=O&from=NRT&to=IST&departure=2027-01-06...` → HTTP 404.
- `/app/booking/flight-search?...` → `/app/booking/?selLang=en&...` adresine
  yönleniyor ve sayfa "Something interrupted the booking journey." diyor.
- `departureDate=2027-01-06` ve `06/01/2027` biçimleri, `children/infants`,
  `class=E` varyantları da aynı sonucu verdi; ağda yalnız üyelik doğrulama
  (`/dapi/public/bff/mobile/membership/...`) çağrıları görüldü, uygunluk
  (availability) ucu tetiklenmedi. Widget otomasyona dirençli (30.10.2026'da
  da aynı). Bu yüzden kaynak kapalı; DOH aktarmalı QR satırları Google/
  Skyscanner/Kayak'tan gelir.

`ara()` `NotImplementedError` fırlatır (bot engeli değil: güzergâh oturumu
kurulamıyor).
"""
from __future__ import annotations

import urllib.parse

KAYNAK = "qatar"
SITE = "https://www.qatarairways.com/tr-tr/homepage.html"
DESTEKLENIYOR = False
NEDEN = ("derin bağlantı `/app/booking/?...` 'Something interrupted the booking "
         "journey' veriyor; arama widget'ı otomasyonla uygunluk çağrısı "
         "tetiklemiyor (14.11.2026 ve 30.10.2026 denemeleri).")


def url_olustur(kalkis: str, varis: str, tarih: str, yolcu: int = 1) -> str:
    """Belgelenmiş (çalışmayan) derin bağlantı — rapor/bağlantı için saklanır."""
    q = urllib.parse.urlencode({
        "selLang": "en", "tripType": "O", "adults": int(yolcu),
        "children": 0, "infants": 0,
        "from": str(kalkis).upper(), "to": str(varis).upper(),
        "departureDate": tarih, "class": "E",
    })
    return "https://www.qatarairways.com/app/booking/?" + q


def ara(t, bacak, tarih: str):
    raise NotImplementedError("qatar: " + NEDEN)
