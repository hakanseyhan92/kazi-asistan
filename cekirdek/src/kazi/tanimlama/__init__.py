"""Ürün tanımlama (SARTNAME §4): metin → yapısal sorgu → katalog adayları → eşleşme kuralı.

Akış: `ayristir` (LLM'siz) → güven < 0.5 ise `Saglayici` (LLM) → `katalog_ara` (Akakçe) → onay → `kurallar_uret`.
api.py bağlantısı orkestraya aittir; bu paket ağ erişimini `getir(url) -> html` çağrısına bırakır.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from ..modeller import Aday
from .ayristir import KATEGORILER, YapisalSorgu, ayristir, katla, kucult
from .katalog import Eleme, katalog_ara, katalog_ara_ayrintili
from .kural import kurallar_uret
from .llm import GUVEN_ESIGI, EvrenSaglayici, KapaliSaglayici, Saglayici, birlestir

__all__ = [
    "KATEGORILER", "YapisalSorgu", "ayristir", "katla", "kucult", "Aday", "Eleme", "katalog_ara",
    "katalog_ara_ayrintili", "kurallar_uret", "Saglayici", "EvrenSaglayici", "KapaliSaglayici", "tanimla",
]

log = logging.getLogger("kazi.tanimlama")
_KAPALI = KapaliSaglayici()


def tanimla(metin: str, tur: str = "urun", getir: Callable[[str], str] | None = None,
            saglayici: Saglayici = _KAPALI) -> tuple[YapisalSorgu, list[Aday]]:
    """Serbest metni tanımla: (yapısal sorgu, sıralı adaylar).

    LLM YALNIZ kural tabanlı `guven < 0.5` iken çağrılır; hatası sessizce yutulur (kural sonucuyla sürer).
    Adaylar yalnız `tur == "urun"` iken ve `getir` verilmişse aranır.
    """
    sorgu = ayristir(metin, tur)
    if sorgu.guven < GUVEN_ESIGI:
        try:
            sorgu = birlestir(sorgu, saglayici.yapilandir(metin, sorgu))
        except Exception as e:                       # sağlayıcı hatası tanımlamayı düşürmesin
            log.warning("LLM sağlayıcı hatası: %s", type(e).__name__)
    adaylar: list[Aday] = []
    if getir is not None and sorgu.tur == "urun":
        adaylar = katalog_ara(sorgu, getir)
    return sorgu, adaylar
