"""cimri.com bağdaştırıcısı (TOPLAYICI).

Ayrıştırma
----------
Arama: `__OCTOPUS_DATA__` JSON'undaki `search.products[]` (title, url,
topOffers[].price/merchant/installments). Model listesi = her ürünün en düşük
teklifi.
Ürün sayfası: JSON-LD `Product.offers.offers[]` — her teklifin `url` alanı
gerçek mağaza URL'si, `seller.name` "mağaza/satıcı" biçiminde.

13.11.2026: düz HTTP 403 (Cloudflare). **Arama URL'sine doğrudan gidilmez**
(kullanıcı kuralı; blok yiyor): `ara()` yalnız `Tarayici.site_ici_ara` ile
ana sayfa → site içi arama kutusu → Enter akışını kullanır (SARTNAME §2.7).
"""
from __future__ import annotations

import json
import re
import urllib.parse

from ._kart import Kart, tl_sayi
from ._ortak import BotEngeli, blok_mu

SITE = "cimri"
HTTP_ARAMA = False   # arama yalnız tarayıcıdaki site içi kutudan → hızlı HTTP turuna girmez (zamanlayici/hizli.py)
TABAN = "https://www.cimri.com"
ANA = TABAN + "/"
ANA_URL = ANA
ARAMA = TABAN + "/arama"
dogrudan_url_yasak = True

_OCTOPUS = re.compile(
    r'<script[^>]*id=["\']__OCTOPUS_DATA__["\'][^>]*>(.*?)</script>', re.S | re.I)
_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


def arama_url(sorgu: str) -> str:
    return ARAMA + "?q=" + urllib.parse.quote_plus(sorgu)


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def _jsonld_nesneleri(html: str):
    for m in _JSONLD.finditer(html or ""):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        for n in (d if isinstance(d, list) else [d]):
            yield n
            if isinstance(n, dict) and "@graph" in n:
                for g in n["@graph"]:
                    yield g


def _octopus(html: str) -> dict:
    m = _OCTOPUS.search(html or "")
    if not m:
        return {}
    try:
        return (json.loads(m.group(1)) or {}).get("props", {}).get("pageProps", {}).get("data", {}) or {}
    except Exception:
        return {}


def _taksit(teklif: dict) -> int | None:
    sayilar = []
    for i in teklif.get("installments") or []:
        try:
            n = int((i or {}).get("count"))
        except (TypeError, ValueError):
            continue
        if 2 <= n <= 24:
            sayilar.append(n)
    return max(sayilar) if sayilar else None


# ------------------------------------------------------------------ arama
def modeller(html: str, yontem: str = "http") -> list[Kart]:
    """Arama sayfası HTML'i -> model listesi (en düşük teklif fiyatıyla)."""
    d = _octopus(html)
    urunler = (d.get("search") or {}).get("products") or []
    bulunan: dict[str, Kart] = {}
    for p in urunler:
        if not isinstance(p, dict):
            continue
        if p.get("isDeletedProduct"):
            continue
        ad = str(p.get("title") or "").strip()
        url = _mutlak(str(p.get("url") or ""))
        offers = [o for o in (p.get("topOffers") or []) if isinstance(o, dict)
                  and tl_sayi(o.get("price"))]
        if not ad or not url or not offers:
            continue
        en = min(offers, key=lambda o: tl_sayi(o.get("price")))
        fiyat = tl_sayi(en.get("price"))
        if not fiyat or fiyat <= 0:
            continue
        merchant = ((en.get("merchant") or {}).get("name") or "").strip() or None
        stok = False if p.get("isAllOffersPassive") else None
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, kaynak=merchant,
                 pesin_taksit=_taksit(en), stok=stok, yontem=yontem)
        eski = bulunan.get(url)
        if eski is None or t.sepet_fiyati < eski.sepet_fiyati:
            bulunan[url] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def _getir(t, url: str) -> tuple[str, str]:
    """Önce düz HTTP; engel/403 ise Brave ana sayfasından oturumlu fetch."""
    try:
        t.hiz_sinirla(SITE)
        durum, h = t.http_getir(url, SITE)
        if durum == 200 and not blok_mu(h):
            return h, "http"
    except Exception:
        pass
    h = t.brave_fetch(ANA, url, site=SITE)
    if blok_mu(h):
        raise BotEngeli(SITE, url)
    return h, "brave_fetch"


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    """Cimri araması: YALNIZ ana sayfa + site içi arama kutusu (SARTNAME §2.7)."""
    h, _ = t.site_ici_ara(
        SITE, ANA, sorgu,
        kutu_secicileri=("input#search-input", 'input[name="q"]'),
        sonuc_bekle_secici="script#__OCTOPUS_DATA__",
    )
    hepsi = []
    for tk in modeller(h, yontem="brave"):
        if min_f is not None and tk.sepet_fiyati < min_f:
            continue
        if max_f is not None and tk.sepet_fiyati > max_f:
            continue
        hepsi.append(tk)
    return hepsi


# ------------------------------------------------------------------ teklifler
def teklifleri_ayristir(html: str, urun_url: str = "",
                        yontem: str = "http") -> list[Kart]:
    """Ürün sayfası HTML'i -> gerçek mağaza teklifleri (JSON-LD offers)."""
    ad = ""
    offers: list[dict] = []
    for d in _jsonld_nesneleri(html):
        if not isinstance(d, dict) or d.get("@type") != "Product":
            continue
        ad = str(d.get("name") or "").strip() or ad
        o = d.get("offers")
        if isinstance(o, dict):
            for x in (o.get("offers") or []):
                if isinstance(x, dict):
                    offers.append(x)
            if not o.get("offers") and o.get("url"):
                offers.append(o)
        elif isinstance(o, list):
            offers.extend(x for x in o if isinstance(x, dict))
    if not ad:
        m = _TITLE.search(html or "")
        ad = re.sub(r"\s*[-|]\s*Cimri.*$", "", m.group(1).strip(), flags=re.I) if m else ""

    bulunan: dict[tuple, Kart] = {}
    for o in offers:
        fiyat = tl_sayi(o.get("price"))
        url = str(o.get("url") or "").strip()
        if not fiyat or fiyat <= 0 or not url.startswith("http"):
            continue
        seller = (o.get("seller") or {}).get("name") if isinstance(o.get("seller"), dict) else None
        kaynak = satici = None
        if seller:
            parca = [s.strip() for s in str(seller).split("/", 1)]
            kaynak = parca[0] or None
            satici = parca[1] if len(parca) > 1 and parca[1] else None
        if not kaynak:
            try:
                host = urllib.parse.urlparse(url).hostname or ""
            except Exception:
                host = ""
            host = host.lower().removeprefix("www.")
            kaynak = host or None
        av = str(o.get("availability") or "").lower()
        stok = None
        if av:
            stok = "outofstock" not in av and "soldout" not in av
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, satici=satici,
                 stok=stok, kaynak=kaynak, yontem=yontem)
        k = (url, round(fiyat, 2))
        eski = bulunan.get(k)
        if eski is None or t.sepet_fiyati < eski.sepet_fiyati:
            bulunan[k] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def teklifler(t, urun_url: str) -> list[Kart]:
    h, yontem = _getir(t, urun_url)
    return teklifleri_ayristir(h, urun_url=urun_url, yontem=yontem)


def urun(t, url: str) -> Kart | None:
    """Ürün sayfasının en düşük teklifi (gerçek mağaza URL'siyle)."""
    try:
        tk = teklifler(t, url)
    except Exception:
        return None
    return tk[0] if tk else None
