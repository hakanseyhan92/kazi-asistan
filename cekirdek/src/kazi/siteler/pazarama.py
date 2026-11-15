"""pazarama.com bağdaştırıcısı — düz HTTP (Nuxt, sunucu tarafı render).

Arama: `/arama?q=<sorgu>`. Kart `div.product-card`; ad/url `a[title]`
(`href` göreli), fiyat `.product-card__price` metninde İKİ fiyat olabilir:
"459.757,80 TL Sepette 454.757,80 TL" → efektif = sepet fiyatı (düşük olan),
liste = ilk fiyat. Ürün sayfası: JSON-LD `Product`.
"""
from __future__ import annotations

import json
import re
import urllib.parse

from ._kart import Kart, tl_sayi
from ._ortak import http_ya_da_site_ici, taksit_metinden

SITE = "pazarama"
TABAN = "https://www.pazarama.com"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ('input[data-testid="search-input"]', 'input[name="search-bar"]')
dogrudan_url_yasak = False

_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_FIYAT = re.compile(r"[\d]{1,3}(?:\.[\d]{3})*(?:,\d{1,2})?\s*(?:TL|₺)")


def arama_url(sorgu: str, sayfa: int = 1) -> str:
    p = {"q": sorgu}
    if sayfa and sayfa > 1:
        p["page"] = str(int(sayfa))
    return TABAN + "/arama?" + urllib.parse.urlencode(p, quote_via=urllib.parse.quote_plus)


def _jsonld_nesneleri(html: str):
    for m in _JSONLD.finditer(html or ""):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        for n in (d if isinstance(d, list) else [d]):
            if isinstance(n, dict) and "@graph" in n:
                yield from n["@graph"]
            else:
                yield n


def _metin(n) -> str:
    return " ".join((n.text() or "").split()) if n is not None else ""


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def _fiyatlar(kart) -> tuple[float | None, float | None]:
    p = kart.css_first(".product-card__price")
    if p is None:
        return None, None
    degerler = [v for v in (tl_sayi(x.group(0)) for x in _FIYAT.finditer(_metin(p))) if v]
    if not degerler:
        return None, None
    satis = min(degerler)              # "Sepette" fiyatı en düşüktür
    liste = max(degerler)
    if liste <= satis:
        liste = None
    return satis, liste


def teklifler(html: str, yontem: str = "http") -> list[Kart]:
    from selectolax.lexbor import LexborHTMLParser
    tree = LexborHTMLParser(html or "")
    bulunan: dict[str, Kart] = {}
    for kart in tree.css("div.product-card"):
        a = kart.css_first("a[title]")
        if a is None:
            continue
        ad = (a.attributes.get("title") or "").strip()
        url = _mutlak(a.attributes.get("href") or "")
        satis, liste = _fiyatlar(kart)
        if not ad or not url or not satis:
            continue
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=satis, url=url, liste_fiyati=liste,
                 stok=True, yontem=yontem)
        eski = bulunan.get(url)
        if eski is None or t.sepet_fiyati < eski.sepet_fiyati:
            bulunan[url] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    html, yontem = http_ya_da_site_ici(t, SITE, ANA, arama_url(sorgu), sorgu,
                                       kutu_secicileri=KUTU)
    return [tk for tk in teklifler(html, yontem=yontem)
            if (min_f is None or tk.sepet_fiyati >= min_f)
            and (max_f is None or tk.sepet_fiyati <= max_f)]


def urun(t, url: str) -> Kart | None:
    try:
        y = t.getir(url, site=SITE)
    except Exception:
        return None
    h = y.html or ""
    ld = next((d for d in _jsonld_nesneleri(h)
               if isinstance(d, dict) and d.get("@type") == "Product"), None)
    if ld is None:
        return None
    ad = str(ld.get("name") or "").strip()
    offers = ld.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    fiyat = tl_sayi(offers.get("price")) or tl_sayi(offers.get("lowPrice"))
    if not ad or not fiyat:
        return None
    av = str(offers.get("availability") or "").lower()
    stok = None if not av else ("outofstock" not in av)
    return Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, stok=stok,
                pesin_taksit=taksit_metinden(h), yontem=y.yontem)
