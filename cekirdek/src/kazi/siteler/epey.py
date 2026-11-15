"""epey.com bağdaştırıcısı (TOPLAYICI).

Ayrıştırma
----------
Arama sayfası (`/ara/?ara=<sorgu>`): ürün satırları `<ul class="metin row">`
içinde. Ürün adı/linki `a.urunadi`, fiyat ise `<li class="fiyat cell">`
içindeki bağlantıda ("169.999,00 TL<span>1 site, 2 fiyat</span>").
JSON-LD yok; DOM + metin ayrıştırma kullanılır.
"""
from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta

from ._kart import Kart, sorgu_varyantlari, tl_sayi

SITE = "epey"
TABAN = "https://www.epey.com"
ANA = TABAN + "/"
ANA_URL = ANA
ARAMA = TABAN + "/ara/"
dogrudan_url_yasak = False

_SATIR = re.compile(
    r'<a href="(https?://www\.epey\.com/[^"]+)" class="urunadi" title="([^"]+)">'
    r'.*?<li class="fiyat cell">\s*<a [^>]*>\s*([\d.,]+)\s*TL'
    r'(?:\s*<span>([^<]*)</span>)?',
    re.S,
)
_SITE_SAYI = re.compile(r"(\d+)\s*site", re.I)


def arama_url(sorgu: str) -> str:
    return ARAMA + "?ara=" + urllib.parse.quote_plus(sorgu)


def teklifler(html: str, yontem: str = "http") -> list[Kart]:
    """Arama sayfası HTML'i -> model listesi (fiyata göre artan)."""
    bulunan: dict[str, Kart] = {}
    for url, ad, fiyat_m, site_m in _SATIR.findall(html or ""):
        ad = ad.strip()
        fiyat = tl_sayi(fiyat_m)
        if not ad or not fiyat or fiyat <= 0:
            continue
        stok = None
        m = _SITE_SAYI.search(site_m or "")
        if m:
            try:
                stok = int(m.group(1)) > 0
            except ValueError:
                stok = None
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, satici=None,
                 stok=stok, yontem=yontem)
        eski = bulunan.get(url)
        if eski is None or t.sepet_fiyati < eski.sepet_fiyati:
            bulunan[url] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def modeller(html: str, yontem: str = "http") -> list[Kart]:
    """`teklifler` ile aynı (ad uyumu için)."""
    return teklifler(html, yontem=yontem)


# ---------------------------------------------------------------- teklifler
_OFFER = re.compile(
    r'<a[^>]*class="git[^"]*"[^>]*data-link="([^"]+)"[^>]*title="([^"]*)"'
    r'(.*?)</a>', re.S | re.I)
_FIYAT = re.compile(r'urun_fiyat"?[^>]*>\s*([\d]{1,3}(?:\.[\d]{3})*,\d{2})\s*TL',
                    re.S | re.I)
_SATICI = re.compile(r'Satıcı:\s*</strong>\s*([^<|]+)', re.I)
_ONCE = re.compile(r'(\d+)\s*(dk|dakika|saat|gün|gun)\s*önce', re.I)
_STOKSUZ = ("tükendi", "stokta yok", "satışta değil")


def _once_iso(metin: str, simdi: datetime | None = None) -> str | None:
    m = _ONCE.search(metin or "")
    if not m:
        return None
    n = int(m.group(1))
    birim = m.group(2).lower()
    if birim in ("dk", "dakika"):
        delta = timedelta(minutes=n)
    elif birim == "saat":
        delta = timedelta(hours=n)
    else:
        delta = timedelta(days=n)
    return ((simdi or datetime.now()) - delta).isoformat(timespec="seconds")


def teklifleri_ayristir(html: str, model_url: str = "",
                        yontem: str = "brave") -> list[Kart]:
    """Model sayfası HTML'i -> gerçek mağaza teklifleri (data-link çözülür)."""
    bulunan: dict[tuple, Kart] = {}
    for link, baslik, blok in _OFFER.findall(html or ""):
        url = urllib.parse.unquote(link).strip()
        if not url.startswith("http"):
            continue
        m = _FIYAT.search(blok)
        fiyat = tl_sayi(m.group(1)) if m else None
        if not fiyat or fiyat <= 0:
            continue
        magaza = (baslik.split(" ", 1)[0] if baslik else "").strip() or None
        ms = _SATICI.search(blok)
        satici = ms.group(1).strip() if ms else None
        ad = baslik
        if magaza and ad.lower().startswith(magaza.lower()):
            ad = ad[len(magaza):].strip()
        ad = re.sub(r"\s*fiyatı\s*$", "", ad, flags=re.I).strip() or baslik
        stok = False if any(s in blok.lower() for s in _STOKSUZ) else None
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, satici=satici,
                 kaynak=magaza, stok=stok, yontem=yontem,
                 guncelleme=_once_iso(blok))
        k = (url, round(fiyat, 2))
        if k not in bulunan:
            bulunan[k] = t
    return sorted(bulunan.values(), key=lambda x: x.sepet_fiyati)


def satici_teklifleri(t, model_url: str) -> list[Kart]:
    """Model sayfasını aç, satıcı tekliflerini (gerçek mağaza URL'leriyle) oku."""
    y = t.getir(model_url, site=SITE)
    return teklifleri_ayristir(y.html, model_url=model_url, yontem=y.yontem)


def urun(t, url: str) -> Kart | None:
    """Model sayfasının en ucuz gerçek mağaza teklifi (yönlendirme çözücü)."""
    try:
        ts = satici_teklifleri(t, url)
    except Exception:
        return None
    return ts[0] if ts else None


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    """Epey araması (tek sayfa; site sonuçları JS ile genişletiyor)."""
    toplu: dict[str, Kart] = {}
    for q in sorgu_varyantlari(sorgu):
        y = t.getir(arama_url(q), site=SITE)
        for tk in teklifler(y.html, yontem=y.yontem):
            toplu.setdefault(tk.url, tk)
    hepsi = []
    for tk in sorted(toplu.values(), key=lambda x: x.sepet_fiyati):
        if min_f is not None and tk.sepet_fiyati < min_f:
            continue
        if max_f is not None and tk.sepet_fiyati > max_f:
            continue
        hepsi.append(tk)
    return hepsi
