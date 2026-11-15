"""trendyol.com bağdaştırıcısı.

Ayrıştırma
----------
Arama sayfası (`/sr?q=`) sunucu tarafında render edilir; ürün listesi
`window["__single-search-result__PROPS"]` JSON'unda hazırdır.

Fiyat: `price.discountedPrice` = satış fiyatı, `price.current`/`originalPrice`
üstü çizili liste fiyatıdır; ikisinden büyük olan `liste_fiyati` olur.
Taksit: `tagDetails[].displayName` içindeki "Peşin Fiyatına 9 Taksit".

Kullanıcı kuralı §2.7: arama URL'sine doğrudan gidilmez; ana sayfa + arama
kutusu (kutu `button[data-testid="suggestion-placeholder"]` ile açılır).
Ürün sayfası doğrudan açılabilir (JSON-LD `Product`).
"""
from __future__ import annotations

import json
import re

from selectolax.lexbor import LexborHTMLParser

from ._json_yakala import JsonYakalayici, gomulu_durum
from ._kart import Kart, tl_sayi

SITE = "trendyol"
HTTP_ARAMA = False   # arama yalnız tarayıcıdaki site içi kutudan → hızlı HTTP turuna girmez (zamanlayici/hizli.py)
TABAN = "https://www.trendyol.com"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ("button[data-testid='suggestion-placeholder']",)
# Ana sayfada "Kadın / Erkek" seçim penceresi arama kutusunu kapatıyor → ✕ ile kapat (15.11, kullanıcı)
POPUP_KAPAT = (".modal-section-close",)
dogrudan_url_yasak = True

_PROPS = re.compile(r'window\["__single-search-result__PROPS"\]\s*=\s*')
_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_TAKSIT = re.compile(r"Peşin Fiyat[ıi]na\s*(\d+)\s*Taksit", re.I)
_MERCHANT = re.compile(r'"merchantName"\s*:\s*"([^"]{2,80})"')


def _json_kes(html: str, ad: str):
    """`window["<ad>"] = {...}` atamasını dengeli biçimde çöz."""
    m = re.search(r'window\["' + re.escape(ad) + r'"\]\s*=\s*', html or "")
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(html, m.end())[0]
    except Exception:
        return None


def _jsonld_nesneleri(html: str):
    for m in _JSONLD.finditer(html or ""):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        for n in (d if isinstance(d, list) else [d]):
            yield n


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def _taksit(etiketler) -> int | None:
    sayilar = []
    for t in etiketler or []:
        if not isinstance(t, dict):
            continue
        for a in _TAKSIT.findall(str(t.get("displayName") or "")):
            try:
                n = int(a)
            except ValueError:
                continue
            if 2 <= n <= 24:
                sayilar.append(n)
    return max(sayilar) if sayilar else None


def _fiyatlar(p: dict) -> tuple[float | None, float | None]:
    pr = p.get("price") or {}
    satis = tl_sayi(pr.get("discountedPrice"))
    if not satis:
        satis = tl_sayi(pr.get("current"))
    adaylar = []
    for k in ("current", "originalPrice"):
        v = tl_sayi(pr.get(k))
        if v:
            adaylar.append(v)
    liste = max(adaylar) if adaylar else None
    if liste is not None and satis is not None and liste <= satis:
        liste = None
    return satis, liste


def json_kartlari(veri, yontem: str = "brave") -> list[Kart]:
    """`__single-search-result__PROPS` (ya da yalnız `{"data": {...}}`/`{"products": [...]}`) -> Kart.

    Alanlar: name, url (göreli -> mutlak), price.discountedPrice (alıcının gördüğü fiyat),
    price.current/originalPrice (liste), tagDetails (peşin fiyatına taksit). Satıcı listede
    yalnız `merchantId` olarak gelir (ad yok). Bozuk veri -> [].
    """
    try:
        d = veri if isinstance(veri, dict) else {}
        ic = d.get("data") if isinstance(d.get("data"), dict) else d
        urunler = ic.get("products") or []
        if not isinstance(urunler, list):
            return []
        bulunan: dict[str, Kart] = {}
        for p in urunler:
            if not isinstance(p, dict):
                continue
            ad = str(p.get("name") or "").strip()
            url = _mutlak(str(p.get("url") or "").split("#")[0])
            fiyat, liste = _fiyatlar(p)
            if not ad or not url or not fiyat or fiyat <= 0:
                continue
            t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, liste_fiyati=liste,
                     pesin_taksit=_taksit(p.get("tagDetails")), stok=None, yontem=yontem)
            eski = bulunan.get(url)
            if eski is None or t.sepet_fiyati < eski.sepet_fiyati:
                bulunan[url] = t
        return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)
    except Exception:
        return []


def teklifler_ayristir(html: str, yontem: str = "brave") -> list[Kart]:
    """Arama sonuç HTML'i -> teklifler (gömülü arama JSON'undan)."""
    return json_kartlari(_json_kes(html, "__single-search-result__PROPS") or {}, yontem)


teklifler = teklifler_ayristir


_JS_DURUM = ('js:window["__single-search-result__PROPS"]',)


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    """Trendyol araması: YALNIZ ana sayfa + site içi arama kutusu (§2.7).

    Kartlar sayfanın gömülü JSON durumundan (canlı sayfa), olmazsa dönen HTML'deki
    aynı JSON'dan okunur.
    """
    try:
        yak = JsonYakalayici(t._sekme(SITE), (r"search-feed/products|search/products",))
    except Exception:
        yak = None
    try:
        html, _ = t.site_ici_ara(SITE, ANA, sorgu, kutu_secicileri=KUTU)
        kartlar: list[Kart] = []
        try:
            kartlar = json_kartlari(gomulu_durum(t._sekme(SITE), _JS_DURUM))
        except Exception:
            kartlar = []
        if not kartlar:
            kartlar = teklifler_ayristir(html, yontem="brave")
    finally:
        if yak is not None:
            yak.kapat()
    hepsi = []
    for tk in kartlar:
        if min_f is not None and tk.sepet_fiyati < min_f:
            continue
        if max_f is not None and tk.sepet_fiyati > max_f:
            continue
        hepsi.append(tk)
    return hepsi


def urun(t, url: str) -> Kart | None:
    """Ürün sayfası: JSON-LD Product fiyatı + taksit/satıcı metinden."""
    try:
        y = t.getir(url, site=SITE)
    except Exception:
        return None
    h = y.html or ""
    if not h:
        return None
    ld = next((d for d in _jsonld_nesneleri(h)
               if isinstance(d, dict) and d.get("@type") == "Product"), None)
    ad = str((ld or {}).get("name") or "").strip()
    offers = (ld or {}).get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    fiyat = tl_sayi(offers.get("price"))
    if fiyat is None:
        return None
    if not ad:
        m = re.search(r"<title>(.*?)</title>", h, re.S | re.I)
        ad = (m.group(1).strip() if m else "").split("|")[0].strip()
    stok = None
    av = str(offers.get("availability") or "").lower()
    if av:
        stok = "outofstock" not in av
    metin = " ".join((LexborHTMLParser(h).text(separator=" ") or "").split())
    taksit = _taksit([{"displayName": mm.group(0)}
                      for mm in _TAKSIT.finditer(metin)])
    sm = _MERCHANT.search(h)
    return Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url,
                satici=(sm.group(1) if sm else None),
                pesin_taksit=taksit, stok=stok, yontem=y.yontem)
