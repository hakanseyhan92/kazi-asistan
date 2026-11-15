"""Hepsiburada taksit ve vade farkı ayrıştırıcısı."""

from __future__ import annotations

import re
from typing import Any

from kazi.modeller import Taksit

from ._ortak import (
    html_coz,
    html_tablolarini_ayristir,
    json_tablolarini_ayristir,
    metinden_pesin_taksit,
    pesin_taksit_satirlari,
    sayfa_getir,
    tablo_satirlarini_dedup,
)

_PESIN_DESEN = re.compile(r"Peşin\s*fiyatına\s*(\d{1,2})\s*(?:x\b|taksit|ay)", re.I)


def ayristir(
    html: str = "",
    sepet_fiyati: float | None = None,
    *,
    yanitlar: list[dict[str, Any]] | None = None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """Hepsiburada ağ JSON yanıtları veya ürün HTML'inden taksit bilgisini çıkarır."""
    for y in yanitlar or []:
        j = y.get("json")
        if j is not None:
            bulunan = json_tablolarini_ayristir(j, sepet_fiyati, teklif_id=teklif_id)
            if bulunan:
                return tablo_satirlarini_dedup(bulunan)

    if html:
        tablo = html_tablolarini_ayristir(html, sepet_fiyati, teklif_id=teklif_id)
        if tablo:
            return tablo

        doc = html_coz(html)
        for sec in ('[data-test-id="payment-options"]', ".payment-options"):
            for dugum in doc.css(sec):
                metin = dugum.text(separator=" ", strip=True)
                m = _PESIN_DESEN.search(metin)
                if m:
                    pesin = pesin_taksit_satirlari(
                        int(m.group(1)),
                        sepet_fiyati,
                        teklif_id=teklif_id,
                    )
                    if pesin:
                        return pesin

        return metinden_pesin_taksit(html, sepet_fiyati, teklif_id=teklif_id)
    return []


def taksit_tablosu(
    tarayici: Any,
    url: str,
    sepet_fiyati: float | None,
    *,
    html: str | None = None,
    yanitlar: list[dict[str, Any]] | None = None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """Hepsiburada ürün sayfasından taksit tablosunu alır (gerekirse K2 Brave)."""
    sayfa_html, ag, _ = sayfa_getir(
        tarayici,
        "hepsiburada",
        url,
        ag_desen=r"installment|payment|sardes|taksit",
        html=html,
        yanitlar=yanitlar,
    )
    return ayristir(sayfa_html, sepet_fiyati, yanitlar=ag, teklif_id=teklif_id)
