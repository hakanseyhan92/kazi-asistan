"""incehesap.com bağdaştırıcısı — Brave (HTTP 403 Cloudflare).

Kullanıcı kuralı §2.7: ana sayfa + arama kutusu `input[name="search"]`.
Sonuç kartı `a.product[data-product]`: gömülü JSON'da `name`, `price` (0 =
stok dışı), `id`; `href` kartta. Taksit kart metninden.
"""
from __future__ import annotations

import html as html_mod
import json
import re

from ._kart import Kart, tl_sayi
from ._ortak import taksit_metinden

SITE = "incehesap"
HTTP_ARAMA = False   # arama yalnız tarayıcıdaki site içi kutudan → hızlı HTTP turuna girmez (zamanlayici/hizli.py)
TABAN = "https://www.incehesap.com"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ('input[name="search"]',)
dogrudan_url_yasak = True

_TAKSIT = re.compile(r"Peşin\s+fiyat[ıi]na\s*(\d+)\s*Taksit", re.I)


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


def teklifler(html: str, yontem: str = "brave") -> list[Kart]:
    from selectolax.lexbor import LexborHTMLParser
    tree = LexborHTMLParser(html or "")
    bulunan: dict[str, Kart] = {}
    for a in tree.css("a[data-product]"):
        try:
            d = json.loads(html_mod.unescape(a.attributes.get("data-product") or ""))
        except Exception:
            continue
        ad = str(d.get("name") or a.attributes.get("title") or "").strip()
        url = _mutlak(a.attributes.get("href") or "")
        fiyat = tl_sayi(d.get("price"))
        if not ad or not url or not fiyat:
            continue        # price=0 → stok dışı
        taksit = taksit_metinden(_metin(a))
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, pesin_taksit=taksit,
                 stok=True, yontem=yontem)
        eski = bulunan.get(url)
        if eski is None or fiyat < eski.sepet_fiyati:
            bulunan[url] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    html, _ = t.site_ici_ara(SITE, ANA, sorgu, kutu_secicileri=KUTU)
    return [tk for tk in teklifler(html, yontem="brave")
            if (min_f is None or tk.sepet_fiyati >= min_f)
            and (max_f is None or tk.sepet_fiyati <= max_f)]


def urun(t, url: str) -> Kart | None:
    try:
        h = t.brave_getir(url, site=SITE)
    except Exception:
        return None
    from selectolax.lexbor import LexborHTMLParser
    tree = LexborHTMLParser(h or "")
    h1 = tree.css_first("h1")
    ad = _metin(h1)
    fiyat = tl_sayi(_metin(tree.css_first(".product-price")) or
                    _metin(tree.css_first("[class*='product-price']")))
    if not ad or not fiyat:
        return None
    taksit = taksit_metinden(_metin(tree))
    return Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, pesin_taksit=taksit,
                yontem="brave")
