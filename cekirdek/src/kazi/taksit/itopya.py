"""İtopya taksit ve vade farkı ayrıştırıcısı."""

from __future__ import annotations

import re
from typing import Any

from kazi.modeller import Taksit
from kazi.siteler._kart import tl_sayi
from kazi.tarayici import BotEngeli

from ._ortak import (
    ay_sayisi_coz,
    banka_normalize,
    html_coz,
    http_getir_guvenli,
    metinden_pesin_taksit,
    satir_olustur,
    sayfa_getir,
    tablo_satirlarini_dedup,
)

_URL_ID_DESEN = re.compile(r"_u(\d+)(?:[/?#]|$)", re.I)
_HTML_ID_DESEN = re.compile(r"GetTaksitBilgileri[^0-9]{0,60}(\d+)", re.I)
_API_SABLON = "https://www.itopya.com/Home/GetTaksitBilgileri?id={pid}"


def urun_id_bul(url: str = "", html: str = "") -> str | None:
    """İtopya ürün URL'si veya HTML'inden ürün kimliğini çıkarır."""
    for kaynak, desen in ((url, _URL_ID_DESEN), (html, _HTML_ID_DESEN)):
        if not kaynak:
            continue
        m = desen.search(kaynak)
        if m:
            return m.group(1)
    return None


def _tablo_icin_banka(tablo_dugumu: Any) -> str | None:
    cur = tablo_dugumu
    for _ in range(4):
        if cur is None:
            break
        for img in cur.css("img"):
            src = img.attributes.get("src") or ""
            alt = img.attributes.get("alt") or ""
            dosya = src.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for aday in (alt, dosya, src):
                if "bankkart" in aday.lower():
                    return "Bankkart"
                if "turkiyefinans" in aday.lower():
                    return "Türkiye Finans"
                b = banka_normalize(aday)
                if b:
                    return b
        cur = cur.parent
    return None


def ayristir(
    html: str = "",
    sepet_fiyati: float | None = None,
    *,
    taksit_html: str | None = None,
    yanitlar: list[dict[str, Any]] | None = None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """İtopya GetTaksitBilgileri HTML'i veya ürün HTML'inden taksit satırlarını çıkarır."""
    hedef_html = taksit_html or html
    if not hedef_html:
        return []
    doc = html_coz(hedef_html)
    satirlar: list[Taksit] = []

    for tbl in doc.css("table"):
        banka = _tablo_icin_banka(tbl)
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
            toplam = tl_sayi(hucreler[2].text(strip=True))
            satir = satir_olustur(
                banka=banka,
                taksit_sayisi=ay_n,
                aylik=aylik,
                toplam=toplam,
                pesin_fiyat=sepet_fiyati,
                teklif_id=teklif_id,
            )
            if satir:
                satirlar.append(satir)

    if satirlar:
        return tablo_satirlarini_dedup(satirlar)
    return metinden_pesin_taksit(hedef_html, sepet_fiyati, teklif_id=teklif_id)


def taksit_tablosu(
    tarayici: Any,
    url: str,
    sepet_fiyati: float | None,
    *,
    html: str | None = None,
    yanitlar: list[dict[str, Any]] | None = None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """İtopya ürününün GetTaksitBilgileri uç noktasından taksit tablosunu alır."""
    pid = urun_id_bul(url, html or "")
    taksit_html: str | None = None

    if pid and tarayici is not None and hasattr(tarayici, "http_getir"):
        try:
            _durum, govde = http_getir_guvenli(tarayici, _API_SABLON.format(pid=pid), "itopya")
            if "<table" in govde:
                taksit_html = govde
        except BotEngeli:
            taksit_html = None
        except Exception:
            taksit_html = None

    if taksit_html:
        sonuc = ayristir(taksit_html=taksit_html, sepet_fiyati=sepet_fiyati, teklif_id=teklif_id)
        if sonuc:
            return sonuc

    sayfa_html, ag, _ = sayfa_getir(
        tarayici,
        "itopya",
        url,
        ag_desen=r"GetTaksitBilgileri|taksit",
        html=html,
        yanitlar=yanitlar,
    )
    if not pid:
        pid = urun_id_bul(url, sayfa_html)
        if pid and tarayici is not None and hasattr(tarayici, "http_getir"):
            try:
                _durum, govde = http_getir_guvenli(tarayici, _API_SABLON.format(pid=pid), "itopya")
                if "<table" in govde:
                    taksit_html = govde
            except Exception:
                pass
    return ayristir(sayfa_html, sepet_fiyati, taksit_html=taksit_html, yanitlar=ag, teklif_id=teklif_id)
