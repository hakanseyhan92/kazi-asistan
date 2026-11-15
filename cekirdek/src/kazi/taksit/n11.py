"""n11 taksit ve vade farkı ayrıştırıcısı."""

from __future__ import annotations

import json
import re
from typing import Any

from kazi.modeller import Taksit
from kazi.siteler._kart import tl_sayi
from kazi.tarayici import BotEngeli

from ._ortak import (
    banka_normalize,
    html_tablolarini_ayristir,
    http_getir_guvenli,
    metinden_pesin_taksit,
    pesin_taksit_satirlari,
    satir_olustur,
    sayfa_getir,
    tablo_satirlarini_dedup,
)

_URL_ID_DESEN = re.compile(r"(?:-p-|productId=)(\d+)", re.I)
_HTML_ID_DESEN = re.compile(r'"productId"\s*:\s*"?(\d+)"?', re.I)
_INSTALLMENT_ROZET_DESEN = re.compile(r"INSTALLMENT_(\d{1,2})\b")
_API_SABLON = "https://www.n11.com/rest/v1/getProductInstallmentDetail?productId={pid}"


def urun_id_bul(url: str = "", html: str = "") -> str | None:
    """n11 URL veya ürün sayfası HTML'inden productId değerini çıkarır."""
    for kaynak, desen in ((url, _URL_ID_DESEN), (html, _HTML_ID_DESEN)):
        if not kaynak:
            continue
        m = desen.search(kaynak)
        if m:
            return m.group(1)
    return None


def _api_json_ayristir(
    veri: dict[str, Any],
    sepet_fiyati: float | None,
    teklif_id: int = 0,
) -> list[Taksit]:
    kok = veri.get("response") if isinstance(veri.get("response"), dict) else veri
    planlar = kok.get("creditCardPaymentPlans") or []
    satirlar: list[Taksit] = []
    for plan in planlar:
        if not isinstance(plan, dict):
            continue
        banka = banka_normalize(plan.get("name"))
        if not banka:
            continue
        for odeme in plan.get("installmentPayments") or []:
            if not isinstance(odeme, dict):
                continue
            ay = odeme.get("months")
            if not isinstance(ay, int) or ay < 2:
                continue
            aylik = _sayi_coz(odeme.get("installmentAmount"))
            toplam = _sayi_coz(odeme.get("totalAmount"))
            has_interest = odeme.get("hasInterestRate")
            pesin_mi = (not has_interest) if isinstance(has_interest, bool) else None
            satir = satir_olustur(
                banka=banka,
                taksit_sayisi=ay,
                aylik=aylik,
                toplam=toplam,
                pesin_fiyat=sepet_fiyati,
                teklif_id=teklif_id,
                pesin_mi=pesin_mi,
            )
            if satir:
                satirlar.append(satir)

    if satirlar:
        return tablo_satirlarini_dedup(satirlar)

    ic_html = kok.get("html")
    if isinstance(ic_html, str) and ic_html:
        return html_tablolarini_ayristir(ic_html, sepet_fiyati, teklif_id=teklif_id)
    return []


def _sayi_coz(deger: Any) -> float | None:
    if isinstance(deger, (int, float)):
        return float(deger)
    if isinstance(deger, str):
        return tl_sayi(deger)
    return None


def ayristir(
    html: str = "",
    sepet_fiyati: float | None = None,
    *,
    yanitlar: list[dict[str, Any]] | None = None,
    taksit_json: dict[str, Any] | None = None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """n11 taksit JSON yanıtı veya ürün HTML'inden taksit satırlarını çıkarır."""
    if isinstance(taksit_json, dict):
        sonuc = _api_json_ayristir(taksit_json, sepet_fiyati, teklif_id=teklif_id)
        if sonuc:
            return sonuc

    for y in yanitlar or []:
        j = y.get("json")
        if isinstance(j, dict):
            sonuc = _api_json_ayristir(j, sepet_fiyati, teklif_id=teklif_id)
            if sonuc:
                return sonuc

    if html:
        tablo = html_tablolarini_ayristir(html, sepet_fiyati, teklif_id=teklif_id)
        if tablo:
            return tablo
        m = _INSTALLMENT_ROZET_DESEN.search(html)
        if m:
            pesin = pesin_taksit_satirlari(int(m.group(1)), sepet_fiyati, teklif_id=teklif_id)
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
    """n11 ürün sayfası ve getProductInstallmentDetail API'sinden taksit tablosunu alır."""
    if html is not None or yanitlar:
        mevcut = ayristir(html or "", sepet_fiyati, yanitlar=yanitlar, teklif_id=teklif_id)
        if mevcut:
            return mevcut

    pid = urun_id_bul(url, html or "")
    taksit_json: dict[str, Any] | None = None

    if pid and tarayici is not None and hasattr(tarayici, "http_getir"):
        api_url = _API_SABLON.format(pid=pid)
        try:
            _durum, govde = http_getir_guvenli(tarayici, api_url, "n11")
            taksit_json = json.loads(govde)
        except BotEngeli:
            pass
        except Exception:
            taksit_json = None

    if taksit_json:
        sonuc = ayristir(taksit_json=taksit_json, sepet_fiyati=sepet_fiyati, teklif_id=teklif_id)
        if sonuc:
            return sonuc

    sayfa_html, ag_yanitlari, _ = sayfa_getir(
        tarayici,
        "n11",
        url,
        ag_desen=r"getProductInstallmentDetail|installment",
        html=html,
        yanitlar=yanitlar,
    )
    if not pid:
        pid = urun_id_bul(url, sayfa_html)
        if pid and tarayici is not None and hasattr(tarayici, "http_getir"):
            try:
                _durum, govde = http_getir_guvenli(tarayici, _API_SABLON.format(pid=pid), "n11")
                taksit_json = json.loads(govde)
            except Exception:
                taksit_json = None

    return ayristir(
        sayfa_html,
        sepet_fiyati,
        yanitlar=ag_yanitlari,
        taksit_json=taksit_json,
        teklif_id=teklif_id,
    )
