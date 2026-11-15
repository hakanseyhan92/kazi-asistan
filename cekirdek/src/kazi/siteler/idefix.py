"""idefix.com bağdaştırıcısı — Brave (HTTP'de JS koruması).

Kullanıcı kuralı §2.7: arama URL'sine doğrudan gidilmez. Ana sayfada görünen
arama alanı bir `<button>` (içinde "Ürün, kategori veya marka ara" yazar);
site içi arama akışı bu düğmeye basıp açılan kutuya yazmayı dener.
"""
from __future__ import annotations

import re

from ._kart import Kart, sorgu_varyantlari, tl_sayi
from ._ortak import BotEngeli, blok_mu, genel_ayristir, taksit_metinden

SITE = "idefix"
HTTP_ARAMA = False   # arama yalnız tarayıcıdaki site içi kutudan → hızlı HTTP turuna girmez (zamanlayici/hizli.py)
TABAN = "https://www.idefix.com"
ANA = TABAN + "/"
ANA_URL = ANA
# Masaüstü üst çubuk arama kutusu (15.11 canlı: <input name="headerSearch" id="headerSearch-d">)
KUTU = ("input#headerSearch-d", 'input[name="headerSearch"]')
dogrudan_url_yasak = True

_FIYAT = re.compile(r"([\d]{1,3}(?:\.[\d]{3})*,\d{2})\s*(?:TL|₺)")


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
    for a in tree.css('a[href*="-p-"]'):
        kart = a.parent
        if kart is None:
            continue
        url = _mutlak(a.attributes.get("href") or "")
        h3 = kart.css_first("h3")
        ad = (_metin(h3) or a.attributes.get("title") or _metin(a)).strip()
        fiyatlar = [tl_sayi(x) for x in _FIYAT.findall(_metin(kart))]
        fiyatlar = [x for x in fiyatlar if x]
        if not ad or not url or not fiyatlar:
            continue
        fiyat = min(fiyatlar)
        liste = max(fiyatlar) if len(fiyatlar) > 1 else None
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, stok=True,
                 liste_fiyati=liste, pesin_taksit=taksit_metinden(_metin(kart)),
                 yontem=yontem)
        eski = bulunan.get(url)
        if eski is None or fiyat < eski.sepet_fiyati:
            bulunan[url] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def urun_ayristir(html: str, url: str = "", yontem: str = "brave") -> Kart | None:
    """Ürün sayfası: JSON-LD Product/Offer (yoksa og:title + fiyat kalıbı)."""
    t = genel_ayristir(html, url=url, yontem=yontem)
    if t is not None:
        t.site = SITE
    return t


def urun(t, url: str) -> Kart | None:
    """Ürün sayfasını doğrudan aç (ürün URL'si; §2.7 kapsamı dışında)."""
    try:
        y = t.getir(url, site=SITE)
    except Exception:
        return None
    return urun_ayristir(y.html, url=url, yontem=y.yontem)


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    """Ana sayfa + site içi arama kutusu (§2.7); kutu seçicisi hafızaya yazılır."""
    toplu: dict[str, Kart] = {}
    for q in sorgu_varyantlari(sorgu):
        html, _ = t.site_ici_ara(SITE, ANA, q, kutu_secicileri=KUTU)
        if blok_mu(html):
            raise BotEngeli(SITE, ANA)
        for tk in teklifler(html, yontem="brave"):
            toplu.setdefault(tk.url, tk)
    return [tk for tk in sorted(toplu.values(), key=lambda x: x.sepet_fiyati)
            if (min_f is None or tk.sepet_fiyati >= min_f)
            and (max_f is None or tk.sepet_fiyati <= max_f)]
