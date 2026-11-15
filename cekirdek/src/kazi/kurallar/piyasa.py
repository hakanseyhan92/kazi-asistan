"""Piyasa özeti ve kovalama (SARTNAME §5): ortalamanın altındaki fiyatlar teyit için kovalanır. Saf fonksiyonlar.

14.11 kullanıcı kararı: yıldız (★/★★★) ve akıllı maliyet sistemi projeden ÇIKARILDI. Kalan tek eşik kullanıcının
kendi hedef fiyatıdır (`hedef_altinda`): teyitli fiyat hedefe eşit/altındaysa bildirim gider.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

BASARISIZ_TEYIT = {"urun_farkli", "fiyat_degisti", "stok_yok"}


# ---------------------------------------------------------------- yardımcılar
def _al(t: Any, k: str, yedek: Any = None) -> Any:
    """dict ya da nesne (pydantic Teklif) alanını oku."""
    if isinstance(t, dict):
        return t.get(k, yedek)
    return getattr(t, k, yedek)


def tl(x: float) -> str:
    """108499.4 → '108.499 TL' (Türkçe binlik ayırıcı)."""
    return f"{round(x):,}".replace(",", ".") + " TL"


def yuzde(x: float) -> str:
    """-8.63 → '−%8,6' ; 3.0 → '+%3,0'."""
    isaret = "−" if x < 0 else "+"
    return f"{isaret}%{abs(x):.1f}".replace(".", ",")


def _zaman(z: str) -> datetime:
    return datetime.fromisoformat(z)


# ---------------------------------------------------------------- ürün
@dataclass
class UrunAyar:
    kirpma_orani: float = 0.10
    pencere_saat: float = 24


def kirpilmis_ortalama(fiyatlar: Iterable[float], oran: float = 0.10) -> float | None:
    f = sorted(x for x in fiyatlar if x is not None)
    if not f:
        return None
    k = math.floor(len(f) * oran)
    if k:
        f = f[k:-k]
    return sum(f) / len(f)


def medyan(fiyatlar: Iterable[float]) -> float | None:
    f = sorted(x for x in fiyatlar if x is not None)
    if not f:
        return None
    n = len(f)
    return f[n // 2] if n % 2 else (f[n // 2 - 1] + f[n // 2]) / 2


def _gecerli_son_teklifler(teklifler, simdi: datetime, ayar: UrunAyar) -> list:
    """Pencere içi, stoku tükenmemiş (bilinmeyen stok dahil; arama listeleri stok yazmaz), ürün_farklı olmayan; her (site, satıcı) için en son gözlem."""
    sinir = simdi - timedelta(hours=ayar.pencere_saat)
    son: dict[tuple, Any] = {}
    for t in teklifler:
        if _al(t, "sepet_fiyati") is None or _al(t, "stok") is False:
            continue
        if _al(t, "teyit_durumu") == "urun_farkli":
            continue
        z = _zaman(_al(t, "gozlem_zamani"))
        if z < sinir:
            continue
        anahtar = (_al(t, "site"), _al(t, "satici"))
        if anahtar not in son or z > _zaman(_al(son[anahtar], "gozlem_zamani")):
            son[anahtar] = t
    return list(son.values())


def piyasa_ozeti(teklifler, simdi: datetime, ayar: UrunAyar | None = None) -> dict:
    ayar = ayar or UrunAyar()
    gecerli = _gecerli_son_teklifler(teklifler, simdi, ayar)
    fiyatlar = [_al(t, "sepet_fiyati") for t in gecerli]
    return {"ortalama": kirpilmis_ortalama(fiyatlar, ayar.kirpma_orani), "medyan": medyan(fiyatlar),
            "adet": len(fiyatlar)}


def kovalanacaklar(teklifler, ozet: dict) -> list:
    """Ortalamanın altında olup henüz teyit edilmemiş (ya da teyitten sonra fiyatı değişmiş) teklifler, ucuzdan pahalıya."""
    ort = ozet.get("ortalama")
    if ort is None:
        return []
    secilen = []
    for t in teklifler:
        f = _al(t, "sepet_fiyati")
        if f is None or f >= ort or _al(t, "stok") is False:
            continue
        durum = _al(t, "teyit_durumu", "bekliyor")
        teyit_fiyati = _al(t, "teyit_fiyati")
        if durum == "bekliyor" or (teyit_fiyati is not None and abs(teyit_fiyati - f) >= 0.5):
            secilen.append(t)
    return sorted(secilen, key=lambda t: _al(t, "sepet_fiyati"))


def hedef_altinda(teklif, hedef_fiyat: float | None) -> bool:
    """Teyitli ve fiyatı (teyit fiyatı varsa o) kullanıcının hedef fiyatına eşit ya da altında mı."""
    if hedef_fiyat is None or str(_al(teklif, "teyit_durumu", "")) != "teyitli":
        return False
    f = _al(teklif, "teyit_fiyati") or _al(teklif, "sepet_fiyati")
    return f is not None and f <= hedef_fiyat
