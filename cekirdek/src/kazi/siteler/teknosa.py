"""teknosa.com bağdaştırıcısı — Brave (HTTP 403 Cloudflare).

Kullanıcı kuralı §2.7: arama URL'sine doğrudan gidilmez; ana sayfa + arama
kutusu `input#search-input`. Sonuç kartları sunucu tarafında render edilir:
`.prd-title` başlık, `.prd-prc2 .prc` fiyat ("152.069 TL"), `a.prd-link` URL,
`.sale-text` = "Peşin fiyatına 6 x 25.344,83 TL" → taksit.
"""
from __future__ import annotations

import re

from ._kart import Kart, tl_sayi
from ._ortak import taksit_metinden

SITE = "teknosa"
HTTP_ARAMA = False   # arama yalnız tarayıcıdaki site içi kutudan → hızlı HTTP turuna girmez (zamanlayici/hizli.py)
TABAN = "https://www.teknosa.com"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ("input#search-input", 'input[name="s"]')
dogrudan_url_yasak = True

_TAKSIT = re.compile(r"Peşin\s+fiyat[ıi]na\s*(\d+)\s*x", re.I)


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
    for bas in tree.css(".prd-title"):
        kart = bas
        for _ in range(6):
            if kart.parent is None:
                break
            kart = kart.parent
            if kart.css_first("a.prd-link") is not None:
                break
        link = kart.css_first("a.prd-link")
        url = _mutlak(link.attributes.get("href") or "") if link else ""
        ad = _metin(bas)
        prc = kart.css_first(".prd-prc2 .prc") or kart.css_first(".prd-prices .prc")
        fiyat = tl_sayi(_metin(prc))
        if not ad or not url or not fiyat:
            continue        # fiyat yok = stok dışı
        taksit = taksit_metinden(_metin(kart.css_first(".sale-text") or kart))
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
    kart = tree.css_first("body")
    fiyat = tl_sayi(_metin(tree.css_first(".prd-prc2 .prc"))) or \
        tl_sayi(_metin(tree.css_first(".prc-last")))
    if not ad or not fiyat:
        return None
    taksit = taksit_metinden(_metin(kart))
    return Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, pesin_taksit=taksit,
                yontem="brave")
