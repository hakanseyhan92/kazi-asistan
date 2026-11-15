"""Uçuş kaynak kaydı (SARTNAME §13, ucus/SARTNAME.md §3).

Her modül `ara(t, bacak, tarih) -> list[Segment]` sunar. Kaynaklar birbirinden
bağımsızdır; biri patlarsa çağıran (`tara.py`) hatayı yakalar ve kaydeder.
"""
from __future__ import annotations

from . import enuygun, google, kayak, qatar, skyscanner, thy, tripcom, turkmenistan

KAYNAKLAR = {
    "google": google,
    "skyscanner": skyscanner,
    "kayak": kayak,
    "enuygun": enuygun,
    "tripcom": tripcom,
    "thy": thy,
    "turkmenistan": turkmenistan,
    "qatar": qatar,
}

# Otomasyona kapalı kaynaklar: çalıştırılsa da fiyat üretmez,
# nedenleri modül başlarında ve `NEDEN` alanında belgelenmiştir.
DESTEKLENMIYOR = {
    ad: mod.NEDEN for ad, mod in KAYNAKLAR.items()
    if not getattr(mod, "DESTEKLENIYOR", True)
}

HIZLI_KAYNAKLAR = ("google", "skyscanner")


def ara(kaynak: str, t, bacak, tarih: str):
    mod = KAYNAKLAR[kaynak]
    return mod.ara(t, bacak, tarih)


__all__ = [
    "KAYNAKLAR",
    "HIZLI_KAYNAKLAR",
    "DESTEKLENMIYOR",
    "ara",
    "google",
    "skyscanner",
    "kayak",
    "enuygun",
    "tripcom",
    "thy",
    "turkmenistan",
    "qatar",
]
