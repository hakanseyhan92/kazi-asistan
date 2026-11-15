"""vatanbilgisayar.com bağdaştırıcısı — düz HTTP.

Arama: `/arama/<sorgu>/` (boşluk %20). Kartlar `a.product-list-link` içinde;
ad `.product-list__product-name h3`, fiyat `.price-basket-camp--new-price
.product-list__price` (sepete özel fiyat). Fiyatı olmayan kart = stok dışı,
teklif üretilmez.
Ürün sayfası: JSON-LD `Product` (offers.price, availability).
"""
from __future__ import annotations

import re
import urllib.parse

from ._kart import Kart, tl_sayi
from ._ortak import http_ya_da_site_ici, json_coz, taksit_metinden

SITE = "vatan"
TABAN = "https://www.vatanbilgisayar.com"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ("input#navbar-search-input", 'input[name="search"]')
dogrudan_url_yasak = False

_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def arama_url(sorgu: str, sayfa: int = 1) -> str:
    q = urllib.parse.quote_plus(sorgu).replace("+", "%20")
    yol = f"/arama/{q}/"
    if sayfa and sayfa > 1:
        yol += f"?page={int(sayfa)}"
    return TABAN + yol


def _jsonld_nesneleri(html: str):
    for m in _JSONLD.finditer(html or ""):
        d = json_coz(m.group(1))
        if d is None:
            continue
        for n in (d if isinstance(d, list) else [d]):
            if isinstance(n, dict) and "@graph" in n:
                yield from n["@graph"]
            else:
                yield n


def _metin(n) -> str:
    return " ".join((n.text() or "").split()) if n is not None else ""


def _fiyat(n) -> float | None:
    """`.product-list__cost` içindeki ilk gerçek fiyat (indirimli varsa o)."""
    if n is None:
        return None
    for sel in (".price-basket-camp--new-price .product-list__price",
                ".product-list__price"):
        x = n.css_first(sel)
        s = _metin(x)
        if not s:
            continue
        m = re.search(r"[\d.]+(?:,\d{1,2})?", s)
        if m:
            v = tl_sayi(m.group(0))
            if v:
                return v
    return None


def teklifler(html: str, yontem: str = "http") -> list[Kart]:
    from selectolax.lexbor import LexborHTMLParser
    tree = LexborHTMLParser(html or "")
    bulunan: dict[str, Kart] = {}
    for a in tree.css("a.product-list-link"):
        url = a.attributes.get("href") or ""
        if not url:
            continue
        ad = _metin(a.css_first(".product-list__product-name h3"))
        fiyat = _fiyat(a.css_first(".product-list__cost"))
        if not ad or not fiyat:
            continue
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, yontem=yontem)
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
            break       # site içi arama tek sayfa
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
