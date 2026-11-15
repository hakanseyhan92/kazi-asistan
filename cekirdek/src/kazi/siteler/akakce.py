"""akakce.com bağdaştırıcısı (TOPLAYICI).

Ayrıştırma
----------
Arama sayfası: Astro adalarının `props` özniteliğindeki HTML-kaçışlı JSON
(`searchData.productList.products`) — her ürün için model bilgisi (code, price,
countOfPrices, name, url).
Model sayfası: `initialPgList` astro adası — satıcı teklifleri
(`pgCode, price, pgName, vdName, pgNick, stock, campaigns, params.f`).
`campaigns` içindeki `type == "Installment"` kartlarının metninden
("Peşin fiyatına 6 taksit") vade farksız taksit sayısı çıkarılır.

`kaynak` = satış sitesi adı (n11, Hepsiburada, Trendyol...), `satici` = mağaza
(`pgNick`). Teklif URL'si params.f'deki Akakçe yönlendirmesidir
(https://www.akakce.com/r/?pr=...&vd=...&pg=...).
"""
from __future__ import annotations

import html as _html
import json
import re
import urllib.parse

from ._kart import Kart, tl_sayi

SITE = "akakce"
TABAN = "https://www.akakce.com"
ANA = TABAN + "/"
ANA_URL = ANA
ARAMA = TABAN + "/arama/"
dogrudan_url_yasak = False

_ASTRO = re.compile(r"<astro-island\b[^>]*>", re.S | re.I)
_PROPS = re.compile(r'props="(.*?)"', re.S)
_TAKSIT = re.compile(r"(\d+)\s*taksit", re.I)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


# ------------------------------------------------------------------ yardımcı
def arama_url(sorgu: str, sayfa: int = 1) -> str:
    u = ARAMA + "?q=" + urllib.parse.quote_plus(sorgu)
    if sayfa and sayfa > 1:
        u += "&p=" + str(int(sayfa))
    return u


def _astro(v):
    """Astro serileştirmesini çöz: [0, x] -> x, [1, [x, y]] -> [x, y]."""
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int):
        if v[0] == 1 and isinstance(v[1], list):
            return [_astro(x) for x in v[1]]
        return _astro(v[1])
    if isinstance(v, dict):
        return {k: _astro(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_astro(x) for x in v]
    return v


def _adalar(html: str):
    """Her astro-island etiketinin çözülmüş props sözlüğünü üret."""
    for m in _ASTRO.finditer(html or ""):
        p = _PROPS.search(m.group(0))
        if not p:
            continue
        try:
            d = json.loads(_html.unescape(p.group(1)))
        except Exception:
            continue
        if isinstance(d, dict):
            yield _astro(d)


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def _taksit(campaigns) -> int | None:
    sayilar = []
    for c in campaigns or []:
        if not isinstance(c, dict):
            continue
        if str(c.get("type", "")).lower() != "installment":
            continue
        for a in _TAKSIT.findall(str(c.get("text") or "")):
            try:
                n = int(a)
            except ValueError:
                continue
            if 2 <= n <= 24:
                sayilar.append(n)
    return max(sayilar) if sayilar else None


def _teklif_url(o: dict, model_url: str) -> str:
    par = o.get("params") or {}
    f = par.get("f") if isinstance(par, dict) else None
    if f:
        yol = urllib.parse.unquote(str(f))
        return _mutlak(yol)
    u = str(o.get("url") or "")
    if u.startswith("#") or not u:            # sayfa içi sekme bağlantısı
        return model_url
    return _mutlak(u)


# ------------------------------------------------------------------ arama
def modeller(html: str, yontem: str = "http") -> list[Kart]:
    """Arama sayfası HTML'i -> model listesi (her modelin en düşük fiyatı)."""
    bulunan: dict[str, Kart] = {}
    for d in _adalar(html):
        sd = d.get("searchData")
        if not isinstance(sd, dict):
            continue
        pl = sd.get("productList")
        if not isinstance(pl, dict):
            continue
        for p in pl.get("products") or []:
            if not isinstance(p, dict):
                continue
            ad = str(p.get("name") or "").strip()
            fiyat = tl_sayi(p.get("price"))
            url = _mutlak(str(p.get("url") or ""))
            if not ad or not fiyat or fiyat <= 0 or not url:
                continue
            stok = None
            try:
                if int(p.get("countOfPrices") or 0) > 0:
                    stok = True
            except (TypeError, ValueError):
                stok = None
            t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, satici=None,
                     stok=stok, yontem=yontem)
            eski = bulunan.get(url)
            if eski is None or t.sepet_fiyati < eski.sepet_fiyati:
                bulunan[url] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def ara(t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2) -> list[Kart]:
    """Akakçe araması; sayfa başına en çok 3 sayfa gezilir (§0)."""
    hepsi: dict[str, Kart] = {}
    for n in range(1, max(1, min(int(sayfa or 1), 3)) + 1):
        try:
            y = t.getir(arama_url(sorgu, n), site=SITE)
        except Exception:
            if n == 1:
                raise
            break
        yeni = 0
        for tk in modeller(y.html, yontem=y.yontem):
            if min_f is not None and tk.sepet_fiyati < min_f:
                continue
            if max_f is not None and tk.sepet_fiyati > max_f:
                continue
            eski = hepsi.get(tk.url)
            if eski is None:
                yeni += 1
                hepsi[tk.url] = tk
            elif tk.sepet_fiyati < eski.sepet_fiyati:
                hepsi[tk.url] = tk
        if yeni == 0:
            break
    return sorted(hepsi.values(), key=lambda t: t.sepet_fiyati)


# ------------------------------------------------------------------ teklifler
def teklifleri_ayristir(html: str, model_url: str = "", yontem: str = "http") -> list[Kart]:
    """Model sayfası HTML'i -> satıcı teklifleri (saf ayrıştırıcı, testler için)."""
    adalar = list(_adalar(html))
    ham: list[dict] = []
    for d in adalar:                       # önce tam teklif listesi
        pl = d.get("initialPgList")
        if isinstance(pl, list) and pl:
            ham = [x for x in pl if isinstance(x, dict)]
            break
    if not ham:                            # yoksa vitrindeki tek teklif
        for d in adalar:
            spot = d.get("spotPg")
            if isinstance(spot, dict):
                ham.append(spot)
                break

    if not ham:   # ada yoksa HTML-kaçışlı ham metinden dene
        coz = _html.unescape(html or "")
        for ch in coz.split('{"pgCode":[0,')[1:]:
            ch = ch[:4000]

            def g(k, rx=r'"([^"]*)"'):
                m = re.search('"' + k + r'":\[0,' + rx + r'\]', ch)
                return m.group(1) if m else None

            price, vd = g("price", r"([\d.]+)"), g("vdName")
            if not price or not vd:
                continue
            ham.append({"price": float(price), "vdName": vd, "pgName": g("pgName"),
                        "pgNick": g("pgNick"), "stock": g("stock", r"(\d+)"),
                        "campaigns": [{"type": "Installment", "text": t}
                                      for t in _TAKSIT.findall(ch)],
                        "params": {"f": g("f")}})

    bulunan: dict[tuple, Kart] = {}
    for o in ham:
        fiyat = tl_sayi(o.get("price"))
        kaynak = str(o.get("vdName") or "").strip() or None
        if not fiyat or fiyat <= 0 or not kaynak:
            continue
        satici = str(o.get("pgNick") or "").strip() or None
        ad = str(o.get("pgName") or "").strip()
        url = _teklif_url(o, model_url)
        stok = None
        s = o.get("stock")
        if s is not None and not isinstance(s, str):
            try:
                stok = int(s) > 0
            except (TypeError, ValueError):
                stok = None
        elif isinstance(s, str) and s.strip().isdigit():
            stok = int(s) > 0
        guncelleme = o.get("lastUpdateTime")
        if isinstance(guncelleme, list) and len(guncelleme) == 2:
            guncelleme = guncelleme[1]
        t = Kart(site=SITE, baslik=ad, sepet_fiyati=fiyat, url=url, satici=satici,
                 pesin_taksit=_taksit(o.get("campaigns")), stok=stok, kaynak=kaynak,
                 yontem=yontem,
                 guncelleme=str(guncelleme) if guncelleme else None)
        k = (kaynak, satici, url, round(fiyat, 2))
        eski = bulunan.get(k)
        if eski is None or t.sepet_fiyati < eski.sepet_fiyati:
            bulunan[k] = t
    return sorted(bulunan.values(), key=lambda t: t.sepet_fiyati)


def teklifler(t, model_url: str) -> list[Kart]:
    """Model sayfasından satıcı teklifleri. 429/blokta Tarayici Brave'e geçer."""
    y = t.getir(model_url, site=SITE)
    return teklifleri_ayristir(y.html, model_url=model_url, yontem=y.yontem)


def urun(t, url: str) -> Kart | None:
    """Model sayfasının en düşük teklifi (tek Kart olarak)."""
    try:
        teklifler_ = teklifler(t, url)
    except Exception:
        return None
    return teklifler_[0] if teklifler_ else None
