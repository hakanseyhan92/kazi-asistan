"""Amazon Türkiye taksit ve vade farkı ayrıştırıcısı."""

from __future__ import annotations

from typing import Any

from kazi.modeller import Taksit
from kazi.siteler._kart import tl_sayi

from ._ortak import (
    ay_sayisi_coz,
    banka_normalize,
    html_coz,
    html_tablolarini_ayristir,
    json_tablolarini_ayristir,
    metinden_pesin_taksit,
    satir_olustur,
    sayfa_getir,
    tablo_satirlarini_dedup,
)

_TABLO_BANKA_ON_EK = "dp-calculator-table-view-"


def ayristir(
    html: str = "",
    sepet_fiyati: float | None = None,
    *,
    yanitlar: list[dict[str, Any]] | None = None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """Amazon TR ürün HTML'indeki taksit hesaplayıcı tablolarını ayrıştırır."""
    satirlar: list[Taksit] = []
    if html:
        doc = html_coz(html)
        for tbl in doc.css(f'table[id^="{_TABLO_BANKA_ON_EK}"]'):
            tid = tbl.attributes.get("id") or ""
            kart_kod = tid[len(_TABLO_BANKA_ON_EK):]
            banka = banka_normalize(kart_kod)
            if not banka:
                continue
            for tr in tbl.css("tr"):
                hucreler = tr.css("td")
                if len(hucreler) < 3:
                    continue
                ay_n = ay_sayisi_coz(hucreler[0].text(strip=True))
                if not ay_n:
                    continue
                aylik = tl_sayi(hucreler[1].text(strip=True))
                if len(hucreler) >= 4:
                    vade_metin = hucreler[2].text(strip=True).lower()
                    toplam = tl_sayi(hucreler[3].text(strip=True))
                    pesin_mi = "ücretsiz" in vade_metin or "ucretsiz" in vade_metin or "peşin" in vade_metin
                else:
                    toplam = tl_sayi(hucreler[2].text(strip=True))
                    pesin_mi = None
                satir = satir_olustur(
                    banka=banka,
                    taksit_sayisi=ay_n,
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

    for y in yanitlar or []:
        j = y.get("json")
        if j is not None:
            bulunan = json_tablolarini_ayristir(j, sepet_fiyati, teklif_id=teklif_id)
            if bulunan:
                return bulunan

    if html:
        genel = html_tablolarini_ayristir(html, sepet_fiyati, teklif_id=teklif_id)
        if genel:
            return genel
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
    """Amazon TR ürün sayfasından taksit tablosunu alır (gerekirse K2 Brave)."""
    sayfa_html, ag, _ = sayfa_getir(
        tarayici,
        "amazon",
        url,
        ag_desen=r"installment|calculator|financing",
        html=html,
        yanitlar=yanitlar,
    )
    return ayristir(sayfa_html, sepet_fiyati, yanitlar=ag, teklif_id=teklif_id)
