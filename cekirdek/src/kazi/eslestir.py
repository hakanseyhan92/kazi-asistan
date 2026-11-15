"""Takip listesi ve teklif/kart eşleştirme kuralları (SARTNAME §4)."""
from __future__ import annotations

import os

import json
import re
from pathlib import Path
from typing import Any

from .modeller import UrunKimligi
from .siteler._kart import Kart

ESKI_URUNLER_DOSYA = Path(os.environ.get("KAZI_URUNLER_DOSYA") or Path(os.environ.get("KAZI_ESKI_KOK") or Path.home() / "evren-asistan") / "fiyat" / "urunler.json")   # isteğe bağlı ürün kuralları

_onbellek: dict[str, list[dict[str, Any]]] = {}


def urunler_yukle(yol: str | Path | None = None) -> list[dict[str, Any]]:
    hedef = Path(yol) if yol else ESKI_URUNLER_DOSYA
    anahtar = str(hedef)
    if anahtar in _onbellek:
        return _onbellek[anahtar]
    d = json.loads(hedef.read_text(encoding="utf-8"))
    urunler = d.get("urunler", d if isinstance(d, list) else [])
    _onbellek[anahtar] = urunler
    return urunler


def urun_getir(urun_id: str, yol: str | Path | None = None) -> dict[str, Any] | None:
    for u in urunler_yukle(yol):
        if u.get("id") == urun_id:
            return u
    return None


def _rx(desen: str) -> re.Pattern[str]:
    return re.compile(desen, re.IGNORECASE)


def _kurallari_coz(kurallar: dict[str, Any] | UrunKimligi | None) -> dict[str, Any]:
    if kurallar is None:
        return {}
    if isinstance(kurallar, UrunKimligi):
        k = dict(kurallar.eslesme_kurallari or {})
        if kurallar.fiyat_bandi is not None:
            if k.get("min_fiyat") is None and kurallar.fiyat_bandi.alt is not None:
                k["min_fiyat"] = kurallar.fiyat_bandi.alt
            if k.get("max_fiyat") is None and kurallar.fiyat_bandi.ust is not None:
                k["max_fiyat"] = kurallar.fiyat_bandi.ust
        return k
    if isinstance(kurallar, dict):
        ic = kurallar.get("eslesme_kurallari")
        k = {**kurallar, **ic} if isinstance(ic, dict) else dict(kurallar)
        fb = k.get("fiyat_bandi")
        if isinstance(fb, dict):
            if k.get("min_fiyat") is None and fb.get("alt") is not None:
                k["min_fiyat"] = fb.get("alt")
            if k.get("max_fiyat") is None and fb.get("ust") is not None:
                k["max_fiyat"] = fb.get("ust")
        return k
    return {}


# ---- bileşen ürününde hazır sistem / dizüstü ilanı (15.11) ----
# Canlı veri (14.11 gpu taraması): RTX 5080 tekliflerinin 32/310'u hazır PC ya da dizüstüydü (169–260 bin TL) ve
# ortalamayı şişiriyordu. Bir bileşen, başlığında BAŞKA bileşenlerin izi varsa elenir; kendi kategorisinin izine bakılmaz
# (işlemci başlığında "Ryzen", bellek başlığında "DDR5" doğaldır). Kapatmak için kurala "sistem_ilanina_izin": true.
_IZ: dict[str, re.Pattern[str]] = {k: _rx(v) for k, v in {
    "işlemci": r"(?<![a-z])(ryzen|threadripper)(?![a-z])|core\s*(ultra|i[3579])|(?<![a-z0-9])i[3579][- ]\d{4,5}",
    "bellek": r"(?<![a-z0-9])\d{1,3}\s*gb\s*(ddr[45]|ram)(?![a-z])",
    "ddr": r"(?<![a-z0-9g])ddr[45](?![a-z])",
    "depolama": r"(?<![a-z])(ssd|nvme|hdd)(?![a-z])|(?<![a-z0-9])\d\s*tb(?![a-z])",
    "ekran kartı": r"(?<![a-z])rtx\s*\d{4}|(?<![a-z])rx\s*\d{4}(?![a-z0-9])|geforce",
    "hazır sistem": r"(?<![a-z])oem(?![a-z])|(?<![a-z])(oyuncu|oyun|gaming)\s+(bilgisayar|pc)(?!\s*kasas)"
                    r"|wqxga|freedos|ta[şs][ıi]nabilir\s+bilg|(?<![a-z0-9])1[4-8](\.\d)?\s*(\"|''|in[çc]|inch)",
}.items()}
_KATEGORI_IZLERI: dict[str, tuple[str, ...]] = {
    "gpu": ("işlemci", "bellek", "ddr", "depolama", "hazır sistem"),
    "cpu": ("ekran kartı", "bellek", "depolama", "hazır sistem"),       # "DDR5 desteği" işlemci başlığında olur
    "ram": ("işlemci", "ekran kartı", "depolama", "hazır sistem"),
    "ssd": ("işlemci", "ekran kartı", "hazır sistem"),
    "anakart": ("ekran kartı", "hazır sistem"),                          # anakartta Ryzen/DDR5/NVMe doğal
    "psu": ("işlemci", "ekran kartı", "hazır sistem"),
    "sogutucu": ("ekran kartı", "hazır sistem"),
    "kasa": ("işlemci", "ekran kartı", "hazır sistem"),
}
# Ekran kartı başlığında model ailesi adı ve en az bir kart özelliği şart: gaming.gen.tr hazır sistemleri "NOVA-5080",
# gamegaraj/sinerji sistemleri "Rubinum - RTX5080" gibi adlanıyor (15.11 verisi: 298 başlıkta yalnız bu 6+14 ilanı tutar)
_KATEGORI_SART: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {"gpu": (
    ("model ailesi adı", _rx(r"rtx|geforce|radeon")),
    ("kart özelliği (GB/GDDR/ekran kartı/OC/model kodu)",
     _rx(r"gd?d?r+\s*\d|\d+\s*gb?(?![a-z])|ekran\s*kart|(?<![a-z])vga(?![a-z])|(?<![a-z])oc(?![a-z])"
         r"|[a-z]{2,}\d*-[a-z0-9]{3,}-|\d{3,}\s*bit")),
)}


def sistem_ilani_mi(kategori: str | None, baslik: str) -> str | None:
    """Bileşen kategorisinde başlık hazır sistem/dizüstü ilanına benziyorsa gerekçe, değilse None."""
    if not kategori:
        return None
    for ad, sart in _KATEGORI_SART.get(kategori, ()):
        if not sart.search(baslik):
            return f"hazır sistem ilanı: {kategori} başlığında {ad} yok"
    for iz in _KATEGORI_IZLERI.get(kategori, ()):
        m = _IZ[iz].search(baslik)
        if m:
            return f"hazır sistem ilanı: {kategori} başlığında {iz} izi ({m.group(0)})"
    return None


def neden(kurallar: dict[str, Any] | UrunKimligi, kart: Kart | None) -> str | None:
    """Kart kurallara uymuyorsa elenme gerekçesini, uyuyorsa None döndürür.

    Kurallar (`UrunKimligi.eslesme_kurallari` sözlüğü):
    - `dahil`: başlıkta HEPSİ bulunması gereken regex desenleri
    - `haric`: başlıkta BİRİ bile bulunursa elenecek regex desenleri
    - `min_fiyat` / `alt`: alt fiyat sınırı
    - `max_fiyat` / `ust`: üst fiyat sınırı
    - `siteler`: liste ise kartın sitesi bu listede olmalı
    - `qvl`: `"bellek"` ya da `"ssd"` ise Gigabyte X870 AORUS ELITE WIFI7 QVL eşleşmesi şart
    - `kategori`: bileşen kategorisiyse (gpu, cpu, ram…) hazır sistem/dizüstü ilanı elenir (`sistem_ilani_mi`);
      `sistem_ilanina_izin: true` bunu kapatır
    """
    if kart is None:
        return "kart yok"
    baslik = getattr(kart, "baslik", None) or getattr(kart, "ad", None)
    fiyat = getattr(kart, "sepet_fiyati", None)
    if fiyat is None:
        fiyat = getattr(kart, "fiyat", None)
    if not baslik or fiyat is None:
        return "eksik kart verisi (başlık veya fiyat yok)"

    k = _kurallari_coz(kurallar)

    for desen in k.get("dahil") or []:
        if not _rx(desen).search(baslik):
            return f"zorunlu desen eksik: {desen}"

    for desen in k.get("haric") or []:
        m = _rx(desen).search(baslik)
        if m:
            return f"hariç tutulan ifade: {m.group(0)} ({desen})"

    if not k.get("sistem_ilanina_izin"):
        g = sistem_ilani_mi(k.get("kategori"), baslik)
        if g:
            return g

    mn = k.get("min_fiyat") if k.get("min_fiyat") is not None else k.get("alt")
    mx = k.get("max_fiyat") if k.get("max_fiyat") is not None else k.get("ust")
    if mn is not None and float(fiyat) < float(mn):
        return f"fiyat alt sınırın altında ({float(fiyat):g} < {float(mn):g})"
    if mx is not None and float(fiyat) > float(mx):
        return f"fiyat üst sınırın üstünde ({float(fiyat):g} > {float(mx):g})"

    siteler = k.get("siteler")
    if isinstance(siteler, list) and kart.site not in siteler:
        return f"site kapsam dışı: {kart.site}"

    tur = k.get("qvl")
    if tur:
        from .siteler.qvl import eslestir as qvl_eslestir
        if qvl_eslestir(tur, baslik) is None:
            return f"QVL dışı ({tur})"

    return None


def uygun_mu(kurallar: dict[str, Any] | UrunKimligi, kart: Kart | None) -> bool:
    """Kart bu ürün kimliği kurallarına uyuyor mu?"""
    return neden(kurallar, kart) is None


def eslesenler(kurallar: dict[str, Any] | UrunKimligi, kartlar: list[Kart]) -> list[Kart]:
    return [k for k in kartlar if uygun_mu(kurallar, k)]
