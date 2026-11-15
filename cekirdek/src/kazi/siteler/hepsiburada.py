"""hepsiburada.com bağdaştırıcısı.

Ayrıştırma (G3b — 14.11.2026 canlı denemesiyle düzeltildi)
----------------------------------------------------------
Kart metni TAM metin üzerinden ayrıştırılır (DOM fiyat düğümleri kırılgan):

  li[class*="productListContent"]   (yedek: [data-test-id*="product-card"], article)
  ilk a[href]                       -> ürün bağlantısı
  kart innerText:
    "Teslimat bilgisi: 16 Kas kargoda <AD> Kampanya: Peşin fiyatına 7 x 18.206 TL
     ... Sepete özel 134.156,80 TL %5 127.448,96 TL"
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

from ._json_yakala import JsonYakalayici, gomulu_durum, html_atamasi
from ._kart import Kart, tl_sayi
from ._ortak import BotEngeli, blok_mu

SITE = "hepsiburada"
TABAN = "https://www.hepsiburada.com"
ANA = TABAN + "/"
ANA_URL = ANA
KUTU = ("input[data-test-id='search-bar-input']",)
dogrudan_url_yasak = True

_KART_SECICILERI = ('li[class*="productListContent"]',
                    '[data-test-id*="product-card"]',
                    "article")

# "7 x 18.206 TL", "3 x1.397 TL" -> taksit (fiyat değil)
_TAKSITLI = re.compile(r"\d+\s*x\s*[\d.,]+\s*TL", re.I)
_TAKSIT = re.compile(r"(?:(?:Peşin fiyat[ıi]na)\s*)?(\d+)\s*x\s*[\d.,]+\s*TL", re.I)
# "Ek 1500 TL Kupon" iki kez tekrarlanır; fiyat sayılmaz
_KUPON = re.compile(r"Ek\s*[\d.,]+\s*TL\s*Kupon", re.I)
# TL tutarı
_TL = re.compile(r"(\d[\d.,]*)\s*TL", re.I)
_TESLIMAT = re.compile(r"^Teslimat bilgisi:.*?kargoda\s*", re.I)
_KAMPANYA_SON = re.compile(r"\s*Kampanya:.*$", re.I | re.S)
_REDUX = re.compile(r'<script[^>]*id="reduxStore"[^>]*>(.*?)</script>', re.S)
_STOKSUZ = ("tükendi", "stokta yok", "satışta değil")

# Ürün adı temizliğinde atılan gürültü (fiyat/taksit/puan/promosyon)
_GURULTU = re.compile(
    r"(?:Ürün puanı.*?değerlendirme|"
    r"\b\d+(?:[.,]\d+)?\s*\(\d+\)|"
    r"Sepete özel|Premium ile|Peşin fiyat[ıi]na|"
    r"\d+\s*x\s*[\d.,]+\s*TL|"
    r"Ek\s*[\d.,]+\s*TL\s*Kupon|"
    r"%\s*\d+|"
    r"\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?\s*TL|"
    r"\bKampanya:|\b30 günün en düşük fiyatı)",
    re.I,
)


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def _taksit(metin: str) -> int | None:
    m = _TAKSIT.search(metin or "")
    if m:
        try:
            n = int(m.group(1))
            if 2 <= n <= 24:
                return n
        except ValueError:
            pass
    return None


def _kampanya(metin: str) -> str | None:
    """Kupon notları: 'Ek 1500 TL Kupon' (tekrarı tekilleştir)."""
    notlar: list[str] = []
    for m in _KUPON.finditer(metin or ""):
        s = " ".join(m.group(0).split())
        if s not in notlar:
            notlar.append(s)
    return " · ".join(notlar) or None


def _fiyatlar(metin: str) -> list[float]:
    """Kart metninden gerçek fiyatlar (taksit ve kupon tutarları çıkarılmış)."""
    s = metin or ""
    # DOM alt düğümleri "427 ,04 TL" gibi bölebiliyor -> sayıyı birleştir
    s = re.sub(r"(?<=\d)\s+([.,])\s*(?=\d)", r"\1", s)
    s = _TAKSITLI.sub(" ", s)
    s = _KUPON.sub(" ", s)
    degerler: list[float] = []
    for ham in _TL.findall(s):
        v = tl_sayi(ham)
        if v and 0 < v < 10_000_000 and v not in degerler:
            degerler.append(v)
    return sorted(degerler)


def _ad_temizle(metin: str) -> str:
    s = " ".join((metin or "").split())
    s = _TESLIMAT.sub("", s)
    s = _KAMPANYA_SON.sub("", s)
    s = _GURULTU.sub(" ", s)
    return " ".join(s.split()).strip(" -|·")


def _kartlar(tree):
    for sec in _KART_SECICILERI:
        kartlar = tree.css(sec)
        if kartlar:
            return kartlar
    return []


def _kart_teklif(kart, yontem: str) -> Kart | None:
    # URL: kart içindeki ilk gerçek bağlantı (tercihen başlık bağlantısı)
    a = kart.css_first("h2 a") or kart.css_first("a[href]")
    if a is None:
        return None
    href = (a.attributes.get("href") or "").strip()
    if not href or href.startswith(("#", "javascript:")):
        return None
    url = _mutlak(href)
    metin = " ".join((kart.text(separator=" ") or "").split())

    ad = " ".join((a.text() or "").split())
    ad = _ad_temizle(ad) if ad else ""
    if len(ad) < 5:
        ad = _ad_temizle(metin)
    if not ad:
        return None

    fiyatlar = _fiyatlar(metin)
    if not fiyatlar:
        return None
    fiyat = fiyatlar[0]                     # efektif: sepete özel / Premium
    liste = fiyatlar[-1] if len(fiyatlar) > 1 and fiyatlar[-1] > fiyat else None
    stok = False if any(s in metin.lower() for s in _STOKSUZ) else None
    tk = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, liste_fiyati=liste,
              pesin_taksit=_taksit(metin), stok=stok, yontem=yontem)
    try:
        tk.kampanya = _kampanya(metin)
    except Exception:
        pass
    return tk


def teklifler_ayristir(html: str, yontem: str = "brave") -> list[Kart]:
    tree = LexborHTMLParser(html or "")
    bulunan: dict[str, Kart] = {}
    for kart in _kartlar(tree):
        tk = _kart_teklif(kart, yontem)
        if tk is None:
            continue
        eski = bulunan.get(tk.url)
        if eski is None or tk.sepet_fiyati < eski.sepet_fiyati:
            bulunan[tk.url] = tk
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


teklifler = teklifler_ayristir


# ------------------------------------------------------------------ JSON (sayfanın kendi verisi)
# Arama sonucu sayfası ürün listesini satır içi betikte taşır:
#   window.MORIA.PRODUCTLIST['<guid>'] = {'STATE': {"data": {"products": [...], "currentPage": 1, ...}}}
# product.variantList[] -> {name, url (göreli), procurable, listing{priceInfo{price,originalPrice},
#   campaignPriceInfo{contentType: Basket|Premium, discountedPrice}, merchantName,
#   categorizedLabels{winner[{text}], affordability[{installmentCount,isCashPrice}]}}}
_JS_DURUM = ("js:Object.values(window.MORIA.PRODUCTLIST)[0].STATE",)
_YAKALA_DESENI = (r"hepsiburada\.com/.*(?:search|product|listing)",)


def _sayi(v) -> float | None:
    x = tl_sayi(v)
    return x if x and 0 < x < 10_000_000 else None


def _json_taksit(listing: dict) -> int | None:
    for e in ((listing.get("categorizedLabels") or {}).get("affordability")) or []:
        if not isinstance(e, dict):
            continue
        try:
            n = int(e.get("installmentCount") or 0)
        except (TypeError, ValueError):
            continue
        if e.get("isCashPrice") and 2 <= n <= 24:
            return n
    return None


def _json_kampanya(listing: dict) -> str | None:
    notlar: list[str] = []
    for e in ((listing.get("categorizedLabels") or {}).get("winner")) or []:
        t = " ".join(str((e or {}).get("text") or "").split())
        if "kupon" in t.lower() and t not in notlar:
            notlar.append(t)
    return " · ".join(notlar) or None


def json_kartlari(veri, yontem: str = "brave") -> list[Kart]:
    """MORIA.PRODUCTLIST STATE (ya da `{"products": [...]}`/liste) -> Kart listesi.

    Fiyat: `campaignPriceInfo.discountedPrice` (Sepete özel / Premium) varsa alıcının
    gördüğü fiyat odur; yoksa `priceInfo.price`. `liste_fiyati` = üst fiyat (daha büyükse).
    Bozuk/boş veri -> [] (istisna yok).
    """
    try:
        if isinstance(veri, dict):
            ic = veri.get("data") if isinstance(veri.get("data"), dict) else veri
            veri = ic.get("products")
        if not isinstance(veri, list):
            return []
        bulunan: dict[str, Kart] = {}
        for p in veri:
            if not isinstance(p, dict):
                continue
            for v in p.get("variantList") or []:
                try:
                    if not isinstance(v, dict):
                        continue
                    ls = v.get("listing") or {}
                    pi = ls.get("priceInfo") or {}
                    fiyat = _sayi(pi.get("price"))
                    ust = _sayi(pi.get("originalPrice")) or fiyat
                    cp = ls.get("campaignPriceInfo") or {}
                    indirimli = _sayi(cp.get("discountedPrice")) if isinstance(cp, dict) else None
                    if indirimli and fiyat and indirimli < fiyat:
                        ust = max(ust or 0, fiyat)
                        fiyat = indirimli
                    ad = " ".join(str(v.get("name") or "").split())
                    url = _mutlak(str(v.get("url") or "").split("#")[0])
                    if not fiyat or not ad or not url:
                        continue
                    liste = ust if ust and ust > fiyat else None
                    satici = str(ls.get("merchantName") or "").strip() or None
                    stok = False if v.get("procurable") is False else None
                    tk = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url,
                              liste_fiyati=liste, satici=satici, stok=stok,
                              pesin_taksit=_json_taksit(ls), yontem=yontem)
                    tk.kampanya = _json_kampanya(ls)
                    eski = bulunan.get(url)
                    if eski is None or tk.sepet_fiyati < eski.sepet_fiyati:
                        bulunan[url] = tk
                except Exception:
                    continue
        return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)
    except Exception:
        return []


def _sayfa_durumu(pg, html: str | None = None):
    """Sayfadaki MORIA.PRODUCTLIST STATE (önce canlı sayfa, yoksa HTML)."""
    d = gomulu_durum(pg, _JS_DURUM) if pg is not None else None
    if not d and html:
        i = html.find("window.MORIA.PRODUCTLIST")
        if i >= 0:
            d = html_atamasi(html[i:], "'STATE':")
    return d


def _json_sayfa(pg, yakalayici, sayfa_no: int, html: str | None = None) -> list[Kart]:
    """Sonuç sayfasının JSON kartları; sayfa numarası uyuşmazsa [] (bayat durum)."""
    try:
        adaylar = [_sayfa_durumu(pg, html)]
        adaylar += [y.get("json") for y in (getattr(yakalayici, "yanitlar", None) or [])]
        for d in adaylar:
            if not isinstance(d, dict):
                continue
            ic = d.get("data") if isinstance(d.get("data"), dict) else d
            try:
                gecerli = int(ic.get("currentPage") or 0)
            except (TypeError, ValueError):
                gecerli = 0
            if gecerli != sayfa_no:       # bayat/başka sayfa durumu kullanılmaz
                continue
            k = json_kartlari(d)
            if k:
                return k
    except Exception:
        pass
    return []


# ------------------------------------------------------------------ arama
_SONRAKI = ('a[data-test-id="next-page"]', '[data-test-id*="next-page"]',
            'a[aria-label*="Sonraki" i]', 'button[aria-label*="Sonraki" i]')


def _kaydir(pg):
    try:
        for oran in (0.4, 0.8, 1.0):
            pg.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {oran})")
            pg.wait_for_timeout(400)
    except Exception:
        pass


def _kartlari_bekle(pg, ust_ms: int = 12000, eski_url: str | None = None) -> None:
    """Sabit 5–6 sn uyku yerine: (adres değişti →) ürün kartları görünür olduğu an devam (15.11 hız).
    Önce adres değişimi beklenir; yoksa ana sayfadaki/önceki sayfadaki kartlar okunurdu."""
    if eski_url is not None:
        try:
            pg.wait_for_url(lambda u: u != eski_url, timeout=10000)
        except Exception:
            pass
    try:
        pg.wait_for_selector(", ".join(_KART_SECICILERI), timeout=ust_ms)
    except Exception:
        pass
    pg.wait_for_timeout(500)


def _sayfa_url(url: str, n: int) -> str:
    """Arama sonuç URL'sine `sayfa=N` ekle (aynı oturum, §2.7)."""
    p = urlsplit(url)
    q = dict(parse_qsl(p.query, keep_blank_values=True))
    q["sayfa"] = str(n)
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


def _arama_sayfasi(t, sorgu: str, yakalayici=None):
    """Ana sayfa + site içi arama; sonuç sayfası (html, page, json_kartlari)."""
    pg = t._sekme(SITE)
    t.hiz_sinirla(SITE)
    try:
        pg.goto(ANA, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pg.goto(ANA, wait_until="commit", timeout=60000)

    kutu = pg.locator(KUTU[0]).first
    kutu.wait_for(state="visible", timeout=20000)
    kutu.focus()
    pg.wait_for_timeout(800)          # ilk harf yutulmasın (canlı gözlem)
    try:
        kutu.fill("")
    except Exception:
        pass
    pg.keyboard.type(sorgu, delay=90)
    try:
        yazilan = kutu.input_value()
    except Exception:
        yazilan = None
    if yazilan is not None and yazilan.strip() != sorgu.strip():
        try:
            kutu.fill(sorgu)
        except Exception:
            pass
    eski_url = pg.url
    pg.keyboard.press("Enter")
    _kartlari_bekle(pg, eski_url=eski_url)
    jkartlar = _json_sayfa(pg, yakalayici, 1)
    if not jkartlar:                  # JSON yok -> eski DOM yolu (kaydırma)
        _kaydir(pg)

    html = pg.content()
    if blok_mu(html):
        try:
            t._hata(SITE, f"site ici arama blok ({pg.url})")
            t.bildir_kanca(SITE, pg.url)
        except Exception:
            pass
        raise BotEngeli(SITE, pg.url)
    if not jkartlar:
        jkartlar = _json_sayfa(None, None, 1, html)
    return html, pg, jkartlar


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    """Hepsiburada araması: en çok `sayfa` sonuç sayfası (≥1).

    Önce sayfanın kendi JSON durumu (MORIA.PRODUCTLIST) okunur; kart çıkmazsa
    eski DOM yolu aynen çalışır.
    """
    try:
        yak = JsonYakalayici(t._sekme(SITE), _YAKALA_DESENI)
    except Exception:
        yak = None
    try:
        html, pg, jk = _arama_sayfasi(t, sorgu, yak)
        bulunan: dict[str, Kart] = {}
        for tk in (jk or teklifler_ayristir(html, yontem="brave")):
            bulunan.setdefault(tk.url, tk)

        for i in range(max(1, int(sayfa)) - 1):
            t.hiz_sinirla(SITE)
            onceki_url = pg.url
            tiklandi = False
            for sec in _SONRAKI:
                try:
                    b = pg.locator(sec).first
                    if b.count() > 0 and b.is_visible():
                        b.click(timeout=5000)
                        tiklandi = True
                        break
                except Exception:
                    continue
            if not tiklandi:
                try:
                    pg.goto(_sayfa_url(pg.url, i + 2), wait_until="domcontentloaded",
                            timeout=60000)
                    tiklandi = True
                except Exception:
                    break
            _kartlari_bekle(pg, eski_url=onceki_url)
            jk = _json_sayfa(pg, yak, i + 2)
            if not jk:
                _kaydir(pg)
            html = pg.content()
            if blok_mu(html):
                raise BotEngeli(SITE, pg.url)
            if not jk:
                jk = _json_sayfa(None, None, i + 2, html)
            for tk in (jk or teklifler_ayristir(html, yontem="brave")):
                if tk.url not in bulunan or tk.sepet_fiyati < bulunan[tk.url].sepet_fiyati:
                    bulunan[tk.url] = tk
    finally:
        if yak is not None:
            yak.kapat()

    hepsi = []
    for tk in bulunan.values():
        if min_f is not None and tk.sepet_fiyati < min_f:
            continue
        if max_f is not None and tk.sepet_fiyati > max_f:
            continue
        hepsi.append(tk)
    return sorted(hepsi, key=lambda t: t.sepet_fiyati)


def urun(t, url: str) -> Kart | None:
    """Ürün sayfası: reduxStore productState (fiyat, satıcı, stok) + taksit metni."""
    try:
        y = t.getir(url, site=SITE)
    except Exception:
        return None
    h = y.html or ""
    m = _REDUX.search(h)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
    except Exception:
        return None
    p = (d.get("productState") or {}).get("product") or {}
    if not isinstance(p, dict) or not p:
        return None

    ad = str(p.get("name") or "").strip()
    fiyatlar = []
    for x in p.get("prices") or []:
        if isinstance(x, dict):
            v = tl_sayi(x.get("value"))
            if v:
                fiyatlar.append(v)
    fiyat = min(fiyatlar) if fiyatlar else None
    if not ad or not fiyat:
        return None
    satici = (p.get("merchant") or {}).get("name") or p.get("merchantName") or None
    stok = None
    si = p.get("stockInformation")
    if isinstance(si, dict) and si.get("isInStock") is not None:
        stok = bool(si.get("isInStock"))
    elif p.get("isInStock") is not None:
        stok = bool(p.get("isInStock"))
    liste = None
    if p.get("formattedMaximumPriceOfListing"):
        v = tl_sayi(p.get("formattedMaximumPriceOfListing"))
        if v and v > fiyat:
            liste = v

    metin = " ".join((LexborHTMLParser(h).text(separator=" ") or "").split()) \
        if h else ""
    return Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url,
                liste_fiyati=liste, satici=str(satici).strip() if satici else None,
                pesin_taksit=_taksit(metin), stok=stok, yontem=y.yontem)
