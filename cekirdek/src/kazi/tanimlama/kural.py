"""Onaylanan aday(lar)dan ürün kimliği kuralı (SARTNAME §4.4).

`kurallar_uret(adaylar, sorgu) -> dict` — eski `evren-asistan/fiyat/urunler.json` ile AYNI anlamda:

- `dahil`: regex listesi, HEPSİ başlıkta bulunmalı (re.IGNORECASE);
- `haric`: regex listesi, BİRİ bulunursa teklif elenir;
- `min_fiyat` / `max_fiyat`: aday fiyatlarının 0.6× – 2.5× bandı.

Kipler:
- `mpn`: TEK aday ve MPN'si var ("Evet, bu") → çip/model + MPN regex'i (kesin ürün).
- `ozellik`: çoklu aday ("Bunların hepsi") ya da MPN'siz tek aday → sorgunun istediği + adayların ORTAK
  doğrulanabilir özellikleri (kapasite, hız, CL, watt, ... ) regex'e dönüşür.
"""
from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any

from ..modeller import Aday
from .ayristir import _MARKALAR, YapisalSorgu, katla, kucult

ALT_CARPAN = 0.6
UST_CARPAN = 2.5

# Eski urunler.json standart hariç listesi (+ "oyun bilgisayar", "haz[ıi]r pc").
STANDART_HARIC: list[str] = [
    r"laptop", r"notebook", r"diz[uü]st[uü]", r"oyuncu bilgisayar", r"oyun bilgisayar", r"gaming pc",
    r"haz[ıi]r sistem", r"haz[ıi]r pc", r"masa[uü]st[uü] bilgisayar",
    r"oem paket", r"masa[uü]st[uü] oyun",
    r"outlet", r"te[şs]hir", r"yenilenmi[şs]", r"ikinci el", r"refurb", r"kutusu hasarl",
]

KATEGORI_HARIC: dict[str, list[str]] = {
    "gpu": [r"su so[gğ]utma blo", r"waterblock", r"bracket", r"tutucu", r"backplate", r"adapt[oö]r",
            r"(?<![a-z0-9])kablo(?![a-z0-9])", r"riser"],
    "cpu": [r"anakart", r"bundle", r"\bset\b", r"kombo", r"\brtx\b"],
    "ram": [r"so-?dimm"],
    "anakart": [r"bundle", r"\bset\b", r"kombo", r"\brtx\b"],
    "ssd": [r"harici", r"external", r"portable", r"enclosure", r"kasas[ıi]"],
    "psu": [r"kasa\b", r"kablo seti", r"adapt[oö]r"],
    "kasa": [r"fan\s*(?:paketi|seti)", r"kablo seti", r"tutucu", r"\bstand\b"],
    "sogutucu": [r"fan\s*paketi", r"macun", r"bracket"],
    "monitor": [r"\bkol\b", r"\bstand\b", r"tutucu", r"ekran koruyucu"],
}

_QVL_TUR = {"ram": "bellek", "ssd": "ssd"}
_KATEGORI_AD = {"gpu": "Ekran kartı", "cpu": "İşlemci", "ram": "RAM", "anakart": "Anakart", "ssd": "SSD",
                "psu": "Güç kaynağı", "kasa": "Kasa", "sogutucu": "Soğutucu", "monitor": "Monitör"}

# adaylardan "ortak" çıkarılabilen (kullanıcı istemese de kurala giren) doğrulanabilir özellikler
_ORTAK_ANAHTARLAR = ("kit", "kapasite_gb", "hiz_mts", "cl", "watt", "80plus", "atx", "pcie", "wifi",
                     "yonga_seti", "radyator_mm")
# soket başlıkta yalnız bu kategorilerde aranır; diğerlerinde (RAM "AM5'e uyan") uyumluluk şartıdır, başlık şartı DEĞİL
_SOKET_BASLIKTA = ("anakart",)
_ESIT_MODUL = (4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256)

_TR_SINIF = {"i": "[ıi]", "ı": "[ıi]", "o": "[oö]", "ö": "[oö]", "u": "[uü]", "ü": "[uü]", "s": "[sş]", "ş": "[sş]",
             "g": "[gğ]", "ğ": "[gğ]", "c": "[cç]", "ç": "[cç]"}


# ----------------------------------------------------------------------------- regex yardımcıları
def tr_regex(kelime: str) -> str:
    """'diz üstü' benzeri serbest metni Türkçe-harf toleranslı regex'e çevir."""
    cikti = []
    for ch in kucult(kelime.strip()):
        if ch.isspace():
            cikti.append(r"\s+")
        elif ch in _TR_SINIF:
            cikti.append(_TR_SINIF[ch])
        else:
            cikti.append(re.escape(ch))
    return "".join(cikti)


_SINIR_ON = r"(?<![a-z0-9])"
_SINIR_SON = r"(?![a-z0-9])"


def esnek_kod(kod: str) -> str:
    """'G5080-16V3C' → harf/rakam kümeleri arasında boşluk/tire toleranslı, sınırlı regex."""
    kumeler = re.findall(r"[A-Za-z]+|\d+", kod)
    return _SINIR_ON + r"[\s-]?".join(re.escape(k.lower()) for k in kumeler) + _SINIR_SON


def _sayi_deseni(n: int | str) -> str:
    return rf"(?<![0-9]){n}(?![0-9])"


def _marka_deseni(marka: str) -> str | None:
    for d, ad in _MARKALAR:
        if ad == marka:
            return _SINIR_ON + f"(?:{d})" + _SINIR_SON
    return tr_regex(marka)


def _kit_deseni(n: int, m: int) -> str:
    return rf"(?<![0-9]){n}\s*x\s*{m}(?![0-9])"


# ----------------------------------------------------------------------------- özellik → regex
def _ozellik_regex(anahtar: str, deger: Any) -> tuple[list[str], list[str]]:
    """(dahil, haric) regex listeleri; boş olabilir."""
    d: list[str] = []
    h: list[str] = []
    if anahtar == "kapasite_gb":
        gb = int(deger)
        if gb >= 1000 and gb % 1000 == 0:
            tb = gb // 1000
            d.append(rf"(?<![0-9]){tb}\s*-?\s*tb|(?<![0-9]){gb}\s*gb|(?<![0-9]){tb * 1024}\s*gb")
        else:
            d.append(rf"(?<![0-9]){gb}\s*-?\s*gb")
    elif anahtar == "kit":
        pass                                         # kapasite + düzen aşağıda birlikte işlenir
    elif anahtar == "hiz_mts":
        d.append(_sayi_deseni(int(deger)))
    elif anahtar == "cl":
        cl = int(deger)
        d.append(rf"{_SINIR_ON}c(?:l)?\s*-?\s*{cl}(?![0-9])")
        h.append(rf"{_SINIR_ON}cl\s*-?\s*(?!{cl}(?![0-9]))\d{{2}}(?![0-9])")
    elif anahtar == "watt":
        d.append(_sayi_deseni(int(deger)))
    elif anahtar == "80plus":
        if deger and deger != "80+":
            d.append(rf"{_SINIR_ON}{re.escape(str(deger).lower())}{_SINIR_SON}")
    elif anahtar == "atx":
        v = str(deger)
        ana, _, ond = v.partition(".")
        if ond in ("", "0"):
            d.append(rf"{_SINIR_ON}atx\s*-?\s*{ana}(?:\.0)?(?![0-9.])")
        else:
            d.append(rf"{_SINIR_ON}atx\s*-?\s*{ana}\.{ond}(?![0-9])")
    elif anahtar == "pcie":
        ana = str(deger).split(".")[0]
        d.append(rf"(?:{_SINIR_ON}pci[\s-]*e(?:xpress)?[\s-]*(?:gen[\s-]*)?{ana}(?:\.0)?|{_SINIR_ON}gen[\s-]*{ana})(?![0-9])")
    elif anahtar == "soket":
        d.append(esnek_kod(str(deger)))
    elif anahtar == "yonga_seti":
        d.append(esnek_kod(str(deger)))
    elif anahtar == "wifi":
        v = str(deger).lower()
        if v != "var":
            d.append(rf"w[ıi][\s-]?f[ıi][\s-]*{re.escape(v)}|{_SINIR_ON}wf{re.escape(v)}")
        else:
            d.append(r"w[ıi][\s-]?f[ıi]")
    elif anahtar == "radyator_mm":
        d.append(_sayi_deseni(int(deger)))
    elif anahtar == "bellek_tipi":
        d.append(rf"(?<![a-z])ddr\s*-?\s*{re.escape(str(deger)[-1])}")
    elif anahtar == "ekran_inc":
        sayi = str(deger).replace(".0", "")
        d.append(rf"(?<![0-9]){re.escape(sayi)}(?:[.,]\d)?\s*(?:inc|inch|in\b|\"|”|inç|in[cç]lik)")
    elif anahtar == "yenileme_hz":
        d.append(rf"(?<![0-9]){int(deger)}\s*hz")
    elif anahtar == "renk":
        d.append(r"beyaz|white" if deger == "Beyaz" else r"siyah|black")
    elif anahtar == "profil":
        d.append(rf"{_SINIR_ON}{re.escape(str(deger).lower())}{_SINIR_SON}")
    elif anahtar == "form_faktor":
        v = str(deger)
        harita = {"mATX": r"m-?atx|micro\s*-?\s*atx", "Mini-ITX": r"\bitx\b|mini\s*-?\s*itx", "E-ATX": r"e-?\s?atx"}
        if v in harita:
            d.append(harita[v])
    return d, h


def _kit_regex(kit: str) -> tuple[str, list[str]]:
    """'2x32' → (dahil: toplam kapasite VEYA 2x32, haric: aynı toplamın başka düzenleri)."""
    n, m = (int(x) for x in kit.split("x"))
    toplam = n * m
    dahil = rf"(?<![0-9]){toplam}\s*-?\s*gb|{_kit_deseni(n, m)}"
    haric = [_kit_deseni(a, toplam // a) for a in (1, 2, 4, 8)
             if toplam % a == 0 and toplam // a in _ESIT_MODUL and (a, toplam // a) != (n, m)]
    return dahil, haric


def _model_regex(model: str, kategori: str | None) -> tuple[list[str], list[str]]:
    """Çip/model → (dahil, haric). GPU'da Ti/Super/XT varyantı ayrılır; CPU'da SKU esnek yazılır."""
    d: list[str] = []
    h: list[str] = []
    mm = re.fullmatch(r"(RTX|GTX|RX|ARC)\s+(\d{3,4})(?:\s+(.+))?", model, re.I)
    if mm and kategori in (None, "gpu"):
        sayi, ek = mm.group(2), (mm.group(3) or "").lower()
        if ek:
            d.append(rf"{_sayi_deseni(sayi)}[\s-]*{re.escape(ek).replace('\\ ', r'[\s-]*')}(?![a-z0-9])")
        else:
            d.append(_sayi_deseni(sayi))
            h.append(rf"{_sayi_deseni(sayi)}[\s-]*(?:ti|super|xtx|xt|gre)(?![a-z0-9])")
        return d, h
    d.append(esnek_kod(model))
    return d, h


# ----------------------------------------------------------------------------- ortak özellik
def ortak_ozellikler(adaylar: Sequence[Aday]) -> dict[str, Any]:
    """TÜM adaylarda bulunan ve eşit olan doğrulanabilir özellikler."""
    if not adaylar:
        return {}
    ilk = adaylar[0].ozellikler
    cikti: dict[str, Any] = {}
    for k in _ORTAK_ANAHTARLAR:
        if k not in ilk:
            continue
        if all(a.ozellikler.get(k) == ilk[k] for a in adaylar[1:]):
            cikti[k] = ilk[k]
    return cikti


def _ortak_deger(adaylar: Sequence[Aday], anahtar: str) -> Any:
    if not adaylar:
        return None
    ilk = adaylar[0].ozellikler.get(anahtar)
    if ilk is not None and all(a.ozellikler.get(anahtar) == ilk for a in adaylar[1:]):
        return ilk
    return None


# ----------------------------------------------------------------------------- fiyat bandı
def fiyat_bandi(adaylar: Sequence[Aday]) -> tuple[float | None, float | None]:
    alt = [a.fiyat_araligi.alt for a in adaylar if a.fiyat_araligi and a.fiyat_araligi.alt]
    ust = [(a.fiyat_araligi.ust or a.fiyat_araligi.alt) for a in adaylar
           if a.fiyat_araligi and (a.fiyat_araligi.ust or a.fiyat_araligi.alt)]
    if not alt:
        return None, None
    mn = math.floor(min(alt) * ALT_CARPAN / 100) * 100
    mx = math.ceil(max(ust) * UST_CARPAN / 100) * 100
    return float(mn), float(mx)


# ----------------------------------------------------------------------------- ad
def kanonik_ad(adaylar: Sequence[Aday], kategori: str | None, model: str | None,
               oz: dict[str, Any], marka: str | None) -> str:
    if len(adaylar) == 1:
        return adaylar[0].baslik
    parcalar = [_KATEGORI_AD.get(kategori or "", "")]
    if marka and kategori != "cpu":
        parcalar.append(marka)
    if model:
        parcalar.append(model)
    if "kit" in oz:
        parcalar.append(f"{oz.get('kapasite_gb')}GB ({oz['kit']})")
    elif "kapasite_gb" in oz:
        gb = oz["kapasite_gb"]
        parcalar.append(f"{gb // 1000}TB" if gb >= 1000 and gb % 1000 == 0 else f"{gb}GB")
    if oz.get("bellek_tipi"):
        parcalar.append(str(oz["bellek_tipi"]) + (f"-{oz['hiz_mts']}" if oz.get("hiz_mts") else ""))
    elif oz.get("hiz_mts"):
        parcalar.append(f"{oz['hiz_mts']} MT/s")
    if oz.get("cl"):
        parcalar.append(f"CL{oz['cl']}")
    if oz.get("watt"):
        parcalar.append(f"{oz['watt']}W")
    if oz.get("80plus") and oz["80plus"] != "80+":
        parcalar.append(f"80+ {oz['80plus']}")
    if oz.get("atx"):
        parcalar.append(f"ATX {oz['atx']}")
    if oz.get("yonga_seti"):
        parcalar.append(str(oz["yonga_seti"]))
    if oz.get("radyator_mm"):
        parcalar.append(f"{oz['radyator_mm']} mm")
    return " ".join(p for p in parcalar if p).strip() or "Ürün"


# ----------------------------------------------------------------------------- kalan sözcükler
_ROMEN = {"ii": r"(?:ii|2)", "iii": r"(?:iii|3)", "iv": r"(?:iv|4)", "vi": r"(?:vi|6)"}


def _kalan_kurallari(adaylar: Sequence[Aday], sorgu: YapisalSorgu) -> list[str]:
    """Kullanıcının yazdığı ama tanınmayan ürün-hattı sözcükleri (aorus, elite, lancool, iii ...):
    TÜM onaylanan adayların başlığında geçiyorsa kurala girer (aday onayı = kullanıcı bunu istiyor)."""
    if not adaylar:
        return []
    basliklar = [katla(a.baslik) for a in adaylar]
    cikti: list[str] = []
    for w in sorgu.kalan_sozcukler:
        wk = katla(w)
        if len(wk) < 2 or not re.search(r"[a-z]", wk) or re.fullmatch(r"\d+[a-z]?", wk):
            continue
        if not all(re.search(_SINIR_ON + re.escape(wk) + _SINIR_SON, b) for b in basliklar):
            continue
        cikti.append(_SINIR_ON + _ROMEN.get(wk, tr_regex(w)) + _SINIR_SON)
    return cikti


# ----------------------------------------------------------------------------- ana işlev
def _tekil(liste: list[str]) -> list[str]:
    gorulen: set[str] = set()
    return [x for x in liste if not (x in gorulen or gorulen.add(x))]


def kurallar_uret(adaylar: Sequence[Aday], sorgu: YapisalSorgu) -> dict:
    """Onaylanan aday(lar) + sorgudan eşleşme kuralı (docstring'e bakın)."""
    adaylar = list(adaylar)
    kategori = sorgu.kategori or _ortak_deger(adaylar, "kategori")
    model = sorgu.model or _ortak_deger(adaylar, "model")
    marka = sorgu.marka or None
    mpnler = [a.mpn for a in adaylar if a.mpn]

    dahil: list[str] = []
    haric: list[str] = []
    mod = "ozellik"

    if model:
        d, h = _model_regex(str(model), kategori)
        dahil += d
        haric += h

    if len(adaylar) == 1 and adaylar[0].mpn:
        mod = "mpn"
        dahil.append(esnek_kod(adaylar[0].mpn))
        oz: dict[str, Any] = {}
    else:
        oz = {**ortak_ozellikler(adaylar), **{k: v for k, v in sorgu.ozellikler.items() if v not in (None, "")}}
        if marka and kategori != "cpu":
            p = _marka_deseni(marka)
            if p:
                dahil.append(p)
        if "kit" in oz:
            d_kit, h_kit = _kit_regex(str(oz["kit"]))
            dahil.append(d_kit)
            haric += h_kit
        for k in ("kapasite_gb", "hiz_mts", "cl", "watt", "80plus", "atx", "pcie", "soket", "yonga_seti", "wifi",
                  "radyator_mm", "bellek_tipi", "ekran_inc", "yenileme_hz", "renk", "profil", "form_faktor"):
            if k not in oz:
                continue
            if k == "kapasite_gb" and "kit" in oz:
                continue                             # kit regex'i toplamı zaten kapsıyor
            if k == "kapasite_gb" and kategori == "gpu":
                continue                             # VRAM: model zaten belirler
            if k == "soket" and kategori not in _SOKET_BASLIKTA:
                continue
            d, h = _ozellik_regex(k, oz[k])
            dahil += d
            haric += h

    if mod == "ozellik":
        dahil += _kalan_kurallari(adaylar, sorgu)

    if not dahil and adaylar:                        # model/özellik yok: başlıktan ayırt edici sözcükler
        for a in adaylar[:1]:
            for w in re.findall(r"[^\W_]{4,}", a.baslik)[:3]:
                dahil.append(_SINIR_ON + tr_regex(w))

    haric = STANDART_HARIC + KATEGORI_HARIC.get(kategori or "", []) + haric
    for h in sorgu.haric:
        haric.append(next((p for p in STANDART_HARIC if re.fullmatch(p, h, re.I)), None)
                     or {"so-dimm": r"so-?dimm", "waterblock": r"waterblock|su so[gğ]utma blo",
                         "bundle": r"bundle|kombo"}.get(h) or r"\b" + tr_regex(h))

    mn, mx = fiyat_bandi(adaylar)
    kurallar: dict[str, Any] = {
        "kategori": kategori,
        "mod": mod,
        "dahil": _tekil(dahil),
        "haric": _tekil(haric),
        "min_fiyat": mn,
        "max_fiyat": mx,
        "mpn": mpnler,
        "kanonik_ad": kanonik_ad(adaylar, kategori, model, oz if mod != "mpn" else {}, marka),
        "aday_idler": [a.id for a in adaylar],
    }
    if oz.get("soket") and kategori not in _SOKET_BASLIKTA:
        kurallar.setdefault("uyumluluk", {})["soket"] = oz["soket"]
    if sorgu.ozellikler.get("qvl"):
        tur = _QVL_TUR.get(kategori or "")
        if tur:
            kurallar["qvl"] = tur                    # eski eslestir.py: qvl="bellek"|"ssd"
        else:
            kurallar.setdefault("uyumluluk", {})["qvl"] = True
    return kurallar
