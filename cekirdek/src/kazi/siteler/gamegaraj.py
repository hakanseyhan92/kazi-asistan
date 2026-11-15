"""gamegaraj.com bağdaştırıcısı — düz HTTP.

Arama: `/arama/?q=<sorgu>` (+ `&page=N`). Kart `div[data-product-id]`;
ad/url `a.btn-product-review`, fiyat `p.text-3xl`. Stok dışı kartlarda fiyat
yerine "Yakında Stoklarda" yazar → teklif üretilmez.
Ürün sayfası: JSON-LD `Product` (offers.price, availability).
"""
from __future__ import annotations

import json
import re
import urllib.parse

from ._kart import Kart, tl_sayi
from ._ortak import http_ya_da_site_ici, taksit_metinden

SITE = "gamegaraj"
TABAN = "https://www.gamegaraj.com"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ("input#default-search", 'input[type="search"]')
dogrudan_url_yasak = False

_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def arama_url(sorgu: str, sayfa: int = 1) -> str:
    u = TABAN + "/arama/?" + urllib.parse.urlencode({"q": sorgu},
                                                    quote_via=urllib.parse.quote_plus)
    if sayfa and sayfa > 1:
        u += "&page=" + str(int(sayfa))
    return u


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


def teklifler(html: str, yontem: str = "http") -> list[Kart]:
    from selectolax.lexbor import LexborHTMLParser
    tree = LexborHTMLParser(html or "")
    bulunan: dict[str, Kart] = {}
    for kart in tree.css("div[data-product-id]"):
        a = kart.css_first("a.btn-product-review")
        if a is None:
            continue
        ad = (a.attributes.get("title") or _metin(a)).strip()
        url = _mutlak(a.attributes.get("href") or "")
        fiyat = tl_sayi(_metin(kart.css_first("p.text-3xl")))
        if not ad or not url or not fiyat:
            continue
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, stok=True, yontem=yontem)
        eski = bulunan.get(url)
        if eski is None or t.sepet_fiyati < eski.sepet_fiyati:
            bulunan[url] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    hepsi: dict[str, Kart] = {}
    for n in range(1, max(1, min(int(sayfa or 1), 3)) + 1):
        html, yontem = http_ya_da_site_ici(
            t, SITE, ANA, arama_url(sorgu, n), sorgu, kutu_secicileri=KUTU)
        for tk in teklifler(html, yontem=yontem):
            if min_f is not None and tk.sepet_fiyati < min_f:
                continue
            if max_f is not None and tk.sepet_fiyati > max_f:
                continue
            eski = hepsi.get(tk.url)
            if eski is None or tk.sepet_fiyati < eski.sepet_fiyati:
                hepsi[tk.url] = tk
        if yontem != "http":
            break
    return sorted(hepsi.values(), key=lambda t: t.sepet_fiyati)


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
