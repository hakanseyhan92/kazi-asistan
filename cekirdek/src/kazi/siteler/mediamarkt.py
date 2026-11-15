"""mediamarkt.com.tr bağdaştırıcısı.

Ayrıştırma
----------
Arama sonuçları SPA'dır ve GraphQL (`operationName=SearchV4`) yanıtıyla gelir;
`site_ici_ara(..., ag_desen=...)` bu JSON'u yakalar (DOM'dan sağlam).
  data.searchV4.products[] -> productAggregate.product.{title,url}
  cofrProductAggregate.cofrPriceFeature.price.amount        -> fiyat
  ...promoPrice / strikePrice.amount                        -> indirimli/liste
  ...marketplaceSeller                                     -> satıcı
  ...price.installment.duration                             -> taksit
JSON yakalanamazsa sayfadaki JSON-LD/DOM'a düşülür.

Kullanıcı kuralı §2.7: arama URL'sine doğrudan gidilmez; ana sayfa
(https://www.mediamarkt.com.tr/) + arama kutusu `input[name="query"]`.
"""
from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser

from ._kart import Kart, sorgu_varyantlari, tl_sayi

SITE = "mediamarkt"
HTTP_ARAMA = False   # arama yalnız tarayıcıdaki site içi kutudan → hızlı HTTP turuna girmez (zamanlayici/hizli.py)
TABAN = "https://www.mediamarkt.com.tr"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ("input[name='query']", "input[type='search']")
dogrudan_url_yasak = True

_GQL = r"operationName=SearchV4"
_TAKSIT = re.compile(r"Peşin fiyat[ıi]na\s*(\d+)\s*Taksit", re.I)


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def _fiyat(o: dict) -> float | None:
    if not isinstance(o, dict):
        return None
    for k in ("amount", "value", "price"):
        if o.get(k) is not None:
            v = tl_sayi(o.get(k))
            if v:
                return v
    return None


def teklifler_json(yanitlar, yontem: str = "brave") -> list[Kart]:
    """Yakalanan GraphQL yanıtları -> teklifler."""
    en_iyi: dict[str, dict] = {}
    for y in yanitlar or []:
        if "SearchV4" not in str(y.get("url") or ""):
            continue
        data = (y.get("json") or {}).get("data") or {}
        sv = data.get("searchV4") or {}
        for x in sv.get("products") or []:
            if not isinstance(x, dict):
                continue
            agg = x.get("productAggregate") or {}
            prod = agg.get("product") or {}
            cofr = x.get("cofrProductAggregate") or {}
            pf = cofr.get("cofrPriceFeature") or {}
            pr = pf.get("price") or {}
            satis = _fiyat(pf.get("promoPrice")) or _fiyat(pr)
            if not satis:
                continue
            ad = str(prod.get("title") or "").strip()
            url = _mutlak(str(prod.get("url") or ""))
            if not ad or not url:
                continue
            liste = _fiyat(pf.get("strikePrice"))
            if liste is not None and liste <= satis:
                liste = None
            seller = pf.get("marketplaceSeller")
            satici = None
            if isinstance(seller, dict):
                satici = str(seller.get("name") or seller.get("title") or "").strip() or None
            elif isinstance(seller, str) and seller.strip():
                satici = seller.strip()
            taksit = None
            try:
                n = int((pr.get("installment") or {}).get("duration"))
                if 2 <= n <= 24:
                    taksit = n
            except (TypeError, ValueError):
                pass
            ds = (((cofr.get("cofrDeliveryFeature") or {}).get("delivery") or {})
                  .get("displayStatus") or "")
            stok = None
            if ds:
                stok = ds.upper() in ("AVAILABLE", "AVAILABLE_WITHIN_REASONABLE_TIME_FRAME")
            yer = {"ad": ad, "url": url, "fiyat": satis, "liste": liste,
                   "satici": satici, "taksit": taksit, "stok": stok}
            eski = en_iyi.get(url)
            if eski is None or yer["fiyat"] < eski["fiyat"]:
                en_iyi[url] = yer
    return sorted((Kart(site=SITE, baslik=v["ad"], sepet_fiyati=v["fiyat"], url=v["url"],
                        liste_fiyati=v["liste"], satici=v["satici"],
                        pesin_taksit=v["taksit"], stok=v["stok"], yontem=yontem)
                   for v in en_iyi.values()), key=lambda t: t.sepet_fiyati)


def teklifler_ayristir(html: str, yontem: str = "brave") -> list[Kart]:
    """JSON yakalanamadıysa DOM yedeği: ad + ₺ fiyatı olan bağlantılar."""
    tree = LexborHTMLParser(html or "")
    bulunan: dict[str, Kart] = {}
    for a in tree.css('a[href*="/product/"]'):
        ad = " ".join((a.text() or "").split())
        if len(ad) < 10:
            continue
        kart = a
        for _ in range(4):
            if kart.parent is None:
                break
            kart = kart.parent
            f = re.search(r"₺\s*([\d.]+,\d{2})", " ".join((kart.text() or "").split()))
            if f:
                fiyat = tl_sayi(f.group(1))
                if fiyat:
                    url = _mutlak((a.attributes.get("href") or "").split("?")[0])
                    if url and url not in bulunan:
                        bulunan[url] = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url,
                                            yontem=yontem)
                break
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


teklifler = teklifler_ayristir


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    """MediaMarkt araması: ana sayfa + site içi arama kutusu; GraphQL yakalanır."""
    toplu: dict[str, Kart] = {}
    for q in sorgu_varyantlari(sorgu):
        html, yanitlar = t.site_ici_ara(
            SITE, ANA, q, kutu_secicileri=KUTU, ag_desen=_GQL)
        bulunan = teklifler_json(yanitlar, yontem="brave")
        if not bulunan:
            bulunan = teklifler_ayristir(html, yontem="brave")
        for tk in bulunan:
            toplu.setdefault(tk.url, tk)
    hepsi = []
    for tk in sorted(toplu.values(), key=lambda x: x.sepet_fiyati):
        if min_f is not None and tk.sepet_fiyati < min_f:
            continue
        if max_f is not None and tk.sepet_fiyati > max_f:
            continue
        hepsi.append(tk)
    return hepsi


def urun(t, url: str) -> Kart | None:
    """Ürün sayfası: başlık + DOM'daki ilk ₺ fiyatı + JSON-LD yokluğunda metin."""
    try:
        h = t.brave_getir(url, site=SITE)
    except Exception:
        return None
    tree = LexborHTMLParser(h or "")
    h1 = tree.css_first("h1")
    ad = " ".join((h1.text() or "").split()) if h1 else ""
    if not ad:
        return None
    metin = " ".join((tree.text(separator=" ") or "").split())
    fm = re.search(r"₺\s*([\d.]+,\d{2})", metin)
    fiyat = tl_sayi(fm.group(1)) if fm else None
    if not fiyat:
        return None
    satici = None
    sm = re.search(r"Satıcı:\s*([A-Za-zÇĞİÖŞÜçğıöşü0-9 .&'-]{2,60})", metin)
    if sm:
        satici = sm.group(1).strip()
    stok = None
    if tree.css_first('[data-test*="delivery_AVAILABLE"]') is not None:
        stok = True
    elif tree.css_first('[data-test*="delivery_NOT_AVAILABLE"]') is not None:
        stok = False
    tm = _TAKSIT.search(metin)
    taksit = int(tm.group(1)) if tm and 2 <= int(tm.group(1)) <= 24 else None
    return Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, satici=satici,
                pesin_taksit=taksit, stok=stok, yontem="brave")
