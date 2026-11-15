"""sinerji.gen.tr bağdaştırıcısı — düz HTTP (blokta Brave site içi arama).

Sitenin kendi arama kutusunun kullandığı uç nokta `POST /Search/Autocomplete?q=`
ürün bazında doğru sonuç veriyor (Title + Url, fiyat yok). Bu yüzden `ara()`
önce autocomplete'ten ürün URL'lerini alır, sonra her ürün sayfasını (JSON-LD
`Product`) okur. Autocomplete az sonuç verdiyse slug aramasıyla kapsamı
genişletir; blokta site içi aramaya düşer (§2.7).
"""
from __future__ import annotations

import re
import urllib.parse

from ._kart import Kart, tl_sayi
from ._ortak import http_ara, http_ya_da_site_ici, json_coz, taksit_metinden

SITE = "sinerji"
TABAN = "https://www.sinerji.gen.tr"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ("input#QuickSearch",)
AUTO = TABAN + "/Search/Autocomplete?q="
dogrudan_url_yasak = False

_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_SLUG_TEMIZ = re.compile(r"[^a-z0-9]+")


def slug(sorgu: str) -> str:
    s = sorgu.lower().replace("-", " ")
    s = (s.replace("ı", "i").replace("ğ", "g").replace("ü", "u")
         .replace("ş", "s").replace("ö", "o").replace("ç", "c"))
    return _SLUG_TEMIZ.sub("-", s).strip("-")


def arama_url(sorgu: str, sayfa: int = 1) -> str:
    u = f"{TABAN}/{slug(sorgu)}-s"
    if sayfa and sayfa > 1:
        u += f"?page={int(sayfa)}"
    return u


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
    for kart in tree.css("article.product"):
        a = kart.css_first(".title a")
        if a is None:
            continue
        ad = _metin(a)
        url = _mutlak(a.attributes.get("href") or "")
        fiyat = tl_sayi(_metin(kart.css_first("span.price")))
        if not ad or not url or not fiyat:
            continue
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, stok=True, yontem=yontem)
        eski = bulunan.get(url)
        if eski is None or t.sepet_fiyati < eski.sepet_fiyati:
            bulunan[url] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def autocomplete(t, sorgu: str) -> list[dict]:
    """Sitenin kendi arama kutusunun kullandığı uç nokta (ürün bazlı, fiyatsız)."""
    durum, h = http_ara(t, SITE, AUTO + urllib.parse.quote_plus(sorgu))
    if durum != 200 or not h:
        return []
    d = json_coz(h)
    urunler = d.get("Products") if isinstance(d, dict) else None
    return [p for p in (urunler or []) if isinstance(p, dict) and p.get("Url")]


def _uygun(tk: Kart, min_f, max_f) -> bool:
    return ((min_f is None or tk.sepet_fiyati >= min_f)
            and (max_f is None or tk.sepet_fiyati <= max_f))


def _slug_sayfalari(t, sorgu, min_f, max_f, sayfa):
    hepsi: dict[str, Kart] = {}
    for n in range(1, max(1, min(int(sayfa or 1), 3)) + 1):
        html, yontem = http_ya_da_site_ici(
            t, SITE, ANA, arama_url(sorgu, n), sorgu, kutu_secicileri=KUTU)
        for tk in teklifler(html, yontem=yontem):
            if _uygun(tk, min_f, max_f):
                eski = hepsi.get(tk.url)
                if eski is None or tk.sepet_fiyati < eski.sepet_fiyati:
                    hepsi[tk.url] = tk
        if yontem != "http":
            break       # site içi arama tek sayfa döner
    return hepsi


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    oneriler = autocomplete(t, sorgu)
    bulunan: dict[str, Kart] = {}
    if oneriler:
        for p in oneriler[:8]:
            url = _mutlak(str(p.get("Url") or ""))
            if not url or url in bulunan:
                continue
            tk = urun(t, url)
            if tk is None:
                continue
            if not tk.baslik:
                tk.baslik = str(p.get("Title") or "")
            if _uygun(tk, min_f, max_f):
                bulunan[url] = tk

    if len(bulunan) < 3:
        for url, tk in _slug_sayfalari(t, sorgu, min_f, max_f, sayfa).items():
            eski = bulunan.get(url)
            if eski is None or tk.sepet_fiyati < eski.sepet_fiyati:
                bulunan[url] = tk

    return sorted(bulunan.values(), key=lambda x: x.sepet_fiyati)


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
