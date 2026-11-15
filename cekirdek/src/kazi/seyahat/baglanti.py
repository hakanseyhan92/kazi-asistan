"""Bacaklar arası bağlantı payı (SARTNAME §13.3).

gerekli = önceki aracın gecikme payı + şehir içi transfer (varış noktası → sonraki kalkış noktası)
          + sonraki aracın kabul süresi + (ayrı bilet ise ek pay)
"""
from __future__ import annotations

from dataclasses import dataclass

from .segment import Segment, veri

VARSAYILAN_TRANSFER_DK = 120


@dataclass
class BaglantiAyar:
    kabul_dis_hat: int = 180
    kabul_ic_hat: int = 90
    kabul_yht: int = 30
    kabul_otobus: int = 30
    gecikme_dis_hat: int = 60
    gecikme_ic_hat: int = 45
    gecikme_yht: int = 30
    gecikme_otobus: int = 60
    ayri_bilet_ek: int = 60
    gece_baslangic: int = 0   # saat
    gece_bitis: int = 5


def transfer_tablosu() -> dict[str, int]:
    return dict(veri("transfer.json")["sure_dk"])


def transfer_dk(a: str, b: str, tablo: dict[str, int] | None = None) -> tuple[int, bool]:
    """(dakika, tabloda var mı). Aynı nokta → 0."""
    if a == b:
        return 0, True
    tablo = tablo if tablo is not None else transfer_tablosu()
    for anahtar in (f"{a}|{b}", f"{b}|{a}"):
        if anahtar in tablo:
            return tablo[anahtar], True
    return VARSAYILAN_TRANSFER_DK, False


def _kabul(s: Segment, ay: BaglantiAyar) -> int:
    if s.arac == "ucak":
        return ay.kabul_dis_hat if s.dis_hat else ay.kabul_ic_hat
    return ay.kabul_yht if s.arac == "yht" else ay.kabul_otobus


def _gecikme(s: Segment, ay: BaglantiAyar) -> int:
    if s.arac == "ucak":
        return ay.gecikme_dis_hat if s.dis_hat else ay.gecikme_ic_hat
    return ay.gecikme_yht if s.arac == "yht" else ay.gecikme_otobus


def gerekli_sure_dk(onceki: Segment, sonraki: Segment, tablo: dict[str, int] | None = None,
                    ayar: BaglantiAyar | None = None, ayri_bilet: bool = True) -> tuple[int, list[str]]:
    """Bağlantı için gereken dakika ve uyarılar. Farklı segmentler varsayılan olarak ayrı bilettir."""
    ay = ayar or BaglantiAyar()
    tr, bilinen = transfer_dk(onceki.varis_nokta, sonraki.kalkis_nokta, tablo)
    uyarilar = [] if bilinen else [f"⚠ {onceki.varis_nokta} → {sonraki.kalkis_nokta} transfer süresi bilinmiyor ({tr} dk varsayıldı)"]
    toplam = _gecikme(onceki, ay) + tr + _kabul(sonraki, ay) + (ay.ayri_bilet_ek if ayri_bilet else 0)
    return toplam, uyarilar


def uygun_mu(onceki: Segment, sonraki: Segment, tablo: dict[str, int] | None = None,
             ayar: BaglantiAyar | None = None, ayri_bilet: bool = True) -> tuple[bool, int, int, list[str]]:
    """(uygun, gerekli_dk, boşluk_dk, uyarılar). Boşluk = sonraki kalkış − önceki varış."""
    ay = ayar or BaglantiAyar()
    gerekli, uyarilar = gerekli_sure_dk(onceki, sonraki, tablo, ay, ayri_bilet)
    bosluk = int((sonraki.kalkis_dt - onceki.varis_dt).total_seconds() // 60)
    if onceki.varis_nokta != sonraki.kalkis_nokta:
        # transfer, gecikme payından sonra başlar
        bas = (onceki.varis_dt.hour * 60 + onceki.varis_dt.minute + _gecikme(onceki, ay)) % (24 * 60)
        if ay.gece_baslangic * 60 <= bas < ay.gece_bitis * 60:
            uyarilar.append("⚠ gece (00–05) şehir içi transfer")
    return bosluk >= gerekli, gerekli, bosluk, uyarilar
