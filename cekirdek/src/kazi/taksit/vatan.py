"""Vatan Bilgisayar taksit ve vade farkı ayrıştırıcısı."""

from __future__ import annotations

import re
from typing import Any

from kazi.modeller import Taksit
from kazi.siteler._kart import tl_sayi

from ._ortak import (
    ay_sayisi_coz,
    html_coz,
    html_tablolarini_ayristir,
    metinden_pesin_taksit,
    satir_olustur,
    sayfa_getir,
    tablo_satirlarini_dedup,
)

# Doğrudan Vatan görsellerinden/sayfa altlığından doğrulanmış data-cbid -> kart eşlemesi.
# 6 = Havale, 12 = Visa/MasterCard Tek Çekim -> atlanır.
_CBID_BANKA: dict[str, str] = {
    "1": "QNB",
    "2": "Axess",
    "3": "Maximum",
    "4": "Bonus",
    "8": "World",
    "18": "Bankkart",
    "19": "Paraf",
    "29": "Kuveyt Türk",
}
_X_TUTAR_DESEN = re.compile(r"(\d{1,2})\s*x\s*([\d.,]+)", re.I)


def ayristir(
    html: str = "",
    sepet_fiyati: float | None = None,
    *,
    yanitlar: list[dict[str, Any]] | None = None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """Vatan ürün sayfası HTML'inden #cart_tab_content taksit tablolarını ayrıştırır."""
    if not html:
        return []
    doc = html_coz(html)
    satirlar: list[Taksit] = []

    for oge in doc.css("#cart_tab_content .installment-tab-item"):
        cbid = (oge.attributes.get("data-cbid") or "").strip()
        banka = _CBID_BANKA.get(cbid)
        if not banka:
            continue
        for tr in oge.css("tbody tr, tr"):
            hucreler = tr.css("td")
            if len(hucreler) < 3:
                continue
            rozet = hucreler[0].text(strip=True).lower()
            taksit_hucre = hucreler[1].text(strip=True)
            toplam_hucre = hucreler[2].text(strip=True)

            taksit_n: int | None = None
            aylik: float | None = None
            m = _X_TUTAR_DESEN.search(taksit_hucre)
            if m:
                taksit_n = int(m.group(1))
                aylik = tl_sayi(m.group(2))
            else:
                taksit_n = ay_sayisi_coz(taksit_hucre)
            toplam = tl_sayi(toplam_hucre)
            if not taksit_n:
                continue
            pesin_mi = "peşin" in rozet or "farksız" in rozet
            satir = satir_olustur(
                banka=banka,
                taksit_sayisi=taksit_n,
                aylik=aylik,
                toplam=toplam,
                pesin_fiyat=sepet_fiyati,
                teklif_id=teklif_id,
                pesin_mi=pesin_mi or None,
            )
            if satir:
                satirlar.append(satir)

    if satirlar:
        return tablo_satirlarini_dedup(satirlar)

    genel = html_tablolarini_ayristir(html, sepet_fiyati, teklif_id=teklif_id)
    if genel:
        return genel
    return metinden_pesin_taksit(html, sepet_fiyati, teklif_id=teklif_id)


def taksit_tablosu(
    tarayici: Any,
    url: str,
    sepet_fiyati: float | None,
    *,
    html: str | None = None,
    yanitlar: list[dict[str, Any]] | None = None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """Vatan ürün sayfasından kredi kartı taksit tablosunu alır."""
    sayfa_html, ag, _ = sayfa_getir(
        tarayici,
        "vatan",
        url,
        ag_desen=r"installment|taksit",
        html=html,
        yanitlar=yanitlar,
    )
    return ayristir(sayfa_html, sepet_fiyati, yanitlar=ag, teklif_id=teklif_id)
