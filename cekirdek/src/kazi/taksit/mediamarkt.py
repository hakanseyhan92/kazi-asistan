"""MediaMarkt Türkiye taksit ve vade farkı ayrıştırıcısı."""

from __future__ import annotations

import re
from typing import Any

from kazi.modeller import Taksit

from ._ortak import (
    html_tablolarini_ayristir,
    json_tablolarini_ayristir,
    metinden_pesin_taksit,
    satir_olustur,
    sayfa_getir,
    tablo_satirlarini_dedup,
)

_INSTALLMENT_JSON_DESEN = re.compile(
    r'\{[^{}]*"duration"\s*:\s*\d+[^{}]*"monthlyRate"\s*:[^{}]*\}',
    re.I,
)


def ayristir(
    html: str = "",
    sepet_fiyati: float | None = None,
    *,
    yanitlar: list[dict[str, Any]] | None = None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """MediaMarkt ürün HTML'i veya ağ JSON yanıtlarından taksit bilgisini çıkarır."""
    import json

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

        satirlar: list[Taksit] = []
        for m in _INSTALLMENT_JSON_DESEN.finditer(html):
            try:
                nesne = json.loads(m.group(0))
            except Exception:
                continue
            sure = nesne.get("duration")
            aylik = nesne.get("monthlyRate")
            toplam = nesne.get("totalAmount")
            faiz = nesne.get("interestTotal")
            if not isinstance(sure, int) or aylik is None or toplam is None:
                continue
            pesin_mi = faiz is None or float(faiz) == 0.0
            satir = satir_olustur(
                banka=None,
                taksit_sayisi=sure,
                aylik=float(aylik),
                toplam=float(toplam),
                pesin_fiyat=sepet_fiyati,
                teklif_id=teklif_id,
                pesin_mi=pesin_mi,
            )
            if satir:
                satirlar.append(satir)
        if satirlar:
            return tablo_satirlarini_dedup(satirlar)

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
    """MediaMarkt ürün sayfasından taksit bilgisini alır."""
    sayfa_html, ag, _ = sayfa_getir(
        tarayici,
        "mediamarkt",
        url,
        ag_desen=r"installment|financing|graphql",
        html=html,
        yanitlar=yanitlar,
    )
    return ayristir(sayfa_html, sepet_fiyati, yanitlar=ag, teklif_id=teklif_id)
