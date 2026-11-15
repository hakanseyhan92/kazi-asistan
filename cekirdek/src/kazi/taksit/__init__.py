"""Kart/banka bazlı kredi kartı taksit ve vade farkı kazıma paketi."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from kazi.modeller import TZ_ISTANBUL, Taksit, Teklif, TeyitDurumu, simdi_iso
from kazi.tarama.depo import baglanti_coz, son_teklifler
from kazi.tarama.teyit import _toplayici_mi, yonlendirme_coz
from kazi.tarayici import BotEngeli

from . import (
    amazon,
    hepsiburada,
    incehesap,
    itopya,
    mediamarkt,
    n11,
    teknosa,
    trendyol,
    vatan,
)
from ._ortak import mevcut_sekme_html

log = logging.getLogger(__name__)

SITE_BAGDASTIRICILARI: dict[str, Any] = {
    "hepsiburada": hepsiburada,
    "trendyol": trendyol,
    "n11": n11,
    "amazon": amazon,
    "vatan": vatan,
    "itopya": itopya,
    "incehesap": incehesap,
    "teknosa": teknosa,
    "mediamarkt": mediamarkt,
}

_ALAN_SITE: tuple[tuple[str, str], ...] = (
    ("hepsiburada.com", "hepsiburada"),
    ("trendyol.com", "trendyol"),
    ("n11.com", "n11"),
    ("amazon.com.tr", "amazon"),
    ("vatanbilgisayar.com", "vatan"),
    ("itopya.com", "itopya"),
    ("incehesap.com", "incehesap"),
    ("teknosa.com", "teknosa"),
    ("mediamarkt.com.tr", "mediamarkt"),
)

SON_ENGEL_NEDENLERI: dict[str, str] = {}


def site_sec(url: str) -> tuple[str | None, Any]:
    """Gerçek mağaza alan adından site kimliği ve bağdaştırıcı modülünü seçer."""
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for alan, site in _ALAN_SITE:
        if host == alan or host.endswith("." + alan):
            return site, SITE_BAGDASTIRICILARI.get(site)
    return None, None


class _K0YalnizTarayici:
    """Yalnız-K0 kipinde yonlendirme_coz'ün Brave sekmesi açmasını engeller."""

    def __init__(self, asil: Any) -> None:
        self._asil = asil
        self.yalniz_k0 = True

    def http_getir(self, *args: Any, **kwargs: Any) -> Any:
        return self._asil.http_getir(*args, **kwargs)

    def _sekme(self, site: str) -> Any:
        raise BotEngeli(site, f"yalnız K0 kipinde tarayıcı açılmadı ({site})")


def _teklif_alanlari(teklif: Teklif | dict[str, Any] | str) -> tuple[int, str, float | None]:
    if isinstance(teklif, str):
        return 0, teklif, None
    if isinstance(teklif, dict):
        url = teklif.get("teyit_url") or teklif.get("url") or ""
        fiyat = teklif.get("sepet_fiyati")
        if fiyat is None:
            fiyat = teklif.get("fiyat")
        return int(teklif.get("id") or 0), str(url), fiyat
    url = getattr(teklif, "teyit_url", None) or getattr(teklif, "url", "") or ""
    fiyat = getattr(teklif, "sepet_fiyati", None)
    if fiyat is None:
        fiyat = getattr(teklif, "fiyat", None)
    return int(getattr(teklif, "id", None) or 0), str(url), fiyat


def tablo_getir(
    tarayici: Any,
    teklif: Teklif | dict[str, Any] | str,
    *,
    sepet_fiyati: float | None = None,
    html: str | None = None,
    yanitlar: list[dict[str, Any]] | None = None,
    nedenler: dict[str, str] | None = None,
) -> list[Taksit]:
    """Teklif URL'sinden gerçek mağaza taksit tablosunu çıkarır.

    Toplayıcı bağlantıyı ``teyit.yonlendirme_coz`` ile çözer; desteklenmeyen sitede
    veya ``BotEngeli`` durumunda hata yükseltmeden boş liste döner ve nedeni kaydeder.
    """
    teklif_id, ham_url, teklif_fiyat = _teklif_alanlari(teklif)
    fiyat = sepet_fiyati if sepet_fiyati is not None else teklif_fiyat
    if not ham_url:
        return []

    gercek_url = ham_url
    if _toplayici_mi(ham_url) and tarayici is not None:
        coz_tarayici = (
            _K0YalnizTarayici(tarayici)
            if getattr(tarayici, "yalniz_k0", False)
            else tarayici
        )
        try:
            gercek_url = yonlendirme_coz(coz_tarayici, ham_url)
        except BotEngeli as exc:
            neden = f"bot_engeli: yönlendirme çözülemedi ({exc.url})"
            SON_ENGEL_NEDENLERI[exc.site] = neden
            if nedenler is not None:
                nedenler[exc.site] = neden
            return []
        except Exception as exc:
            neden = f"yönlendirme çözülemedi: {exc}"
            site_ad = str(getattr(teklif, "site", "") or "toplayici")
            SON_ENGEL_NEDENLERI[site_ad] = neden
            if nedenler is not None:
                nedenler[site_ad] = neden
            log.debug("Yönlendirme çözme hatası (%s): %s", ham_url, exc)
            return []

    site, modul = site_sec(gercek_url)
    if not site or modul is None:
        return []

    try:
        return modul.taksit_tablosu(
            tarayici,
            gercek_url,
            fiyat,
            html=html,
            yanitlar=yanitlar,
            teklif_id=teklif_id,
        )
    except BotEngeli as exc:
        neden = f"bot_engeli: {exc.site} ({exc.url})"
        SON_ENGEL_NEDENLERI[exc.site] = neden
        if nedenler is not None:
            nedenler[exc.site] = neden
        log.warning("Taksit kazımada bot engeli (%s): %s", exc.site, exc.url)
        return []
    except Exception as exc:
        log.warning("Taksit kazıma hatası (%s - %s): %s", site, gercek_url, exc)
        return []


def kaydet(v: Any, teklif_id: int, satirlar: list[Taksit]) -> int:
    """Teklifin eski banka satırlarını silip yenilerini yazar; banka=None satırını korur."""
    bag, kilit, _ = baglanti_coz(v)
    eklenen = 0
    with kilit:
        mevcut_pesin_aylar = {
            int(r[0])
            for r in bag.execute(
                "SELECT taksit_sayisi FROM taksit WHERE teklif_id = ? AND banka IS NULL",
                (teklif_id,),
            ).fetchall()
        }
        bag.execute(
            "DELETE FROM taksit WHERE teklif_id = ? AND banka IS NOT NULL",
            (teklif_id,),
        )
        for s in satirlar:
            if s.banka is None and s.taksit_sayisi in mevcut_pesin_aylar:
                continue
            if s.banka is None:
                mevcut_pesin_aylar.add(s.taksit_sayisi)
            bag.execute(
                "INSERT INTO taksit(teklif_id, banka, taksit_sayisi, aylik, toplam, vade_farki_yuzde) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    teklif_id,
                    s.banka,
                    s.taksit_sayisi,
                    s.aylik,
                    s.toplam,
                    s.vade_farki_yuzde,
                ),
            )
            eklenen += 1
        bag.commit()
    return eklenen


def teyitten_kaydet(
    v: Any,
    tarayici: Any,
    teklif: Teklif,
    gercek_url: str,
    sepet_fiyati: float,
) -> list[Taksit]:
    """Teyit sayfası okunduktan sonra ek sayfa açmadan (mümkünse açık sekmeden) taksit çıkarır."""
    site, modul = site_sec(gercek_url)
    if not site or modul is None or getattr(teklif, "id", None) is None:
        return []

    acik_html = mevcut_sekme_html(tarayici, gercek_url)
    gecici_teklif = SimpleNamespace(
        id=teklif.id,
        url=gercek_url,
        teyit_url=gercek_url,
        fiyat=sepet_fiyati,
    )
    if acik_html:
        satirlar = modul.ayristir(acik_html, sepet_fiyati, teklif_id=int(teklif.id))
        if not satirlar:
            satirlar = tablo_getir(
                tarayici,
                gecici_teklif,
                sepet_fiyati=sepet_fiyati,
                html=acik_html,
            )
    elif hasattr(tarayici, "http_getir"):
        # Açık tarayıcı sekmesi yoksa yalnız K0 (ek tarayıcı sayfası açmadan) dene.
        eski_bayrak = getattr(tarayici, "yalniz_k0", None)
        try:
            if eski_bayrak is None:
                setattr(tarayici, "yalniz_k0", True)
            satirlar = tablo_getir(tarayici, gecici_teklif, sepet_fiyati=sepet_fiyati)
        finally:
            if eski_bayrak is None and hasattr(tarayici, "yalniz_k0"):
                delattr(tarayici, "yalniz_k0")
    else:
        return []

    if satirlar:
        kaydet(v, int(teklif.id), satirlar)
    return satirlar


def _onbellek_hazirla(v: Any) -> None:
    bag, kilit, _ = baglanti_coz(v)
    with kilit:
        bag.execute(
            """
            CREATE TABLE IF NOT EXISTS taksit_onbellek (
                url TEXT PRIMARY KEY,
                son_zaman TEXT NOT NULL,
                durum TEXT NOT NULL,
                satir_sayisi INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        bag.commit()


def _onbellek_gecerli_mi(v: Any, url: str, onbellek_saat: float) -> bool:
    _onbellek_hazirla(v)
    bag, kilit, _ = baglanti_coz(v)
    with kilit:
        satir = bag.execute(
            "SELECT son_zaman FROM taksit_onbellek WHERE url = ?",
            (url,),
        ).fetchone()
    if not satir:
        return False
    try:
        onceki = datetime.fromisoformat(str(satir[0]))
        if onceki.tzinfo is None:
            onceki = onceki.replace(tzinfo=TZ_ISTANBUL)
        return datetime.now(TZ_ISTANBUL) - onceki < timedelta(hours=onbellek_saat)
    except ValueError:
        return False


def _onbellek_yaz(v: Any, url: str, durum: str, satir_sayisi: int) -> None:
    _onbellek_hazirla(v)
    bag, kilit, _ = baglanti_coz(v)
    with kilit:
        bag.execute(
            """
            INSERT INTO taksit_onbellek(url, son_zaman, durum, satir_sayisi)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                son_zaman = excluded.son_zaman,
                durum = excluded.durum,
                satir_sayisi = excluded.satir_sayisi
            """,
            (url, simdi_iso(), durum, satir_sayisi),
        )
        bag.commit()


def urun_taksitlerini_cek(
    v: Any,
    tarayici: Any,
    urun_id: str,
    *,
    azami: int = 5,
    zorla: bool = False,
    onbellek_saat: float = 12.0,
) -> list[dict[str, Any]]:
    """Ürünün en ucuz geçerli teklifleri için taksit tablolarını çeker (12 saat önbellekli)."""
    teklifler = [
        t
        for t in son_teklifler(v, urun_id)
        if t.teyit_durumu not in (TeyitDurumu.urun_farkli, TeyitDurumu.stok_yok)
        and (t.sepet_fiyati or 0) > 0
    ]
    teklifler.sort(key=lambda t: float(t.sepet_fiyati or 0))
    secilenler = teklifler[:azami]

    rapor: list[dict[str, Any]] = []
    for t in secilenler:
        hedef_url = t.teyit_url or t.url
        nedenler: dict[str, str] = {}
        if not zorla and _onbellek_gecerli_mi(v, hedef_url, onbellek_saat):
            mevcut = _teklif_taksitlerini_oku(v, int(t.id or 0))
            rapor.append(
                {
                    "teklif": t,
                    "url": hedef_url,
                    "satirlar": mevcut,
                    "durum": "onbellek",
                    "neden": None,
                }
            )
            continue

        satirlar = tablo_getir(tarayici, t, nedenler=nedenler)
        durum = "bulundu" if satirlar else ("engellendi" if nedenler else "yok")
        if satirlar and t.id is not None:
            kaydet(v, int(t.id), satirlar)
        _onbellek_yaz(v, hedef_url, durum, len(satirlar))
        rapor.append(
            {
                "teklif": t,
                "url": hedef_url,
                "satirlar": satirlar,
                "durum": durum,
                "neden": next(iter(nedenler.values()), None),
            }
        )
    return rapor


def _teklif_taksitlerini_oku(v: Any, teklif_id: int) -> list[Taksit]:
    if not teklif_id:
        return []
    bag, kilit, _ = baglanti_coz(v)
    with kilit:
        satirlar = bag.execute(
            "SELECT id, teklif_id, banka, taksit_sayisi, aylik, toplam, vade_farki_yuzde "
            "FROM taksit WHERE teklif_id = ? ORDER BY banka IS NULL, banka, taksit_sayisi",
            (teklif_id,),
        ).fetchall()
    return [Taksit(**dict(s)) for s in satirlar]


__all__ = [
    "SITE_BAGDASTIRICILARI",
    "SON_ENGEL_NEDENLERI",
    "kaydet",
    "site_sec",
    "tablo_getir",
    "teyitten_kaydet",
    "urun_taksitlerini_cek",
]
