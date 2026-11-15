"""Mağaza bağdaştırıcıları için ortak erişim yardımcıları.

Kullanıcı kuralı §2.7: Brave'de arama URL'sine DOĞRUDAN gidilmez. Bu yüzden
`Tarayici.getir()` (HTTP başarısız olunca doğrudan URL'yi Brave'de açar) arama
sayfaları için kullanılmaz. Bunun yerine:

  http_ya_da_site_ici(): önce arama URL'sini düz HTTP ile dener; 403/blok/hata
  olursa Brave'de ana sayfa + arama kutusu akışına (`site_ici_ara`) düşer.

Tek sayfa HTTP aramaları için `http_ara()`, çok sayfalı HTTP aramaları için
`http_ara_sayfalar()` kullanılabilir; blok olursa site içi aramaya geçilir.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
from dataclasses import dataclass

from ._kart import Kart, tl_sayi

_BLOK_RX = re.compile(
    r"just a moment|cf-chl|challenge-platform|captcha|px-captcha|access denied|"
    r"olağan dışı trafik|olagan disi trafik|robot olmadığınızı|robot olmadiginizi|"
    r"bot koruması|bot korumasi|are you a robot|verify you are human",
    re.IGNORECASE,
)
_FIYAT_RX = re.compile(r"(₺|\bTL\b|\d[\d.,]*\s*(?:TL|₺))", re.IGNORECASE)


def _yedek_blok_mu(html: str | None) -> bool:
    if not html:
        return True
    if _BLOK_RX.search(html):
        if len(html) > 150_000 and len(_FIYAT_RX.findall(html)) >= 5:
            return False
        return True
    if len(html) < 2048 and not _FIYAT_RX.search(html):
        return True
    return False


def _yedek_site_adi(url: str) -> str:
    try:
        h = urllib.parse.urlparse(url).hostname or url
    except Exception:
        h = str(url)
    h = h.lower()
    if h.startswith("www."):
        h = h[4:]
    return h


class _YedekBotEngeli(Exception):
    def __init__(self, site: str, url: str = ""):
        self.site = site
        self.url = url
        super().__init__(f"{site}: bot doğrulaması/erişim engeli ({url})")


@dataclass
class _YedekYanit:
    html: str
    yontem: str
    durum: int | None
    url: str


try:
    from ..tarayici import BotEngeli, Yanit, blok_mu, site_adi  # type: ignore[import-not-found]
except ImportError:
    BotEngeli = _YedekBotEngeli  # type: ignore[misc,assignment]
    Yanit = _YedekYanit  # type: ignore[misc,assignment]
    blok_mu = _yedek_blok_mu
    site_adi = _yedek_site_adi

# "Peşin fiyatına 6 taksit" / "Peşin Fiyatına 9 Taksit" / "6 x 22.299,50 TL"
_TAKSIT = re.compile(
    r"peşin\s+fiyat[ıi]na\s*(\d{1,2})\s*(?:x|taksit)", re.IGNORECASE)
_TAKSIT_NX = re.compile(
    r"\b(\d{1,2})\s*x\s*[\d]{1,3}(?:\.[\d]{3})*(?:,\d{2})?\s*(?:TL|₺)",
    re.IGNORECASE)


def json_coz(ham: str | None):
    """JSON-LD/gömülü JSON'u toleranslı çöz.

    Canlı sayfalarda görülen bozulmalar: script sonunda `;`, JSON içinde
    kaçışlanmamış kontrol karakteri (vatan), birden çok nesne. İlk geçerli
    nesneyi döndürür; çözülemezse None.
    """
    s = (ham or "").strip().lstrip("\ufeff")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", s)
    s = s.rstrip().rstrip(";").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        try:
            return json.JSONDecoder().raw_decode(s)[0]
        except Exception:
            return None


def taksit_metinden(metin: str | None) -> int | None:
    """Metindeki vade farksız taksit sayısını bul (2..24), yoksa None."""
    if not metin:
        return None
    for rx in (_TAKSIT, _TAKSIT_NX):
        for m in rx.finditer(metin):
            n = int(m.group(1))
            if 2 <= n <= 24:
                return n
    return None


def http_ara(t, site: str, url: str, zaman_asimi: float = 40.0):
    """Tek sayfa HTTP isteği. (durum, html) döner; istisna fırlatmaz."""
    try:
        t.hiz_sinirla(site)
        return t.http_getir(url, site, zaman_asimi)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception:
        return None, ""


_JSONLD = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.S | re.I)
_OG_TITLE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_TL = re.compile(r"(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+)\s*(?:TL|₺)", re.I)
_STOKSUZ = ("stokta yok", "tükendi", "tukendi", "satışta değil", "satista degil")


def genel_ayristir(html: str, url: str = "", yontem: str = "http") -> Kart | None:
    """Bilinmeyen mağaza ürün sayfası: JSON-LD Product/Offer + og:title + fiyat kalıbı.

    Dönüş: Kart | None. Saf ayrıştırıcı (ağa çıkmaz, testler için).
    """
    if not html:
        return None
    ad = ""
    fiyat = None
    stok = None
    satici = None
    offers = []
    for m in _JSONLD.finditer(html):
        d = json_coz(m.group(1))
        for n in (d if isinstance(d, list) else [d]):
            if not isinstance(n, dict):
                continue
            tur = str(n.get("@type") or "")
            if tur not in ("Product", "product"):
                continue
            ad = ad or str(n.get("name") or "").strip()
            o = n.get("offers")
            if isinstance(o, dict):
                offers.append(o)
            elif isinstance(o, list):
                offers.extend(x for x in o if isinstance(x, dict))
    for o in offers:
        f = tl_sayi(o.get("price") or o.get("lowPrice"))
        if f and f > 0 and (fiyat is None or f < fiyat):
            fiyat = f
        av = str(o.get("availability") or "").lower()
        if av:
            stok = "outofstock" not in av and "soldout" not in av
        s = o.get("seller")
        if isinstance(s, dict) and s.get("name"):
            satici = str(s.get("name")).strip()
    if not ad:
        m = _OG_TITLE.search(html) or _TITLE.search(html)
        if m:
            ad = re.sub(r"\s*[-|]\s*[^-|]{1,40}$", "", m.group(1)).strip()
    if fiyat is None:
        for m in _TL.finditer(re.sub(r"<[^>]+>", " ", html)):
            f = tl_sayi(m.group(0))
            if f and f > 0:
                fiyat = f
                break
    if stok is None:
        kucuk = html.lower()
        if any(s in kucuk for s in _STOKSUZ):
            stok = False
    if not ad or not fiyat:
        return None
    return Kart(
        site=site_adi(url) or "magaza",
        baslik=ad,
        sepet_fiyati=fiyat,
        url=url,
        satici=satici,
        stok=stok,
        yontem=yontem,
        pesin_taksit=taksit_metinden(html),
    )


def genel_urun(t, url: str) -> Kart | None:
    """Listede olmayan mağaza için ürün sayfası oku (JSON-LD + metin)."""
    y = t.getir(url)
    return genel_ayristir(y.html, url=url, yontem=y.yontem)


def http_ya_da_site_ici(t, site: str, ana_url: str, arama_url: str,
                        sorgu: str, kutu_secicileri=(),
                        sonuc_bekle_secici: str | None = None):
    """HTTP arama → başarısızsa Brave site içi arama (§2.7).

    Dönüş: (html, yontem). yontem "http" ya da "brave".
    HTTP 200 + blok yoksa doğrudan döner; aksi halde doğrudan arama URL'sini
    Brave'de AÇMAZ, ana sayfa + arama kutusu akışını kullanır.
    """
    durum, html = http_ara(t, site, arama_url)
    if durum == 200 and not blok_mu(html):
        try:
            t._basari(site, "http")
        except Exception:
            pass
        return html, "http"

    # HTTP kademesi başarısız: durumu kaydet, sonra site içi arama.
    try:
        t._hata(site, f"HTTP {durum} arama; site ici aramaya dusuldu",
                http_blok=durum in (403, 429, 503))
    except Exception:
        pass
    html, _yanitlar = t.site_ici_ara(site, ana_url, sorgu,
                                     kutu_secicileri=kutu_secicileri,
                                     sonuc_bekle_secici=sonuc_bekle_secici)
    return html, "brave"
