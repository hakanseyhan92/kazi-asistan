"""amazon.com.tr bağdaştırıcısı.

Ayrıştırma
----------
Arama sayfası sunucu tarafında render edilir; kartlar
`div[data-component-type="s-search-result"]`. Her kartta:
  h2 (aria-label / span)  -> ürün adı ("Sponsorlu Reklam - " temizlenir)
  a[href*="/dp/"]         -> ürün URL'si (sponsorlu /sspa/ yönlendirmesi atlanır)
  .a-price .a-offscreen   -> fiyat
  .a-text-price           -> üstü çizili "Önceki" fiyat
Atlananlar: `pd_rhf` (son bakılanlar) kartları ve "Normalden yüksek fiyat"
uyarısı taşıyan kartlar. Aynı ASIN birden çok kartta (sponsorlu + organik)
geçebilir → ASIN'e göre tekilleştir, /dp/ bağlantısını yeğle.

Kullanıcı kuralı §2.7: arama URL'sine doğrudan gidilmez; ana sayfa +
`input#twotabsearchtextbox`.
"""
from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser

from ._kart import Kart, sorgu_varyantlari, tl_sayi

SITE = "amazon"
HTTP_ARAMA = False   # arama yalnız tarayıcıdaki site içi kutudan → hızlı HTTP turuna girmez (zamanlayici/hizli.py)
TABAN = "https://www.amazon.com.tr"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ("input#twotabsearchtextbox",)
dogrudan_url_yasak = True

_KART = 'div[data-component-type="s-search-result"]'
_TAKSIT = re.compile(r"Peşin fiyat[ıi]na\s*(\d+)\s*taksit", re.I)
_STOKSUZ = ("stokta yok", "şu anda mevcut değil", "mevcut değil", "tükendi")
_SPONSOR = re.compile(r"^\s*(sponsorlu(?:\s+reklam)?\s*[-–]\s*)", re.I)


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def _ad(c) -> str:
    h2 = c.css_first("h2")
    if h2 is None:
        return ""
    ad = " ".join((h2.attributes.get("aria-label") or h2.text() or "").split())
    return _SPONSOR.sub("", ad).strip()


def _href(c) -> str:
    for a in c.css('a[href*="/dp/"]'):
        h = a.attributes.get("href") or ""
        if h:
            return _mutlak(h.split("/ref=")[0])
    a = c.css_first("a.a-link-normal")
    return _mutlak((a.attributes.get("href") or "") if a else "")


def teklifler_ayristir(html: str, yontem: str = "brave") -> list[Kart]:
    tree = LexborHTMLParser(html or "")
    bulunan: dict[str, Kart] = {}
    for c in tree.css(_KART):
        siniflar = (c.attributes.get("class") or "") + " " + (c.attributes.get("data-component-type") or "")
        if "pd_rhf" in siniflar or c.css_first(".pd_rhf") is not None:
            continue
        metin = " ".join((c.text() or "").split())
        if "normalden yüksek fiyat" in metin.lower():
            continue
        ad = _ad(c)
        url = _href(c)
        if not ad or not url or "/dp/" not in url:
            continue
        f = c.css_first(".a-price .a-offscreen") or c.css_first(".a-price")
        fiyat = tl_sayi(f.text()) if f is not None else None
        if not fiyat or fiyat <= 0:
            continue
        liste = None
        lp = c.css_first(".a-text-price .a-offscreen") or c.css_first(".a-text-price")
        if lp is not None:
            v = tl_sayi(lp.text())
            if v and v > fiyat:
                liste = v
        m = re.search(r"/dp/([A-Z0-9]{10})", url)
        anahtar = m.group(1) if m else url
        stok = False if any(s in metin.lower() for s in _STOKSUZ) else None
        tk = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, liste_fiyati=liste,
                  pesin_taksit=None, stok=stok, yontem=yontem)
        eski = bulunan.get(anahtar)
        # organik /dp/ bağlantısı sponsorlu /sspa/ yerine yeğlenir
        if eski is None or (("/sspa/" in eski.url) and ("/dp/" in url)) \
                or (tk.sepet_fiyati < eski.sepet_fiyati and "/sspa/" not in url):
            bulunan[anahtar] = tk
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


teklifler = teklifler_ayristir


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    """Amazon araması: ana sayfa + site içi arama kutusu (§2.7)."""
    bulunan: dict[str, Kart] = {}
    for q in sorgu_varyantlari(sorgu):
        html, _ = t.site_ici_ara(SITE, ANA, q, kutu_secicileri=KUTU)
        for tk in teklifler_ayristir(html, yontem="brave"):
            bulunan.setdefault(tk.url, tk)
    hepsi = []
    for tk in sorted(bulunan.values(), key=lambda x: x.sepet_fiyati):
        if min_f is not None and tk.sepet_fiyati < min_f:
            continue
        if max_f is not None and tk.sepet_fiyati > max_f:
            continue
        hepsi.append(tk)
    return hepsi


def urun(t, url: str) -> Kart | None:
    """Ürün sayfası: #productTitle, çekirdek fiyat, satıcı, stok."""
    try:
        y = t.getir(url, site=SITE)
    except Exception:
        return None
    tree = LexborHTMLParser(y.html or "")
    baslik = tree.css_first("#productTitle")
    ad = " ".join((baslik.text() or "").split()) if baslik else ""
    if not ad:
        return None
    f = (tree.css_first("#corePrice_feature_div .a-price .a-offscreen")
         or tree.css_first("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen")
         or tree.css_first(".a-price .a-offscreen"))
    fiyat = tl_sayi(f.text()) if f is not None else None
    if not fiyat:
        return None
    liste = None
    lp = tree.css_first("#corePrice_feature_div .a-text-price .a-offscreen")
    if lp is not None:
        v = tl_sayi(lp.text())
        if v and v > fiyat:
            liste = v
    satici = None
    s = tree.css_first("#sellerProfileTriggerId") or tree.css_first("#merchant-info")
    if s is not None:
        metin = " ".join((s.text() or "").split())
        if metin:
            satici = metin[:120]
    stok = None
    av = tree.css_first("#availability")
    if av is not None:
        am = " ".join((av.text() or "").split()).lower()
        if am:
            stok = not any(x in am for x in _STOKSUZ)
    metin = " ".join((tree.text(separator=" ") or "").split())
    tm = _TAKSIT.search(metin)
    taksit = int(tm.group(1)) if tm and 2 <= int(tm.group(1)) <= 24 else None
    return Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, liste_fiyati=liste,
                satici=satici, pesin_taksit=taksit, stok=stok, yontem=y.yontem)
