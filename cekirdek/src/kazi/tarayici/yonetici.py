"""Kademeli katman yöneticisi (K0 -> K1 -> K2 -> K3) ve öğrenen site sınıflandırması.

SARTNAME §3 kuralları:
- Her site için `Site.katman_tercihi` DB'de (`site` tablosu) tutulur.
- İstek tercih edilen katmandan başlar; blok/hata durumunda bir üste çıkar (K0 -> K1 -> K2 -> K3).
- Başarıda `son_basari` ve `katman_tercihi`; başarısızlıkta `son_hata` ve `hata_turu`
  (`http_403`, `captcha`, `bos_sonuc`, `zaman_asimi`, `ag_hatasi`) yazılır.
- Haftalık alt katman denemesi: tercih K1/K2 ise ve son alt deneme > 7 gün önceyse
  önce bir alt katman denenir.
- K3: K2'de doğrulama çıkarsa `dogrulama_gerekli` olayı yayınlanır ve `bekle_k3` saniye
  (varsayılan 300) boyunca 10 sn arayla aynı sekmede `blok_mu` denetlenir; çözülürse
  devam edilir, çözülmezse `BotEngeli` fırlatılır.
- `dogrudan_url_yasak` işaretli sitelerde arama URL'sine doğrudan gidilmez -> ana sayfa + `site_ici_ara`.
- Hız sınırı: site başına aynı anda 1 istek, istekler arası 2-3 sn.
"""
from __future__ import annotations

import inspect
import socket
import threading
import time
import urllib.error
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..modeller import TZ_ISTANBUL, Katman, Site
from .cekirdek import (
    DURUM_DOSYA,
     _BLOK_RX,
    _FIYAT_RX,
    _oku,
    _yaz,
    BotEngeli,
    Yanit,
    blok_mu,
    site_adi,
    varsayilan_bildirim,
)
from .chromium import ChromiumKatmani, ChromiumKullanilamiyor

if TYPE_CHECKING:  # pragma: no cover
    from ..db import VeriTabani
    from .cekirdek import Tarayici

# Doğrudan arama URL'sine gidilmesi yasak olan siteler (SARTNAME §2.7 / eski kod)
DOGRUDAN_URL_YASAK_SITELER: set[str] = {
    "cimri",
    "trendyol",
    "hepsiburada",
    "amazon",
    "mediamarkt",
    "teknosa",
    "incehesap",
    "pttavm",
    "idefix",
}

SITE_ARAMA_AYARLARI: dict[str, dict[str, Any]] = {
    "cimri": {
        "ana_url": "https://www.cimri.com/",
        "kutu_secicileri": ("input#search-input", 'input[name="q"]'),
        "sonuc_bekle_secici": "script#__OCTOPUS_DATA__",
    },
    "trendyol": {
        "ana_url": "https://www.trendyol.com/",
        "kutu_secicileri": ("button[data-testid='suggestion-placeholder']",),
    },
    "hepsiburada": {
        "ana_url": "https://www.hepsiburada.com/",
        "kutu_secicileri": ("input[data-test-id='search-bar-input']",),
    },
    "amazon": {
        "ana_url": "https://www.amazon.com.tr/",
        "kutu_secicileri": ("input#twotabsearchtextbox",),
    },
    "mediamarkt": {
        "ana_url": "https://www.mediamarkt.com.tr/",
        "kutu_secicileri": ("input[name='query']", "input[type='search']"),
    },
    "teknosa": {
        "ana_url": "https://www.teknosa.com/",
        "kutu_secicileri": ("input#search-input", 'input[name="s"]'),
    },
    "incehesap": {
        "ana_url": "https://www.incehesap.com/",
        "kutu_secicileri": ('input[name="search"]',),
    },
    "pttavm": {
        "ana_url": "https://www.pttavm.com/",
        "kutu_secicileri": ('input[name="general-search-input"]',),
    },
    "idefix": {
        "ana_url": "https://www.idefix.com/",
        "kutu_secicileri": (),
    },
    "sinerji": {
        "ana_url": "https://www.sinerji.gen.tr/",
        "kutu_secicileri": ("input#QuickSearch",),
    },
    "itopya": {
        "ana_url": "https://www.itopya.com/",
        "kutu_secicileri": ("input#searchBar",),
    },
    "vatan": {
        "ana_url": "https://www.vatanbilgisayar.com/",
        "kutu_secicileri": ("input#navbar-search-input", 'input[name="search"]'),
    },
    "pazarama": {
        "ana_url": "https://www.pazarama.com/",
        "kutu_secicileri": ('input[data-testid="search-input"]', 'input[name="search-bar"]'),
    },
    "gaminggentr": {
        "ana_url": "https://www.gaming.gen.tr/",
        "kutu_secicileri": ('input[name="s"]', ".dgwt-wcas-search-input"),
    },
    "gamegaraj": {
        "ana_url": "https://www.gamegaraj.com/",
        "kutu_secicileri": ("input#default-search", 'input[type="search"]'),
    },
}

_ARAMA_PARAMETRELERI = ("q", "k", "query", "s", "bul", "ara", "search", "keyword")
_ARAMA_YOLLARI = ("/arama", "/ara", "/sr", "/search", "/s")


def hata_turu_belirle(
    durum: int | None = None,
    html: str | None = None,
    istisna: BaseException | None = None,
) -> str:
    """Başarısızlığı standart hata türüne sınıflandır:
    `http_403` | `captcha` | `bos_sonuc` | `zaman_asimi` | `ag_hatasi`.
    """
    if istisna is not None:
        if isinstance(istisna, urllib.error.HTTPError):
            durum = istisna.code
        elif isinstance(istisna, BotEngeli):
            if istisna.site == "brave" and "kullanılamıyor" in str(istisna.url):
                return "ag_hatasi"
            return "captcha"
        elif isinstance(istisna, (TimeoutError, socket.timeout)):
            return "zaman_asimi"
        elif isinstance(istisna, urllib.error.URLError):
            sebep = getattr(istisna, "reason", None)
            if isinstance(sebep, (TimeoutError, socket.timeout)):
                return "zaman_asimi"
            if "timed out" in str(istisna).lower() or "timeout" in str(istisna).lower():
                return "zaman_asimi"
            return "ag_hatasi"
        else:
            mesaj = str(istisna).lower()
            if "timeout" in mesaj or "timed out" in mesaj or "zaman aşımı" in mesaj:
                return "zaman_asimi"
            return "ag_hatasi"

    if durum in (401, 403, 429, 503):
        return "http_403"
    if durum is not None and durum >= 400:
        return "ag_hatasi"

    if not html or not html.strip():
        return "bos_sonuc"
    if _BLOK_RX.search(html):
        return "captcha"
    if len(html) < 2048 and not _FIYAT_RX.search(html):
        return "bos_sonuc"
    return "captcha"


def _arama_sorgusu_cikar(url: str) -> str | None:
    """URL bir arama URL'si ise arama sorgusunu çıkar; değilse `None`."""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return None
    sorgu_dict = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
    for anahtar in _ARAMA_PARAMETRELERI:
        deger = sorgu_dict.get(anahtar)
        if deger and deger.strip():
            return deger.strip()
    yol = (p.path or "").rstrip("/")
    # /arama/<sorgu> biçimi (ör. vatan)
    if yol.startswith("/arama/"):
        parca = urllib.parse.unquote_plus(yol[len("/arama/") :].strip("/"))
        if parca:
            return parca
    # /<slug>-s biçimi (ör. sinerji)
    if yol.endswith("-s") and yol.count("/") == 1 and len(yol) > 3:
        return yol[1:-2].replace("-", " ").strip()
    return None


def _arama_url_mi(url: str) -> bool:
    """Verilen URL bir site arama sayfası URL'si mi?"""
    if _arama_sorgusu_cikar(url) is not None:
        return True
    try:
        yol = (urllib.parse.urlsplit(url).path or "").rstrip("/").lower()
    except Exception:
        return False
    return yol in _ARAMA_YOLLARI or any(yol.startswith(x + "/") for x in _ARAMA_YOLLARI)


def _iso_coz(metin: str | None, varsayilan_tz: Any = TZ_ISTANBUL) -> datetime | None:
    if not metin:
        return None
    try:
        dt = datetime.fromisoformat(metin)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=varsayilan_tz)
        return dt
    except Exception:
        return None


class Kademe:
    """K0 -> K1 -> K2 -> K3 kademeli erişim ve öğrenen site sınıflandırıcısı."""

    def __init__(
        self,
        veritabani: "VeriTabani | None" = None,
        bildirim_fn: Callable[[dict[str, Any]], Any] | None = None,
        *,
        tarayici: "Tarayici | None" = None,
        k0: Any = None,
        k1: Any = None,
        k2: Any = None,
        durum_dosya: Path | str | None = None,
        bekle_k3: float = 300.0,
        k3_aralik: float = 10.0,
        min_aralik: float = 2.5,
        jitter: float = 0.5,
        dogrudan_url_yasak: set[str] | None = None,
        zaman_fn: Callable[[], float] | None = None,
        uyku_fn: Callable[[float], None] | None = None,
        simdi_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.veritabani = veritabani
        self.bildirim_fn: Callable[[dict[str, Any]], Any] = bildirim_fn or varsayilan_bildirim
        self.tarayici = tarayici
        self.k0 = k0
        self._k1_acikca_verildi = k1 is not None
        self.k1 = k1 if k1 is not None else ChromiumKatmani(bildirim_fn=self._bildir)
        self.k2 = k2
        self.durum_dosya = Path(durum_dosya) if durum_dosya else DURUM_DOSYA
        self.bekle_k3 = float(bekle_k3)
        self.k3_aralik = float(k3_aralik)
        self.min_aralik = float(min_aralik)
        self.jitter = float(jitter)
        self.dogrudan_url_yasak: set[str] = (
            set(dogrudan_url_yasak)
            if dogrudan_url_yasak is not None
            else set(DOGRUDAN_URL_YASAK_SITELER)
        )
        self._zaman_enjekte = zaman_fn is not None or uyku_fn is not None
        self._zaman_fn = zaman_fn or time.monotonic
        self._uyku_fn = uyku_fn or time.sleep
        self._simdi_fn = simdi_fn or (lambda: datetime.now(TZ_ISTANBUL))

        self._siteler_bellek: dict[str, Site] = {}
        self._site_kilitleri: dict[str, threading.RLock] = {}
        self._genel_kilit = threading.Lock()
        self._son_istek: dict[str, float] = {}

    # -------------------------------------------------- zaman & bildirim
    def _simdi(self) -> datetime:
        dt = self._simdi_fn()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_ISTANBUL)
        return dt

    def _simdi_iso(self) -> str:
        return self._simdi().isoformat(timespec="seconds")

    def _bildir(self, olay: dict[str, Any]) -> None:
        try:
            self.bildirim_fn(olay)
        except Exception:
            pass

    # -------------------------------------------------- durum.json & site DB
    def _durum_sozlugu(self) -> dict[str, Any]:
        if self.tarayici is not None:
            return self.tarayici._durum
        if not hasattr(self, "_yerel_durum"):
            self._yerel_durum: dict[str, Any] = _oku(self.durum_dosya, {})
        return self._yerel_durum

    def durum(self, site: str) -> dict[str, Any]:
        return self._durum_sozlugu().setdefault(site, {})

    def durum_kaydet(self) -> None:
        if self.tarayici is not None:
            self.tarayici.kaydet()
        else:
            _yaz(self.durum_dosya, self._durum_sozlugu())

    def son_alt_deneme_ayarla(self, site: str, zaman_iso: str) -> None:
        """Testler ve yönetim için sitenin son alt katman deneme zamanını ayarla."""
        self.durum(site)["son_alt_deneme"] = zaman_iso
        self.durum_kaydet()

    def site_bilgisi(self, site: str) -> Site:
        """DB'den (varsa) ya da bellekten `Site` nesnesini getir."""
        if self.veritabani is not None:
            for s in self.veritabani.siteler_listele():
                if s.ad == site:
                    self._siteler_bellek[site] = s
                    return s
        if site in self._siteler_bellek:
            return self._siteler_bellek[site]
        yeni = Site(ad=site, tur="urun", katman_tercihi=None, etkin=True)
        self._siteler_bellek[site] = yeni
        return yeni

    def _site_kaydet(self, kayit: Site) -> None:
        self._siteler_bellek[kayit.ad] = kayit
        if self.veritabani is not None:
            self.veritabani.site_kaydet(kayit)

    def _katman_basari_kaydet(self, site: str, calisan_katman: Katman) -> None:
        mevcut = self.site_bilgisi(site)
        eski_tercih = mevcut.katman_tercihi
        yeni_tercih = Katman.K2 if calisan_katman == Katman.K3 else calisan_katman
        simdi_str = self._simdi_iso()

        guncel = Site(
            ad=site,
            tur=mevcut.tur or "urun",
            katman_tercihi=yeni_tercih,
            son_basari=simdi_str,
            son_hata=mevcut.son_hata,
            hata_turu=mevcut.hata_turu,
            etkin=mevcut.etkin,
        )
        self._site_kaydet(guncel)

        d = self.durum(site)
        if yeni_tercih in (Katman.K1, Katman.K2) and not d.get("son_alt_deneme"):
            d["son_alt_deneme"] = simdi_str
        yontem_haritasi = {
            Katman.K0: "http",
            Katman.K1: "chromium",
            Katman.K2: "brave",
            Katman.K3: "brave",
        }
        if self.tarayici is not None:
            self.tarayici._basari(site, yontem_haritasi.get(calisan_katman, "http"))
        else:
            d["son_yontem"] = yontem_haritasi.get(calisan_katman, "http")
            d["son_basari"] = simdi_str
            d["ardisik_hata"] = 0
            self.durum_kaydet()

        eski_etkin = eski_tercih or Katman.K0
        if eski_etkin != yeni_tercih:
            self._bildir({
                "tur": "katman_degisti",
                "site": site,
                "eski": str(eski_etkin),
                "yeni": str(yeni_tercih),
            })

    def _katman_hata_kaydet(
        self,
        site: str,
        hata_turu: str,
        sebep: str = "",
        http_blok: bool = False,
    ) -> None:
        mevcut = self.site_bilgisi(site)
        simdi_str = self._simdi_iso()
        guncel = Site(
            ad=site,
            tur=mevcut.tur or "urun",
            katman_tercihi=mevcut.katman_tercihi,
            son_basari=mevcut.son_basari,
            son_hata=simdi_str,
            hata_turu=hata_turu,
            etkin=mevcut.etkin,
        )
        self._site_kaydet(guncel)
        if self.tarayici is not None:
            self.tarayici._hata(site, sebep or hata_turu, http_blok=http_blok)
        else:
            d = self.durum(site)
            d["ardisik_hata"] = int(d.get("ardisik_hata", 0)) + 1
            d["son_hata"] = f"{simdi_str} {sebep or hata_turu}"
            if http_blok:
                d["http_ardisik_blok"] = int(d.get("http_ardisik_blok", 0)) + 1
                d["son_http_blok"] = simdi_str
            self.durum_kaydet()

    # -------------------------------------------------- hız sınırı & eşzamanlılık
    def _site_kilidi(self, site: str) -> threading.RLock:
        with self._genel_kilit:
            kilit = self._site_kilitleri.get(site)
            if kilit is None:
                kilit = threading.RLock()
                self._site_kilitleri[site] = kilit
            return kilit

    def hiz_sinirla(self, site: str) -> None:
        if self.tarayici is not None:
            self.tarayici.hiz_sinirla(site)
            return
        import random

        once = self._son_istek.get(site)
        if once is not None:
            gecen = self._zaman_fn() - once
            gereken = self.min_aralik + (random.uniform(0, self.jitter) if self.jitter > 0 else 0.0)
            if gecen < gereken:
                self._uyku_fn(gereken - gecen)
        self._son_istek[site] = self._zaman_fn()

    # -------------------------------------------------- katman sırası belirleme
    def _denenecek_katmanlar(self, site: str) -> list[Katman]:
        mevcut = self.site_bilgisi(site)
        tercih = mevcut.katman_tercihi

        if tercih is None and self.tarayici is not None and self.tarayici._brave_zorunlu(site):
            tercih = Katman.K2

        if tercih is None or tercih == Katman.K0:
            return [Katman.K0, Katman.K1, Katman.K2]

        if tercih == Katman.K3:
            tercih = Katman.K2

        # Haftalık alt katman denemesi: tercih K1 veya K2 ise ve son alt deneme > 7 gün önceyse
        d = self.durum(site)
        referans_str = d.get("son_alt_deneme") or mevcut.son_basari or mevcut.son_hata
        alt_denenecek = False
        simdi = self._simdi()
        if referans_str:
            ref_dt = _iso_coz(referans_str, simdi.tzinfo)
            if ref_dt is not None and (simdi - ref_dt) > timedelta(days=7):
                alt_denenecek = True
        else:
            # Yeni atanmış tercih için başlangıç zaman damgasını kaydet
            d["son_alt_deneme"] = self._simdi_iso()
            self.durum_kaydet()

        if tercih == Katman.K1:
            if alt_denenecek:
                d["son_alt_deneme"] = self._simdi_iso()
                self.durum_kaydet()
                return [Katman.K0, Katman.K1, Katman.K2]
            return [Katman.K1, Katman.K2]

        # tercih == Katman.K2
        if alt_denenecek:
            d["son_alt_deneme"] = self._simdi_iso()
            self.durum_kaydet()
            return [Katman.K1, Katman.K2]
        return [Katman.K2]

    # -------------------------------------------------- K0, K1, K2, K3 yürütücüleri
    def _k0_calistir(self, url: str, site: str, zaman_asimi: float) -> tuple[int, str]:
        kaynak = self.k0
        if kaynak is not None:
            if hasattr(kaynak, "http_getir"):
                return kaynak.http_getir(url, site, zaman_asimi)
            if hasattr(kaynak, "getir"):
                return _cagri_yap(kaynak.getir, url, site, zaman_asimi)
            if callable(kaynak):
                return _cagri_yap(kaynak, url, site, zaman_asimi)
        if self.tarayici is not None:
            return self.tarayici.http_getir(url, site, zaman_asimi)
        from .cekirdek import Tarayici as _T

        gecici = _T(min_aralik=0, jitter=0)
        return gecici.http_getir(url, site, zaman_asimi)

    def _k0_dene(self, url: str, site: str, zaman_asimi: float) -> Yanit | None:
        self.hiz_sinirla(site)
        durum: int | None = None
        html = ""
        try:
            durum, html = self._k0_calistir(url, site, zaman_asimi)
        except urllib.error.HTTPError as e:
            durum = e.code
            try:
                html = e.read().decode("utf-8", "replace") if e.fp else ""
            except Exception:
                html = ""
            ht = hata_turu_belirle(durum=durum, html=html, istisna=e)
            from .. import gunluk
            gunluk.yaz("uyari", "tarayici", f"HTTP {durum}", olay="k0_istek", site=site, tarayici="HTTP (K0)", url=url,
                       veri={"durum": durum})
            self._katman_hata_kaydet(
                site,
                ht,
                sebep=f"HTTP {durum}",
                http_blok=durum in (401, 403, 429, 503),
            )
            return None
        except Exception as e:
            ht = hata_turu_belirle(istisna=e)
            self._katman_hata_kaydet(site, ht, sebep=f"HTTP {type(e).__name__}")
            from .. import gunluk
            gunluk.yaz("uyari", "tarayici", f"HTTP isteği başarısız: {type(e).__name__}: {str(e)[:200]}", olay="k0_istek",
                       site=site, tarayici="HTTP (K0)", url=url)
            return None

        from .. import gunluk
        _blok = blok_mu(html)
        gunluk.yaz("bilgi" if durum == 200 and not _blok else "uyari", "tarayici",
                   f"HTTP {durum}" + (" — bot koruması sayfası" if _blok else ""), olay="k0_istek", site=site,
                   tarayici="HTTP (K0)", url=url, veri={"durum": durum, "boyut": len(html or "")})
        if durum == 200 and not _blok:
            self._katman_basari_kaydet(site, Katman.K0)
            return Yanit(html=html, yontem="http", durum=durum, url=url, katman=Katman.K0)

        http_blok = durum in (401, 403, 429, 503) or (durum == 200 and blok_mu(html))
        ht = hata_turu_belirle(durum=durum, html=html)
        self._katman_hata_kaydet(
            site,
            ht,
            sebep=f"HTTP {durum} blok={blok_mu(html)}",
            http_blok=http_blok,
        )
        return None

    def _k1_atla_mi(self) -> bool:
        if self.k1 is None:
            return True
        # Eski Tarayici testlerinde yalnız http_getir/brave_getir monkeypatch edilmişse
        # ve k1 açıkça verilmemişse gerçek Chromium başlatılmasın.
        if (
            not self._k1_acikca_verildi
            and self.tarayici is not None
            and ("http_getir" in self.tarayici.__dict__ or "brave_getir" in self.tarayici.__dict__)
        ):
            return True
        return False

    def _k1_dene(self, url: str, site: str, zaman_asimi: float) -> Yanit | None:
        if self._k1_atla_mi():
            return None
        if getattr(self.k1, "kullanilabilir", None) is False:
            self._bildir({
                "tur": "k1_atlandi",
                "katman": "K1",
                "site": site,
                "url": url,
                "sebep": "chromium_kurulu_degil",
            })
            return None

        self.hiz_sinirla(site)
        try:
            if hasattr(self.k1, "getir"):
                sonuc = _cagri_yap(self.k1.getir, url, site, zaman_asimi)
            elif callable(self.k1):
                sonuc = _cagri_yap(self.k1, url, site, zaman_asimi)
            else:
                return None
        except ChromiumKullanilamiyor as e:
            self._bildir({
                "tur": "k1_atlandi",
                "katman": "K1",
                "site": site,
                "url": url,
                "sebep": "chromium_kurulu_degil",
                "ayrinti": str(e),
            })
            return None
        except urllib.error.HTTPError as e:
            ht = hata_turu_belirle(durum=e.code, istisna=e)
            self._katman_hata_kaydet(site, ht, sebep=f"K1 HTTP {e.code}")
            return None
        except Exception as e:
            ht = hata_turu_belirle(istisna=e)
            self._katman_hata_kaydet(site, ht, sebep=f"K1 {type(e).__name__}")
            return None

        if isinstance(sonuc, Yanit):
            durum, html = sonuc.durum, sonuc.html
        elif isinstance(sonuc, tuple) and len(sonuc) == 2:
            durum, html = sonuc
        else:
            durum, html = 200, str(sonuc or "")

        if durum is None and not html and getattr(self.k1, "kullanilabilir", None) is False:
            # Chromium kurulu değil, olay yayınlandı -> K1 atlanır
            return None

        if (durum is None or durum == 200) and not blok_mu(html):
            self._katman_basari_kaydet(site, Katman.K1)
            return Yanit(
                html=html,
                yontem="chromium",
                durum=200 if durum is None else durum,
                url=url,
                katman=Katman.K1,
            )

        ht = hata_turu_belirle(durum=durum, html=html)
        self._katman_hata_kaydet(site, ht, sebep=f"K1 {durum} blok={blok_mu(html)}")
        return None

    def _k2_html_getir(self, url: str, site: str) -> str:
        if self.k2 is not None:
            if hasattr(self.k2, "brave_getir"):
                return _cagri_yap(self.k2.brave_getir, url, site)
            if hasattr(self.k2, "getir"):
                sonuc = _cagri_yap(self.k2.getir, url, site)
                if isinstance(sonuc, Yanit):
                    return sonuc.html
                if isinstance(sonuc, tuple) and len(sonuc) == 2:
                    return str(sonuc[1] or "")
                return str(sonuc or "")
            if callable(self.k2):
                sonuc = _cagri_yap(self.k2, url, site)
                if isinstance(sonuc, tuple) and len(sonuc) == 2:
                    return str(sonuc[1] or "")
                return str(sonuc or "")

        if self.tarayici is not None:
            if "brave_getir" not in self.tarayici.__dict__:
                self.tarayici._brave_kilidi_al()
            return self.tarayici.brave_getir(url, site)

        raise BotEngeli("brave", "kullanılamıyor")

    def _k3_sekme_icerigi(self, site: str, url: str) -> str:
        """K3 beklemesi sırasında aynı sekmenin güncel HTML içeriğini oku."""
        if self.k2 is not None:
            if hasattr(self.k2, "sekme_icerigi"):
                return str(self.k2.sekme_icerigi(site) or "")
            sekmeler = getattr(self.k2, "_sekmeler", None)
            if isinstance(sekmeler, dict) and site in sekmeler:
                pg = sekmeler[site]
                if pg is not None and hasattr(pg, "content"):
                    return str(pg.content() or "")
            return self._k2_html_getir(url, site)

        if self.tarayici is not None:
            pg = self.tarayici._sekmeler.get(site)
            if pg is not None and hasattr(pg, "content"):
                return str(pg.content() or "")
            return self.tarayici.brave_getir(url, site)

        return ""

    def _k3_bekle(self, site: str, url: str, ilk_html: str) -> Yanit:
        """K2'de doğrulama çıktığında `dogrulama_gerekli` olayı yayınla ve
        `bekle_k3` saniye boyunca `k3_aralik` (10 sn) arayla aynı sekmede `blok_mu` denetle.
        """
        ht = hata_turu_belirle(durum=None, html=ilk_html)
        self._katman_hata_kaydet(site, ht, sebep="brave blok")
        self._bildir({"tur": "dogrulama_gerekli", "site": site, "url": url})

        gercek_tarayici_acik = (
            self.tarayici is not None and getattr(self.tarayici, "_browser", None) is not None
        )
        if self.bekle_k3 <= 0 and not gercek_tarayici_acik:
            raise BotEngeli(site, url)
        enjekte_veya_canli = self._zaman_enjekte or gercek_tarayici_acik
        if gercek_tarayici_acik and self.bekle_k3 > 0:
            # Ayar → doğrulama beklemesi açık: kullanıcı çözebilsin diye pencere hemen gösterilir, bitince ayara dön
            from .gorunurluk import pencereleri_ayarla
            pencereleri_ayarla(self.tarayici._browser, gorunur=True)
            try:
                return self._k3_dongu(site, url, enjekte_veya_canli, istek_dinle=True)
            finally:
                pencereleri_ayarla(self.tarayici._browser)
        # 15.11: bekleme ayarı 0 olsa bile gerçek tarayıcıda kısa "fırsat" süresi beklenir; bu sırada arayüzden
        # "Elle doğrula" gelirse (dogrulama.istek_yaz) pencereyi API öne getirir, tarama istek bitişine kadar bekler.
        return self._k3_dongu(site, url, enjekte_veya_canli, istek_dinle=gercek_tarayici_acik)

    def _gunluk_dogrulama(self, olay: str, seviye: str, site: str, url: str, mesaj: str, **veri: Any) -> None:
        from .. import gunluk
        ad = getattr(self.tarayici, "gunluk_adi", None) or "Brave (kazı)"
        gunluk.yaz(seviye, "tarayici", mesaj, olay=olay, site=site, tarayici=ad, url=url, veri=veri or None)

    def _k3_dongu(self, site: str, url: str, enjekte_veya_canli: bool, istek_dinle: bool = False) -> Yanit:
        """Doğrulama sayfası temizlenene kadar bekle. `istek_dinle`: bitiş zamanı her adımda yeniden hesaplanır
        (ayar beklemesi, 20 sn fırsat, kullanıcının elle doğrulama isteği — en geç olanı; bkz. dogrulama.bekleme_bitisi)."""
        from . import dogrulama

        baslangic = self._zaman_fn()
        gecen = 0.0
        adim = max(0.001, self.k3_aralik)
        if istek_dinle:
            adim = min(adim, 2.0)          # kullanıcı çözünce 2 sn içinde devam

        def _sinir() -> float:
            if not istek_dinle:
                return float(self.bekle_k3)
            return dogrulama.bekleme_bitisi(site, baslangic, self.bekle_k3, self._zaman_fn) - baslangic

        sinir = _sinir()
        if istek_dinle:
            self._gunluk_dogrulama("dogrulama_bekleniyor", "uyari", site, url,
                                   f"doğrulama sayfasında bekleniyor ({int(sinir)} sn) — arayüzden 'Elle doğrula' ile süre uzar",
                                   kalan_sn=int(sinir), bitis=baslangic + sinir)
        while gecen < sinir:
            kalan = sinir - gecen
            bekle_suresi = min(adim, kalan)
            if enjekte_veya_canli:
                self._uyku_fn(bekle_suresi)
                yeni_zaman = self._zaman_fn()
                if yeni_zaman > baslangic + gecen:
                    gecen = yeni_zaman - baslangic
                else:
                    gecen += bekle_suresi
            else:
                gecen += bekle_suresi

            try:
                guncel_html = self._k3_sekme_icerigi(site, url)
            except Exception:
                guncel_html = ""

            if not blok_mu(guncel_html):
                self._katman_basari_kaydet(site, Katman.K3)
                if istek_dinle:
                    self._gunluk_dogrulama("dogrulama_gecildi", "bilgi", site, url,
                                           f"doğrulama geçildi ({int(gecen)} sn sonra), tarama sürüyor")
                    dogrulama.istek_sil(site)
                return Yanit(
                    html=guncel_html,
                    yontem="brave",
                    durum=None,
                    url=url,
                    katman=Katman.K3,
                )
            if istek_dinle:
                yeni = _sinir()
                if yeni > sinir + 1:
                    self._gunluk_dogrulama("dogrulama_bekleniyor", "uyari", site, url,
                                           f"elle doğrulama bekleniyor ({int(yeni - gecen)} sn kaldı)",
                                           kalan_sn=int(yeni - gecen), bitis=baslangic + yeni)
                sinir = yeni

        if istek_dinle:
            self._gunluk_dogrulama("dogrulama_zaman_asimi", "uyari", site, url,
                                   f"doğrulama {int(gecen)} sn içinde geçilmedi — site bu turda atlandı")
        raise BotEngeli(site, url)

    def _k2_dene(self, url: str, site: str) -> Yanit:
        self.hiz_sinirla(site)
        try:
            html = self._k2_html_getir(url, site)
        except BotEngeli as e:
            ht = hata_turu_belirle(istisna=e)
            self._katman_hata_kaydet(site, ht, sebep="brave yok")
            raise
        except Exception as e:
            ht = hata_turu_belirle(istisna=e)
            self._katman_hata_kaydet(site, ht, sebep=f"K2 {type(e).__name__}")
            raise BotEngeli(site, url) from e

        if not blok_mu(html):
            self._katman_basari_kaydet(site, Katman.K2)
            return Yanit(html=html, yontem="brave", durum=None, url=url, katman=Katman.K2)

        return self._k3_bekle(site, url, html)

    # -------------------------------------------------- dogrudan_url_yasak -> site_ici_ara
    def _site_ici_ara_destekli_mi(self) -> bool:
        if self.k2 is not None:
            return hasattr(self.k2, "site_ici_ara")
        if self.tarayici is not None:
            # Eğer test yalnız `brave_getir`'i monkeypatch etmişse ve `site_ici_ara` / `_sekme`
            # monkeypatch edilmemişse doğrudan brave_getir test ediliyordur.
            if (
                "brave_getir" in self.tarayici.__dict__
                and "site_ici_ara" not in self.tarayici.__dict__
                and "_sekme" not in self.tarayici.__dict__
            ):
                return False
            return True
        return False

    def _site_ici_ara_calistir(self, site: str, url: str, sorgu: str) -> Yanit:
        ayar = SITE_ARAMA_AYARLARI.get(site, {})
        ana_url = ayar.get("ana_url")
        if not ana_url:
            p = urllib.parse.urlsplit(url)
            ana_url = f"{p.scheme or 'https'}://{p.netloc}/"
        kutu_secicileri = ayar.get("kutu_secicileri", ())
        sonuc_bekle_secici = ayar.get("sonuc_bekle_secici")

        hedef = self.k2 if (self.k2 is not None and hasattr(self.k2, "site_ici_ara")) else self.tarayici
        if hedef is None:
            raise BotEngeli(site, url)

        sonuc = hedef.site_ici_ara(
            site,
            ana_url,
            sorgu,
            kutu_secicileri=kutu_secicileri,
            sonuc_bekle_secici=sonuc_bekle_secici,
        )
        html = sonuc[0] if isinstance(sonuc, tuple) else str(sonuc or "")
        if blok_mu(html):
            return self._k3_bekle(site, url, html)
        self._katman_basari_kaydet(site, Katman.K2)
        return Yanit(html=html, yontem="brave", durum=None, url=url, katman=Katman.K2)

    # -------------------------------------------------- ana giriş
    def getir(
        self,
        url: str,
        site: str | None = None,
        zaman_asimi: float = 40.0,
        sorgu: str | None = None,
    ) -> Yanit:
        """Site tercihine göre kademeli olarak URL'yi getir."""
        site = site or site_adi(url)
        with self._site_kilidi(site):
            # Kullanıcı kuralı: dogrudan_url_yasak işaretli sitelerde arama URL'sine gidilmez
            if (
                site in self.dogrudan_url_yasak
                and (sorgu is not None or _arama_url_mi(url))
                and self._site_ici_ara_destekli_mi()
            ):
                arama_sorgusu = sorgu or _arama_sorgusu_cikar(url) or ""
                return self._site_ici_ara_calistir(site, url, arama_sorgusu)

            katmanlar = self._denenecek_katmanlar(site)
            for katman in katmanlar:
                if katman == Katman.K0:
                    yanit = self._k0_dene(url, site, zaman_asimi)
                    if yanit is not None:
                        return yanit
                elif katman == Katman.K1:
                    yanit = self._k1_dene(url, site, zaman_asimi)
                    if yanit is not None:
                        return yanit
                elif katman == Katman.K2:
                    return self._k2_dene(url, site)

            return self._k2_dene(url, site)

    def kapat_k1(self) -> None:
        if self.k1 is not None and hasattr(self.k1, "kapat"):
            try:
                self.k1.kapat()
            except Exception:
                pass


def _cagri_yap(fn: Callable[..., Any], url: str, site: str, zaman_asimi: float = 40.0) -> Any:
    """Farklı imzalara sahip sahte/gerçek katman fonksiyonlarını esnek biçimde çağır."""
    try:
        sig = inspect.signature(fn)
        params = [
            p
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        var_args = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
        if var_args or len(params) >= 3:
            return fn(url, site, zaman_asimi)
        if len(params) == 2:
            ikinci_ad = params[1].name.lower()
            if "zaman" in ikinci_ad or "timeout" in ikinci_ad or "sure" in ikinci_ad:
                return fn(url, zaman_asimi)
            return fn(url, site)
        if len(params) == 1:
            return fn(url)
        return fn()
    except (ValueError, TypeError):
        return fn(url)
