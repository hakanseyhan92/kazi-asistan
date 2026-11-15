"""Yan tur: ana rotadaki bir şehirden esnek tarihli gidiş-dönüş (SARTNAME §13.5).

Örnek (14.11 kullanıcı): Tokyo'dayken Ali tek başına Seul'e gider, 2 gece kalır, Tokyo'ya döner;
sonra eşiyle Tokyo→İstanbul uçar. Seyahat penceresindeki tüm gidiş/dönüş günleri denenir,
kalış süresine uyan ve ana uçuşlarla çakışmayan en ucuz çiftler bulunur.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .baglanti import BaglantiAyar, uygun_mu
from .segment import Segment


@dataclass
class YanTur:
    ana_sehir: str
    hedef_sehir: str
    yolcu_idler: list[str]
    kalis_gece: int = 2
    kalis_esneklik: int = 0           # ± gece
    en_erken_gidis: date | None = None
    en_gec_donus: date | None = None
    gidis_bacak_id: str | None = None
    donus_bacak_id: str | None = None


@dataclass
class YanTurCifti:
    gidis: Segment
    donus: Segment
    gece: int
    kisi_basi_toplam: float
    toplam_sure_dk: int
    uyarilar: list[str] = field(default_factory=list)


def _gece(gidis: Segment, donus: Segment) -> int:
    """Hedef şehirde geçen gece: hedefe varış günü → hedeften kalkış günü (yerel saatlerle)."""
    return (donus.kalkis_dt.date() - gidis.varis_dt.date()).days


def yan_tur_ciftleri(gidisler: list[tuple[Segment, list[str]]], donusler: list[tuple[Segment, list[str]]],
                     yt: YanTur, ana_varis: Segment | None = None, ana_kalkis: Segment | None = None,
                     ayar: BaglantiAyar | None = None, tablo: dict[str, int] | None = None,
                     k: int = 10) -> list[YanTurCifti]:
    """En ucuz k çift (kişi başı toplam → toplam süre). `ana_varis`: ana şehre geliş uçuşu,
    `ana_kalkis`: ana şehirden ayrılış uçuşu (verilirse çakışma ve bağlantı payı denetlenir)."""
    sonuc: list[YanTurCifti] = []
    # Dönüşleri hedeften kalkış gününe göre grupla; her gidiş için yalnız kalış gecesine uyan günler denenir.
    # (15.11: tüm gidiş×dönüş çiftleri 2,8 milyon denemeydi → /seyahat 12,9 sn, arayüz zaman aşımı.)
    # Tarihler bir kez çözülür (Segment.kalkis_dt/varis_dt her erişimde metinden çözüyor).
    gunluk: dict[date, list[tuple[Segment, list[str], datetime, datetime]]] = {}
    for d, du in donusler:
        dk, dv = d.kalkis_dt, d.varis_dt
        gunluk.setdefault(dk.date(), []).append((d, du, dk, dv))
    a_kalkis = ana_kalkis.kalkis_dt if ana_kalkis is not None else None
    for g, gu in gidisler:
        gk, gv = g.kalkis_dt, g.varis_dt
        if yt.en_erken_gidis and gk.date() < yt.en_erken_gidis:
            continue
        uy_g: list[str] = []
        if ana_varis is not None:
            if gk <= ana_varis.varis_dt:
                continue
            if gk.date() == ana_varis.varis_dt.date():
                ok, gerekli, bosluk, bu = uygun_mu(ana_varis, g, tablo, ayar)
                if not ok:
                    continue
                uy_g += bu + [f"⚠ aynı gün bağlantı (geliş → yan tur): {bosluk} dk boşluk, {gerekli} dk gerekli"]
        g_sure = g.sure_dk
        for gece in range(yt.kalis_gece - yt.kalis_esneklik, yt.kalis_gece + yt.kalis_esneklik + 1):
            for d, du, dk, dv in gunluk.get(gv.date() + timedelta(days=gece), ()):
                if dk <= gv:
                    continue
                if yt.en_gec_donus and dv.date() > yt.en_gec_donus:
                    continue
                uy = list(uy_g)
                if a_kalkis is not None:
                    if dv >= a_kalkis:
                        continue
                    if dv.date() == a_kalkis.date():
                        ok, gerekli, bosluk, bu = uygun_mu(d, ana_kalkis, tablo, ayar)
                        if not ok:
                            continue
                        uy += bu + [f"⚠ aynı gün bağlantı (yan tur → ayrılış): {bosluk} dk boşluk, {gerekli} dk gerekli"]
                sonuc.append(YanTurCifti(g, d, gece, g.kisi_basi + d.kisi_basi,
                                         g_sure + int((dv - dk).total_seconds() // 60),
                                         list(dict.fromkeys(uy + gu + du))))
    sonuc.sort(key=lambda c: (c.kisi_basi_toplam, c.toplam_sure_dk))
    return sonuc[:k]
