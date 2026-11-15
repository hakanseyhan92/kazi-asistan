"""gaming.gen.tr bağdaştırıcısı — düz HTTP (WooCommerce).

Arama: `/?s=<sorgu>&post_type=product`. Kart `li.product`; ad
`h2.woocommerce-loop-product__title`, fiyat `.price ins .amount` (indirimli),
üstü çizili `del .amount`; taksit bilgisi kartta yok. `outofstock` sınıflı
kart fiyatsız gelir → atlanır.
Ürün sayfası: JSON-LD Product yok; `h1` + `.summary .price`.
"""
from __future__ import annotations

import re
import urllib.parse

from ._kart import Kart, tl_sayi
from ._ortak import http_ya_da_site_ici, taksit_metinden

SITE = "gaminggentr"
TABAN = "https://www.gaming.gen.tr"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ('input[name="s"]', '.dgwt-wcas-search-input')
dogrudan_url_yasak = False

_TAKSIT = re.compile(r"Peşin\s+fiyat[ıi]na\s*(\d+)\s*Taksit", re.I)


def arama_url(sorgu: str, sayfa: int = 1) -> str:
    p = {"s": sorgu, "post_type": "product"}
    if sayfa and sayfa > 1:
        p["paged"] = str(int(sayfa))
    return TABAN + "/?" + urllib.parse.urlencode(p, quote_via=urllib.parse.quote_plus)


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
    p = kart.css_first(".price")
    if p is None:
        return None, None
    ins = tl_sayi(_metin(p.css_first("ins .amount")))
    del_ = tl_sayi(_metin(p.css_first("del .amount")))
    miktarlar = [tl_sayi(_metin(x)) for x in p.css(".amount")]
    miktarlar = [x for x in miktarlar if x]
    satis = ins or (miktarlar[-1] if miktarlar else None)
    liste = del_
    if liste is not None and satis is not None and liste <= satis:
        liste = None
    return satis, liste


def teklifler(html: str, yontem: str = "http") -> list[Kart]:
    from selectolax.lexbor import LexborHTMLParser
    tree = LexborHTMLParser(html or "")
    bulunan: dict[str, Kart] = {}
    for kart in tree.css("li.product"):
        a = kart.css_first("h2.woocommerce-loop-product__title") or \
            kart.css_first("a.woocommerce-LoopProduct-link")
        if a is None:
            continue
        ad = _metin(a)
        url = _mutlak(a.attributes.get("href") or "")
        if not url:
            lik = kart.css_first("a.woocommerce-LoopProduct-link")
            url = _mutlak(lik.attributes.get("href") or "") if lik else ""
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
    from selectolax.lexbor import LexborHTMLParser
    tree = LexborHTMLParser(y.html or "")
    ad = _metin(tree.css_first("h1"))
    kapsam = tree.css_first(".summary") or tree
    satis, liste = _fiyatlar(kapsam)
    if not ad or not satis:
        return None
    taksit = taksit_metinden(_metin(tree))
    return Kart(site=SITE, baslik=ad, sepet_fiyati=satis, url=url, liste_fiyati=liste,
                pesin_taksit=taksit, stok=True, yontem=y.yontem)
