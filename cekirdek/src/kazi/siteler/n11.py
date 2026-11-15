"""n11.com bağdaştırıcısı.

Notlar (13.11.2026): düz HTTP çalışıyor. Arama sayfasında JSON-LD `ItemList` var;
`offers.price` = SEPET/ödenecek fiyat, `priceSpecification.price` = liste fiyatı.
Ürün sayfası: `nickName` satıcı, `stock` stok adedi, `INSTALLMENT_N` taksit rozetleri.
"""
from __future__ import annotations

import json
import re
import urllib.parse

from ._kart import Kart, tl_sayi

SITE = "n11"
TABAN = "https://www.n11.com"
ANA = TABAN + "/"
ANA_URL = ANA
ARAMA = TABAN + "/arama"
dogrudan_url_yasak = False

_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_NICK = re.compile(r'"nickName"\s*:\s*"([^"]*)"')
_PRICE = re.compile(r'"price"\s*:\s*"([\d.,]+(?:\s*TL)?)"')
_DISPLAY = re.compile(r'"displayPrice"\s*:\s*"([\d.,]+(?:\s*TL)?)"')
_STOCK = re.compile(r'"stock"\s*:\s*(\d+)')
_INSTALLMENT = re.compile(r"INSTALLMENT_(\d+)")
_LD_PRICE = re.compile(r'"price"\s*:\s*"?([\d.,]+(?:\s*TL)?)"?')
_LD_SKU = re.compile(r'"sku"\s*:\s*"([^"]*)"')


def arama_url(sorgu: str, min_f=None, max_f=None, sayfa: int = 1) -> str:
    p = {"q": sorgu, "srt": "PRICE_LOW"}
    if min_f is not None:
        p["minp"] = str(int(min_f))
    if max_f is not None:
        p["maxp"] = str(int(max_f))
    if sayfa and sayfa > 1:
        p["pg"] = str(sayfa)
    return ARAMA + "?" + urllib.parse.urlencode(p, quote_via=urllib.parse.quote_plus)


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def _jsonld_nesneleri(html: str):
    for m in _JSONLD.finditer(html):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        for n in (d if isinstance(d, list) else [d]):
            yield n
            if isinstance(n, dict) and "@graph" in n:
                for g in n["@graph"]:
                    yield g


def _offers(o):
    if isinstance(o, list):
        return o[0] if o else {}
    return o or {}


def _fiyat(deger) -> float | None:
    if deger is None:
        return None
    if isinstance(deger, dict):
        deger = deger.get("price") or deger.get("value")
    try:
        return float(deger)
    except (TypeError, ValueError):
        return tl_sayi(deger)


def teklifler(html: str, yontem: str = "http") -> list[Kart]:
    """Arama sayfası HTML'inden JSON-LD ItemList teklifleri."""
    bulunan: dict[str, Kart] = {}
    for d in _jsonld_nesneleri(html):
        if not isinstance(d, dict) or d.get("@type") != "ItemList":
            continue
        for it in d.get("itemListElement") or []:
            p = it.get("item") if isinstance(it, dict) else None
            if not isinstance(p, dict):
                continue
            ad = (p.get("name") or "").strip()
            o = _offers(p.get("offers"))
            fiyat = _fiyat(o.get("price"))
            url = _mutlak(p.get("url") or o.get("url") or "")
            if not ad or fiyat is None or fiyat <= 0 or not url:
                continue
            liste = _fiyat(
                (o.get("priceSpecification") or {}).get("price")
                if isinstance(o.get("priceSpecification"), dict) else None
            )
            if liste is not None and liste <= fiyat:
                liste = None
            stok_metni = (o.get("availability") or "").lower()
            stok = None
            if stok_metni:
                stok = "outofstock" not in stok_metni and "soldout" not in stok_metni
            t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, liste_fiyati=liste,
                     stok=stok, yontem=yontem)
            eski = bulunan.get(url)
            if eski is None or t.sepet_fiyati < eski.sepet_fiyati:
                bulunan[url] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    """n11 araması; sayfa başına en çok 3 sayfa gezilir (§0)."""
    hepsi: dict[str, Kart] = {}
    for n in range(1, max(1, min(int(sayfa or 1), 3)) + 1):
        url = arama_url(sorgu, min_f, max_f, n)
        try:
            y = t.getir(url, site=SITE)
        except Exception:
            if n == 1:
                raise
            break
        for tk in teklifler(y.html, yontem=y.yontem):
            if min_f is not None and tk.sepet_fiyati < min_f:
                continue
            if max_f is not None and tk.sepet_fiyati > max_f:
                continue
            eski = hepsi.get(tk.url)
            if eski is None or tk.sepet_fiyati < eski.sepet_fiyati:
                hepsi[tk.url] = tk
        if not y.html:
            break
    return sorted(hepsi.values(), key=lambda t: t.sepet_fiyati)


def urun(t, url: str) -> Kart | None:
    """Ürün sayfası: sepet fiyatı, liste, satıcı, stok, vade farksız taksit."""
    try:
        y = t.getir(url, site=SITE)
    except Exception:
        return None
    h = y.html or ""
    if not h:
        return None

    ad = ""
    ld = next((d for d in _jsonld_nesneleri(h)
               if isinstance(d, dict) and d.get("@type") in ("Product", ("Product",))), None)
    if ld:
        ad = (ld.get("name") or "").strip()
    if not ad:
        m = _TITLE.search(h)
        ad = m.group(1).strip() if m else ""
    ad = re.sub(r"\s*[-|]\s*n11\.com.*$", "", ad, flags=re.I).strip()

    fiyat = None
    if ld:
        o = _offers(ld.get("offers"))
        fiyat = _fiyat(o.get("price"))
    if fiyat is None:
        m = _PRICE.search(h)
        fiyat = tl_sayi(m.group(1)) if m else None
    if fiyat is None:
        return None

    lm = _DISPLAY.search(h)
    liste = tl_sayi(lm.group(1)) if lm else None
    if liste is not None and liste <= fiyat:
        liste = None

    sm = _STOCK.search(h)
    stok = None
    if sm:
        try:
            stok = int(sm.group(1)) > 0
        except ValueError:
            stok = None

    nm = _NICK.search(h)
    satici = nm.group(1).strip() if nm and nm.group(1).strip() else None

    taksit = None
    sayilar = sorted({int(x) for x in _INSTALLMENT.findall(h)})
    sayilar = [x for x in sayilar if 2 <= x <= 12]
    if sayilar:
        taksit = max(sayilar)

    return Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, liste_fiyati=liste,
                satici=satici, pesin_taksit=taksit, stok=stok, yontem=y.yontem)
