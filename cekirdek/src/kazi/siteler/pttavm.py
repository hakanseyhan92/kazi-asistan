"""pttavm.com bağdaştırıcısı — Brave (HTTP 403 Cloudflare).

Kullanıcı kuralı §2.7: ana sayfa + arama kutusu `input[name="general-search-input"]`.
Sonuç sayfası JSON-LD `ItemList` taşır: item.name, item.url, offers.price,
offers.availability (InStock/OutOfStock). Fiyat yoksa atlanır.
"""
from __future__ import annotations

import json
import re

from ._kart import Kart, tl_sayi
from ._ortak import taksit_metinden

SITE = "pttavm"
HTTP_ARAMA = False   # arama yalnız tarayıcıdaki site içi kutudan → hızlı HTTP turuna girmez (zamanlayici/hizli.py)
TABAN = "https://www.pttavm.com"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ('input[name="general-search-input"]',)
dogrudan_url_yasak = True

_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _jsonld_listeler(html: str):
    for m in _JSONLD.finditer(html or ""):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        for n in (d if isinstance(d, list) else [d]):
            if isinstance(n, dict) and n.get("@type") == "ItemList":
                yield n


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def teklifler(html: str, yontem: str = "brave") -> list[Kart]:
    bulunan: dict[str, Kart] = {}
    for liste in _jsonld_listeler(html):
        for it in liste.get("itemListElement") or []:
            p = it.get("item") if isinstance(it, dict) else None
            if not isinstance(p, dict):
                continue
            ad = str(p.get("name") or "").strip()
            url = _mutlak(str(p.get("url") or ""))
            offers = p.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            fiyat = tl_sayi(offers.get("price"))
            if not ad or not url or not fiyat:
                continue
            av = str(offers.get("availability") or "").lower()
            if "outofstock" in av or "soldout" in av:
                continue        # stok dışı: fiyat yok say
            t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, stok=True,
                     yontem=yontem)
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
    for m in _JSONLD.finditer(h or ""):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        if not isinstance(d, dict) or d.get("@type") != "Product":
            continue
        offers = d.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        fiyat = tl_sayi(offers.get("price"))
        ad = str(d.get("name") or "").strip()
        if ad and fiyat:
            av = str(offers.get("availability") or "").lower()
            stok = None if not av else ("outofstock" not in av)
            return Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, stok=stok,
                        pesin_taksit=taksit_metinden(h), yontem="brave")
    h1 = tree.css_first("h1")
    ad = " ".join((h1.text() or "").split()) if h1 else ""
    if not ad:
        return None
    return None
