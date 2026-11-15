"""Teknosa taksit ve vade farkı ayrıştırıcısı."""

from __future__ import annotations

from typing import Any

from kazi.modeller import Taksit

from ._ortak import (
    html_tablolarini_ayristir,
    json_tablolarini_ayristir,
    metinden_pesin_taksit,
    sayfa_getir,
    tablo_satirlarini_dedup,
)


def ayristir(
    html: str = "",
    sepet_fiyati: float | None = None,
    *,
    yanitlar: list[dict[str, Any]] | None = None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """Teknosa ürün HTML'i veya ağ yanıtlarından taksit tablosunu çıkarır."""
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
    """Teknosa ürün sayfasından taksit tablosunu alır (403'te K2 Brave)."""
    sayfa_html, ag, _ = sayfa_getir(
        tarayici,
        "teknosa",
        url,
        ag_desen=r"installment|taksit|payment",
        html=html,
        yanitlar=yanitlar,
    )
    return ayristir(sayfa_html, sepet_fiyati, yanitlar=ag, teklif_id=teklif_id)
