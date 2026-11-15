"""itopya.com bağdaştırıcısı — düz HTTP (blokta Brave site içi arama).

Arama: `/ara?bul=<sorgu>`. Kart `div.product-block`; ad `h2.truncate-text a`,
fiyat `.product-price strong` ("133.797,00 TL"), taksit "Peşin fiyatına 6 Taksit".
Ürün sayfası fiyatı `.product-details__sidebar_newprice`.
"""
from __future__ import annotations

import re
import urllib.parse

from ._kart import Kart, tl_sayi
from ._ortak import http_ya_da_site_ici, taksit_metinden

SITE = "itopya"
TABAN = "https://www.itopya.com"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ("input#searchBar",)
dogrudan_url_yasak = False

_TAKSIT = re.compile(r"Peşin\s+fiyat[ıi]na\s*(\d+)\s*Taksit", re.I)


def arama_url(sorgu: str) -> str:
    return TABAN + "/ara?bul=" + urllib.parse.quote_plus(sorgu)


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def _metin(n) -> str:
    return " ".join((n.text() or "").split()) if n is not None else ""


def _taksit(metin: str) -> int | None:
    return taksit_metinden(metin)


def teklifler(html: str, yontem: str = "http") -> list[Kart]:
    from selectolax.lexbor import LexborHTMLParser
    tree = LexborHTMLParser(html or "")
    bulunan: dict[str, Kart] = {}
    for b in tree.css("div.product-block"):
        a = b.css_first("h2.truncate-text a") or b.css_first("h1 a")
        if a is None:
            continue
        ad = (a.attributes.get("title") or _metin(a) or "").strip()
        url = _mutlak(a.attributes.get("href") or "")
        fiyat_n = b.css_first(".product-price strong")
        fiyat = tl_sayi(_metin(fiyat_n)) if fiyat_n is not None else None
        if not ad or not url or not fiyat:
            continue   # fiyat yok = stok dışı
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url,
                 pesin_taksit=_taksit(_metin(b)), stok=True, yontem=yontem)
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
    from selectolax.lexbor import LexborHTMLParser
    tree = LexborHTMLParser(y.html or "")
    h1 = tree.css_first("h1")
    ad = _metin(h1)
    fiyat = tl_sayi(_metin(tree.css_first(".product-details__sidebar_newprice")))
    if not ad or not fiyat:
        return None
    metin = _metin(tree)
    return Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url,
                pesin_taksit=_taksit(metin), stok=True, yontem=y.yontem)
