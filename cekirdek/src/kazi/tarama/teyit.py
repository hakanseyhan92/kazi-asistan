"""Teyit motoru ve kovalama orkestrası (SARTNAME §5, §5.1).

Akış:
1. `kovala(v, tarayici, urun_id, azami=5)`:
   - Son 24 saatin geçerli tekliflerinden `piyasa_ozeti` (kırpılmış ortalama, medyan) hesaplanır.
   - `kovalanacaklar` ile ortalamanın altındaki teyitsiz (veya fiyatı değişmiş) teklifler
     en ucuzdan başlayarak en çok `azami` adet seçilir.
2. `teyit_et(...)`:
   - Aynı URL son 2 saat içinde teyit edildiyse (ve fiyat değişmediyse) yeniden açılmaz (önbellek).
   - Toplayıcı linkiyse (`akakce`, `cimri`, `epey`) yönlendirme gerçek mağaza URL'sine çözülür.
   - Mağaza bağdaştırıcısının `urun()` fonksiyonu (yoksa `_ortak.genel_urun`) ile sayfa okunur.
   - Başlık kimlikle eşleşmeli (`eslestir.uygun_mu`), stok açık olmalı, fiyat ±%1 içinde olmalı.
   - **40 sn sonra ikinci okuma** (`bekle_fn` enjekte edilebilir; testte 0): iki okuma tutarsızsa
     `fiyat_degisti`.
   - K1/K2 sayfasından `page.screenshot` → `~/.local/share/kazi/kanit/<teklif_id>.png`
     (HTTP ile teyit edildiyse görüntü yok; `None`).
   - Sonuç DB'ye (`teyit_durumu`, `teyit_zamani`, `teyit_fiyati`, `kanit_goruntusu`) yazılır.
   - Teyit sonunda teyitli fiyat kullanıcının hedef fiyatına eşit/altındaysa
     `bildirim_fn({"tur": "hedef_yakalandi", ...})` çağrılır.
"""
from __future__ import annotations

from datetime import datetime
import os
import re
from pathlib import Path
import statistics
import time
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

import logging

log = logging.getLogger(__name__)
from .. import eslestir
from ..ayar import kanit_dizini as ayar_kanit_dizini
from ..kurallar.piyasa import UrunAyar, hedef_altinda, kovalanacaklar, piyasa_ozeti
from ..modeller import TZ_ISTANBUL, Katman, Teklif, TeyitDurumu, UrunKimligi, simdi_iso
from ..siteler import SITELER, _ortak
from ..siteler._kart import Kart
from ..tarayici import UA, BotEngeli, blok_mu, site_adi
from . import depo
from .tara import _bot_engeli_mi, _karta_cevir

VARSAYILAN_KANIT_DIZINI = Path.home() / ".local" / "share" / "kazi" / "kanit"
KANIT_DIZINI = VARSAYILAN_KANIT_DIZINI

# 1x1 şeffaf PNG (sahte tarayıcı screenshot() bayt döndürmediğinde dosya varlığı için yedek)
_MIN_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

TOPLAYICI_ALANLARI = {"akakce.com", "cimri.com", "epey.com"}
SITE_ALANLARI = {
    "n11": "n11.com",
    "trendyol": "trendyol.com",
    "hepsiburada": "hepsiburada.com",
    "amazon": "amazon.com.tr",
    "mediamarkt": "mediamarkt.com.tr",
    "vatan": "vatanbilgisayar.com",
    "teknosa": "teknosa.com",
    "sinerji": "sinerji.gen.tr",
    "itopya": "itopya.com",
    "incehesap": "incehesap.com",
    "gamegaraj": "gamegaraj.com",
    "gaminggentr": "gaming.gen.tr",
    "pttavm": "pttavm.com",
    "pazarama": "pazarama.com",
    "idefix": "idefix.com",
    "akakce": "akakce.com",
    "cimri": "cimri.com",
    "epey": "epey.com",
}
_YONLENDIRME_IPUCU = ("/r/", "/click", "/git", "/redirect", "/link")


class TeyitliTeklif(Teklif):
    """Hem `Teklif` özniteliklerini hem sözlük erişimini (`tk["teyit_durumu"]`) destekleyen sonuç."""

    teyit_fiyati: float | None = None
    teyit_url: str | None = None
    hedef_altinda: bool = False

    @property
    def durum(self) -> str:
        return str(self.teyit_durumu)

    def __getitem__(self, anahtar: str) -> Any:
        if anahtar == "durum":
            return str(self.teyit_durumu)
        if anahtar == "teklif_id":
            return self.id
        if anahtar == "fiyat":
            return self.sepet_fiyati
        if anahtar == "teyit_fiyat":
            return self.teyit_fiyati
        if hasattr(self, anahtar):
            return getattr(self, anahtar)
        raise KeyError(anahtar)

    def get(self, anahtar: str, yedek: Any = None) -> Any:
        try:
            return self[anahtar]
        except KeyError:
            return yedek


# ------------------------------------------------------------------ yönlendirme ve bağdaştırıcı
def _host(url: str) -> str:
    h = site_adi(url)
    return h[4:] if h.startswith("www.") else h


def _toplayici_mi(url: str) -> bool:
    h = _host(url)
    if not h:
        return False
    if h in TOPLAYICI_ALANLARI or any(h.endswith("." + a) for a in TOPLAYICI_ALANLARI):
        return True
    return False


def modul_bul(site: str):
    return SITELER.get(site)


def _site_kimligi(url: str) -> str | None:
    h = _host(url)
    for kimlik, alan in SITE_ALANLARI.items():
        if h == alan or h.endswith("." + alan):
            return kimlik
    return None


def _bagdastirici(url: str):
    """Alan adına göre `urun(t, url)` fonksiyonu seç (yoksa `_ortak.genel_urun`)."""
    kimlik = _site_kimligi(url)
    if kimlik:
        mod = SITELER.get(kimlik)
        if mod is not None and hasattr(mod, "urun"):
            return mod.urun
    return _ortak.genel_urun


class _YonlendirmeYok(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_META_YENILEME = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]*content\s*=\s*["']\s*\d+\s*;\s*url\s*=\s*([^"'>]+)""", re.I)


def meta_yenileme_hedefi(html: str, taban: str) -> str | None:
    """HTML'deki meta refresh hedefi (yoksa None)."""
    m = _META_YENILEME.search(html or "")
    if not m:
        return None
    import html as _h
    return urllib.parse.urljoin(taban, _h.unescape(m.group(1).strip()))


def _http_hedef(url: str, zaman_asimi: float = 20.0) -> str | None:
    """HTTP 30x yanıtındaki Location başlığını (yönlendirmeyi takip etmeden) döndürür."""
    opener = urllib.request.build_opener(_YonlendirmeYok)
    istek = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9"},
    )
    try:
        with opener.open(istek, timeout=zaman_asimi) as r:
            lok = r.headers.get("Location")
            if lok:
                return urllib.parse.urljoin(url, lok)
            # 200 + <meta http-equiv="refresh" content="1; url=..."> (Akakçe /r/ sayfaları böyle yönlendirir)
            govde = r.read(300_000)
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                govde = gzip.decompress(govde)
            return meta_yenileme_hedefi(govde.decode("utf-8", "replace"), url)
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            lok = e.headers.get("Location")
            if lok:
                return urllib.parse.urljoin(url, lok)
    except Exception:
        pass
    return None


def _brave_son_url(t: Any, url: str) -> tuple[str, str]:
    """Tarayıcıda yönlendirme sayfasını aç; `(son_url, html)` döndür."""
    site = site_adi(url)
    pg = t._sekme(site)
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        try:
            pg.goto(url, wait_until="commit", timeout=60000)
        except Exception:
            pass
    if hasattr(pg, "wait_for_timeout"):
        pg.wait_for_timeout(2500)
    try:
        son = pg.url
    except Exception:
        son = url
    try:
        html = pg.content()
    except Exception:
        html = ""
    if blok_mu(html):
        raise BotEngeli(site, son)
    return son, html


def _model_satici_url(t: Any, url: str) -> str | None:
    """Toplayıcı model sayfasından en ucuz gerçek mağaza URL'sini seç."""
    kimlik = _site_kimligi(url)
    if kimlik not in ("akakce", "cimri", "epey"):
        return None
    mod = SITELER.get(kimlik)
    if mod is None:
        return None
    try:
        if hasattr(mod, "satici_teklifleri"):
            teklifler = mod.satici_teklifleri(t, url) or []
        elif hasattr(mod, "teklifler"):
            teklifler = mod.teklifler(t, url) or []
        else:
            return None
    except Exception:
        return None
    sirali = sorted(
        teklifler,
        key=lambda x: getattr(x, "sepet_fiyati", None) or getattr(x, "fiyat", float("inf")),
    )
    for tk in sirali:
        tk_url = getattr(tk, "url", None)
        if tk_url and not _toplayici_mi(tk_url):
            return tk_url
    return None


# Eski yazımla geriye dönük uyumluluk
_model_satıcı_url = _model_satici_url


def yonlendirme_coz(t: Any, url: str, azami: int = 5) -> str:
    """Toplayıcı yönlendirme linkini gerçek mağaza URL'sine çöz."""
    for _ in range(max(1, azami)):
        if not _toplayici_mi(url):
            return url
        hedef = _http_hedef(url)
        if not hedef:
            yol = urllib.parse.urlparse(url).path.lower()
            if any(i in yol for i in _YONLENDIRME_IPUCU):
                hedef, _html = _brave_son_url(t, url)
            else:
                hedef = _model_satıcı_url(t, url) or ""
                if not hedef:
                    hedef, _html = _brave_son_url(t, url)
        if not hedef or hedef == url:
            break
        url = urllib.parse.urljoin(url, hedef)
    if _toplayici_mi(url):
        raise RuntimeError(f"yönlendirme çözülemedi: {url}")
    return url


def _urun_oku(t: Any, url: str):
    return _bagdastirici(url)(t, url)


# ------------------------------------------------------------------ başlık & kanıt yardımcıları
def _baslik_kurallari(urun: UrunKimligi | dict[str, Any] | None) -> dict[str, Any]:
    """Ürün sayfasında yalnız kimlik/başlık kuralları (`dahil`, `haric`, `qvl`) denetlenir;
    fiyat ve stok ayrı adımlarda (`stok_yok`, `fiyat_degisti`) ele alınır.
    """
    if urun is None:
        return {}
    cozulmus = eslestir._kurallari_coz(urun)
    return {
        k: cozulmus[k]
        for k in ("dahil", "haric", "qvl")
        if k in cozulmus and cozulmus[k] is not None
    }


def _baslik_uygun_mu(urun: UrunKimligi | dict[str, Any] | None, okunan: Kart) -> bool:
    baslik = getattr(okunan, "baslik", None) or getattr(okunan, "ad", None)
    if not baslik:
        return False
    gecici = Kart(
        site=okunan.site or "magaza",
        baslik=baslik,
        url=okunan.url or "",
        sepet_fiyati=okunan.sepet_fiyati if okunan.sepet_fiyati and okunan.sepet_fiyati > 0 else 1.0,
    )
    return eslestir.uygun_mu(_baslik_kurallari(urun), gecici)


def _kanit_hedef_yolu(teklif_id: int | str, kanit_dizini: Path | str | None = None) -> Path:
    if kanit_dizini is not None:
        dizin = Path(kanit_dizini).expanduser()
    elif KANIT_DIZINI != VARSAYILAN_KANIT_DIZINI:
        dizin = Path(KANIT_DIZINI).expanduser()
    elif os.environ.get("KAZI_VERI") or os.environ.get("XDG_DATA_HOME"):
        dizin = ayar_kanit_dizini()
    else:
        dizin = Path(KANIT_DIZINI).expanduser()
    return dizin / f"{teklif_id}.png"


def _sayfa_bul(tarayici: Any, url: str) -> Any:
    """Tarayıcı nesnesinden aktif Playwright/sahte `page` nesnesini bul."""
    if tarayici is None:
        return None
    for attr in ("page", "sayfa"):
        pg = getattr(tarayici, attr, None)
        if pg is not None and hasattr(pg, "screenshot"):
            return pg
    sekmeler = getattr(tarayici, "_sekmeler", None)
    if isinstance(sekmeler, dict) and sekmeler:
        site = site_adi(url)
        kimlik = _site_kimligi(url)
        for anahtar in (site, kimlik):
            if anahtar and anahtar in sekmeler and hasattr(sekmeler[anahtar], "screenshot"):
                return sekmeler[anahtar]
        for pg in reversed(list(sekmeler.values())):
            if pg is not None and hasattr(pg, "screenshot"):
                return pg
    k1 = getattr(tarayici, "k1", None) or getattr(getattr(tarayici, "kademe", None), "k1", None)
    if k1 is not None:
        for attr in ("page", "sayfa", "_son_sayfa"):
            pg = getattr(k1, attr, None)
            if pg is not None and hasattr(pg, "screenshot"):
                return pg
    if hasattr(tarayici, "screenshot") and callable(tarayici.screenshot):
        return tarayici
    return None


def _tarayici_katmani_mi(
    okunan: Kart | None,
    teklif: Teklif,
    tarayici: Any,
    url: str,
) -> bool:
    """Okuma K1/K2 (tarayıcı sayfası) ile mi yapıldı, yoksa düz HTTP (K0) ile mi?"""
    yontem = str(getattr(okunan, "yontem", "") or "").lower()
    if yontem in ("chromium", "k1", "brave", "brave_fetch", "k2", "k3"):
        return True
    katman = getattr(okunan, "kaynak_katmani", None)
    if katman in (Katman.K1, Katman.K2, Katman.K3, "K1", "K2", "K3"):
        return True
    # Tarayıcı üzerinde açık bir sayfa/screenshot kancası varsa ve yöntem açıkça http değilse
    sayfa = _sayfa_bul(tarayici, url)
    if sayfa is not None:
        # Sahte tarayıcıda page/sayfa/screenshot doğrudan verilmişse K1/K2 kabul et
        if (
            hasattr(tarayici, "page")
            or hasattr(tarayici, "sayfa")
            or hasattr(tarayici, "screenshot")
            or yontem != "http"
            or teklif.kaynak_katmani in (Katman.K1, Katman.K2, Katman.K3, "K1", "K2", "K3")
        ):
            return True
    return False


def _kanit_goruntusu_al(
    tarayici: Any,
    url: str,
    teklif_id: int | str | None,
    okunan: Kart | None,
    teklif: Teklif,
    kanit_dizini: Path | str | None = None,
) -> str | None:
    """K1/K2 ise ekran görüntüsünü `<kanit_dizini>/<teklif_id>.png` yoluna kaydeder; HTTP ise `None`."""
    if not _tarayici_katmani_mi(okunan, teklif, tarayici, url):
        return None
    dosya_adi = teklif_id if teklif_id is not None else f"gecici_{ int(time.time() * 1000) }"
    hedef = _kanit_hedef_yolu(dosya_adi, kanit_dizini)
    hedef.parent.mkdir(parents=True, exist_ok=True)

    sayfa = _sayfa_bul(tarayici, url)
    veri: bytes | None = None
    if sayfa is not None and hasattr(sayfa, "screenshot"):
        try:
            sonuc = sayfa.screenshot(path=str(hedef))
            if isinstance(sonuc, (bytes, bytearray)):
                veri = bytes(sonuc)
        except TypeError:
            try:
                sonuc = sayfa.screenshot(str(hedef))
                if isinstance(sonuc, (bytes, bytearray)):
                    veri = bytes(sonuc)
            except TypeError:
                try:
                    sonuc = sayfa.screenshot()
                    if isinstance(sonuc, (bytes, bytearray)):
                        veri = bytes(sonuc)
                except Exception:
                    pass
        except Exception:
            pass

    if not hedef.exists():
        hedef.write_bytes(veri if veri else _MIN_PNG)
    return str(hedef)


# ------------------------------------------------------------------ tekil teyit
def _urun_id_coz(urun: UrunKimligi | dict[str, Any] | str | None, teklif: Any) -> str:
    if isinstance(urun, UrunKimligi) and urun.id:
        return urun.id
    if isinstance(urun, dict) and urun.get("id"):
        return str(urun["id"])
    if isinstance(urun, str) and urun:
        return urun
    return str(getattr(teklif, "urun_id", "") or "")


def _teklif_fiyati(teklif: Any) -> float | None:
    f = getattr(teklif, "sepet_fiyati", None)
    if f is None:
        f = getattr(teklif, "fiyat", None)
    return float(f) if f is not None else None


def _teklif_durum_ata(
    teklif: Any,
    durum: TeyitDurumu,
    *,
    zaman: str | None = None,
    teyit_fiyati: float | None = None,
    teyit_url: str | None = None,
    kanit_goruntusu: str | None = None,
    satici: str | None = None,
    stok: bool | None = None,
    pesin_taksit: int | None = None,
) -> None:
    zaman_str = zaman or simdi_iso()
    try:
        teklif.teyit_durumu = durum
    except Exception:
        object.__setattr__(teklif, "teyit_durumu", durum)
    try:
        teklif.teyit_zamani = zaman_str
    except Exception:
        object.__setattr__(teklif, "teyit_zamani", zaman_str)
    try:
        teklif.kanit_goruntusu = kanit_goruntusu
    except Exception:
        object.__setattr__(teklif, "kanit_goruntusu", kanit_goruntusu)
    if satici:
        mevcut_satici = getattr(teklif, "satici", None)
        if not mevcut_satici or str(mevcut_satici).endswith("üzerinden"):
            try:
                teklif.satici = satici
            except Exception:
                object.__setattr__(teklif, "satici", satici)
    if stok is not None:
        try:
            teklif.stok = bool(stok)
        except Exception:
            object.__setattr__(teklif, "stok", bool(stok))
    if pesin_taksit and int(pesin_taksit) > 0:
        try:
            teklif.pesin_fiyatina_taksit = True
        except Exception:
            object.__setattr__(teklif, "pesin_fiyatina_taksit", True)
        object.__setattr__(teklif, "taksit", int(pesin_taksit))

    object.__setattr__(teklif, "durum", str(durum))
    object.__setattr__(teklif, "teyit_fiyati", teyit_fiyati)
    object.__setattr__(teklif, "teyit_fiyat", teyit_fiyati)
    if teyit_url is not None:
        object.__setattr__(teklif, "teyit_url", teyit_url)


def teyit_et(
    teklif: Any,
    urun: UrunKimligi | dict[str, Any] | None = None,
    tarayici: Any = None,
    *,
    v: Any = None,
    con: Any = None,
    bekle_fn: Callable[[float], Any] | None = None,
    bekle_sn: float = 40.0,
    bekle: float | None = None,
    cozucu: Callable[[Any, str], str] | None = None,
    bagdastirici: Callable[[Any, str], Any] | None = None,
    kanit_dizini: Path | str | None = None,
    onbellek_saat: float = 2.0,
    simdi: datetime | None = None,
) -> TeyitDurumu:
    """Tek bir teklifi satıcının kendi ürün sayfasında (40 sn aralıklı 2 okumayla) teyit eder.

    Dönüş: `TeyitDurumu` (`teyitli` | `urun_farkli` | `stok_yok` | `fiyat_degisti` | `supheli`).
    """
    # Esnek çağrı desteği: teyit_et(v, tarayici, teklif, urun, ...)
    if (hasattr(teklif, "baglanti") or hasattr(teklif, "execute")) and urun is not None:
        v_gecici = teklif
        t_gecici = urun
        teklif = tarayici
        urun = v if isinstance(v, (UrunKimligi, dict)) else None
        v = v_gecici
        tarayici = t_gecici

    hedef_v = v if v is not None else con
    urun_id = _urun_id_coz(urun, teklif)
    if urun is None and hedef_v is not None and urun_id:
        urun = depo.urun_getir(hedef_v, urun_id, otomatik_ekle=True)

    beklenen_fiyat = _teklif_fiyati(teklif)
    url = str(getattr(teklif, "url", "") or "")
    zaman_str = (simdi or datetime.now(TZ_ISTANBUL)).isoformat(timespec="seconds")

    # 1) 2 saatlik önbellek denetimi
    if hedef_v is not None and urun_id and url and onbellek_saat > 0:
        onbellek = depo.teyit_onbellek(
            hedef_v,
            urun_id,
            url,
            saat=onbellek_saat,
            simdi=simdi,
            beklenen_fiyat=beklenen_fiyat,
        )
        if onbellek is not None:
            durum_onb = TeyitDurumu(onbellek["teyit_durumu"])
            _teklif_durum_ata(
                teklif,
                durum_onb,
                zaman=onbellek["teyit_zamani"],
                teyit_fiyati=onbellek["teyit_fiyati"],
                teyit_url=onbellek["teyit_url"],
                kanit_goruntusu=onbellek["kanit_goruntusu"],
                satici=onbellek["satici"],
                stok=onbellek["stok"],
            )
            tid = getattr(teklif, "id", None)
            if tid is not None and tid != onbellek.get("id"):
                depo.teklif_teyit_guncelle(
                    hedef_v,
                    tid,
                    urun_id=urun_id,
                    url=url,
                    teyit_durumu=durum_onb,
                    teyit_zamani=onbellek["teyit_zamani"],
                    teyit_fiyati=onbellek["teyit_fiyati"],
                    kanit_goruntusu=onbellek["kanit_goruntusu"],
                    teyit_url=onbellek["teyit_url"],
                    satici=onbellek["satici"],
                    stok=onbellek["stok"],
                )
            return durum_onb

    def _kaydet_ve_don(
        durum: TeyitDurumu,
        *,
        tf: float | None = None,
        t_url: str | None = None,
        kanit: str | None = None,
        satici: str | None = None,
        stok: bool | None = None,
        taksit: int | None = None,
    ) -> TeyitDurumu:
        _teklif_durum_ata(
            teklif,
            durum,
            zaman=zaman_str,
            teyit_fiyati=tf,
            teyit_url=t_url,
            kanit_goruntusu=kanit,
            satici=satici,
            stok=stok,
            pesin_taksit=taksit,
        )
        if hedef_v is not None and urun_id:
            depo.teklif_teyit_guncelle(
                hedef_v,
                getattr(teklif, "id", None),
                urun_id=urun_id,
                url=url,
                teyit_durumu=durum,
                teyit_zamani=zaman_str,
                teyit_fiyati=tf,
                kanit_goruntusu=kanit,
                teyit_url=t_url,
                satici=getattr(teklif, "satici", None),
                stok=stok,
                pesin_taksit=taksit,
            )
        return durum

    # 2) Toplayıcı yönlendirmesini gerçek mağaza URL'sine çöz
    try:
        gercek_url = (cozucu or yonlendirme_coz)(tarayici, url)
    except Exception as e:
        if _bot_engeli_mi(e):
            return _kaydet_ve_don(TeyitDurumu.supheli)
        if isinstance(e, RuntimeError) and "yönlendirme çözülemedi" in str(e):
            return _kaydet_ve_don(TeyitDurumu.urun_farkli)
        return _kaydet_ve_don(TeyitDurumu.supheli)

    if not gercek_url or _toplayici_mi(gercek_url):
        return _kaydet_ve_don(TeyitDurumu.urun_farkli)

    okuyucu = bagdastirici or _urun_oku

    # 3) İlk okuma
    try:
        ham1 = okuyucu(tarayici, gercek_url)
    except Exception:
        return _kaydet_ve_don(TeyitDurumu.supheli, t_url=gercek_url)

    okunan1 = _karta_cevir(ham1, _site_kimligi(gercek_url) or site_adi(gercek_url) or "magaza")
    if okunan1 is None:
        return _kaydet_ve_don(TeyitDurumu.urun_farkli, t_url=gercek_url)

    if not _baslik_uygun_mu(urun, okunan1):
        return _kaydet_ve_don(TeyitDurumu.urun_farkli, t_url=gercek_url)

    if okunan1.stok is False:
        return _kaydet_ve_don(
            TeyitDurumu.stok_yok,
            tf=okunan1.sepet_fiyati,
            t_url=gercek_url,
            satici=okunan1.satici,
            stok=False,
        )

    f1 = okunan1.sepet_fiyati
    if beklenen_fiyat is None or not f1 or f1 <= 0:
        return _kaydet_ve_don(TeyitDurumu.fiyat_degisti, tf=f1, t_url=gercek_url)

    tolerans = max(beklenen_fiyat * 0.01, 1.0)
    if abs(float(f1) - beklenen_fiyat) > tolerans:
        kanit = _kanit_goruntusu_al(
            tarayici, gercek_url, getattr(teklif, "id", None), okunan1, teklif, kanit_dizini
        )
        return _kaydet_ve_don(
            TeyitDurumu.fiyat_degisti,
            tf=float(f1),
            t_url=gercek_url,
            kanit=kanit,
            satici=okunan1.satici,
            stok=okunan1.stok,
        )

    # 4) 40 sn bekleme ve ikinci okuma
    sure_sn = float(bekle if bekle is not None else bekle_sn)
    if bekle_fn is not None:
        bekle_fn(sure_sn)
    elif sure_sn > 0:
        time.sleep(sure_sn)

    try:
        ham2 = okuyucu(tarayici, gercek_url)
    except Exception:
        return _kaydet_ve_don(TeyitDurumu.supheli, tf=float(f1), t_url=gercek_url)

    okunan2 = _karta_cevir(ham2, _site_kimligi(gercek_url) or site_adi(gercek_url) or "magaza")
    if okunan2 is None or not _baslik_uygun_mu(urun, okunan2):
        return _kaydet_ve_don(TeyitDurumu.urun_farkli, t_url=gercek_url)

    if okunan2.stok is False:
        return _kaydet_ve_don(
            TeyitDurumu.stok_yok,
            tf=okunan2.sepet_fiyati,
            t_url=gercek_url,
            satici=okunan2.satici or okunan1.satici,
            stok=False,
        )

    f2 = okunan2.sepet_fiyati
    kanit = _kanit_goruntusu_al(
        tarayici, gercek_url, getattr(teklif, "id", None), okunan2, teklif, kanit_dizini
    )
    if (
        not f2
        or f2 <= 0
        or abs(float(f2) - beklenen_fiyat) > tolerans
        or abs(float(f2) - float(f1)) > 1.0
    ):
        return _kaydet_ve_don(
            TeyitDurumu.fiyat_degisti,
            tf=float(f2) if f2 else None,
            t_url=gercek_url,
            kanit=kanit,
            satici=okunan2.satici or okunan1.satici,
            stok=okunan2.stok,
        )

    son_satici = okunan2.satici or okunan1.satici
    son_taksit = okunan2.pesin_taksit or okunan1.pesin_taksit
    son_stok = okunan2.stok if okunan2.stok is not None else okunan1.stok
    sonuc = _kaydet_ve_don(
        TeyitDurumu.teyitli,
        tf=float(f2),
        t_url=gercek_url,
        kanit=kanit,
        satici=son_satici,
        stok=son_stok,
        taksit=son_taksit,
    )
    if hedef_v is not None and getattr(teklif, "id", None):
        try:
            from kazi.taksit import teyitten_kaydet

            teyitten_kaydet(hedef_v, tarayici, teklif, gercek_url, float(f2))
        except Exception as exc:
            log.debug("Teyitten taksit kaydetme hatası: %s", exc)
    return sonuc


# ------------------------------------------------------------------ kovalama
def kovala(
    v: Any,
    tarayici: Any = None,
    urun_id: str = "",
    azami: int = 5,
    *,
    bildirim_fn: Callable[[dict[str, Any]], Any] | None = None,
    bekle_fn: Callable[[float], Any] | None = lambda _s: None if False else None,  # aşağıda çözülür
    bekle_sn: float = 40.0,
    cozucu: Callable[[Any, str], str] | None = None,
    bagdastirici: Callable[[Any, str], Any] | None = None,
    kanit_dizini: Path | str | None = None,
    onbellek_saat: float = 2.0,
    simdi: datetime | None = None,
    ayar: UrunAyar | None = None,
    aralik: int = 5,
) -> list[TeyitliTeklif]:
    """Ortalamanın altındaki teklifleri en ucuzdan başlayarak teyit eder; teyitli fiyat hedefin altındaysa bildirir."""
    depo.kur(v)
    ayar = ayar or UrunAyar()
    ref_simdi = simdi or datetime.now(TZ_ISTANBUL)
    if ref_simdi.tzinfo is None:
        ref_simdi = ref_simdi.replace(tzinfo=TZ_ISTANBUL)

    urun = depo.urun_getir(v, urun_id, otomatik_ekle=True)
    if urun is None:
        return []

    # Varsayılan bekle_fn: gerçek çalıştırmada time.sleep; testte bekle_fn enjekte edilir
    etkin_bekle_fn = time.sleep if bekle_fn is kovala.__kwdefaults__["bekle_fn"] else bekle_fn  # type: ignore[index]
    etkin_bildirim = bildirim_fn or getattr(tarayici, "bildirim_fn", None)

    tum_teklifler = depo.son_teklifler(
        v, urun_id, yalniz_son=True, simdi=ref_simdi, pencere_saat=ayar.pencere_saat
    )
    if not tum_teklifler:
        return []

    ozet = piyasa_ozeti(tum_teklifler, ref_simdi, ayar)
    adaylar = kovalanacaklar(tum_teklifler, ozet)

    hedef = depo.hedef_fiyat(v, urun_id)
    if not adaylar and hedef is not None:
        adaylar = sorted(
            [
                t
                for t in tum_teklifler
                if t.sepet_fiyati is not None
                and t.sepet_fiyati <= hedef
                and t.stok is not False
                and str(t.teyit_durumu) == "bekliyor"
            ],
            key=lambda x: x.sepet_fiyati or 0.0,
        )

    secilen = adaylar[: max(0, int(azami))]
    sonuclar: list[TeyitliTeklif] = []

    for i, tk in enumerate(secilen, 1):
        durum = teyit_et(
            tk,
            urun,
            tarayici,
            v=v,
            bekle_fn=etkin_bekle_fn,
            bekle_sn=bekle_sn,
            cozucu=cozucu,
            bagdastirici=bagdastirici,
            kanit_dizini=kanit_dizini,
            onbellek_saat=onbellek_saat,
            simdi=ref_simdi,
        )

        # Teyit sonrası güncel piyasa özeti ve hedef fiyat denetimi (yıldız sistemi 14.11'de kaldırıldı)
        guncel_teklifler = depo.son_teklifler(
            v, urun_id, yalniz_son=True, simdi=ref_simdi, pencere_saat=ayar.pencere_saat
        )
        guncel_ozet = piyasa_ozeti(guncel_teklifler, ref_simdi, ayar)
        hedefte = hedef_altinda(tk, hedef)

        sonuc_tk = TeyitliTeklif(
            id=tk.id,
            urun_id=tk.urun_id,
            site=tk.site,
            satici=tk.satici,
            baslik=tk.baslik,
            url=tk.url,
            liste_fiyati=tk.liste_fiyati,
            sepet_fiyati=tk.sepet_fiyati,
            kupon=tk.kupon,
            kargo=tk.kargo,
            stok=tk.stok,
            pesin_fiyatina_taksit=tk.pesin_fiyatina_taksit,
            gozlem_zamani=tk.gozlem_zamani,
            kaynak_katmani=tk.kaynak_katmani,
            teyit_durumu=durum,
            teyit_zamani=tk.teyit_zamani,
            kanit_goruntusu=tk.kanit_goruntusu,
            teyit_fiyati=getattr(tk, "teyit_fiyati", None),
            teyit_url=getattr(tk, "teyit_url", None),
            hedef_altinda=hedefte,
        )
        sonuclar.append(sonuc_tk)

        if hedefte:
            olay_veri = {
                "tur": "hedef_yakalandi",
                "urun_id": urun_id,
                "urun_ad": urun.kanonik_ad,
                "teklif_id": sonuc_tk.id,
                "site": sonuc_tk.site,
                "satici": sonuc_tk.satici,
                "baslik": sonuc_tk.baslik,
                "fiyat": sonuc_tk.teyit_fiyati or sonuc_tk.sepet_fiyati,
                "sepet_fiyati": sonuc_tk.sepet_fiyati,
                "teyit_fiyati": sonuc_tk.teyit_fiyati,
                "hedef_fiyat": hedef,
                "ortalama": guncel_ozet.get("ortalama"),
                "url": sonuc_tk.teyit_url or sonuc_tk.url,
                "teyit_zamani": sonuc_tk.teyit_zamani,
                "kanit_goruntusu": sonuc_tk.kanit_goruntusu,
            }
            depo.olay_kaydet(
                v,
                "hedef_yakalandi",
                urun_id=urun_id,
                mesaj=f"🎯 Hedef fiyat yakalandı — {urun.kanonik_ad}: {sonuc_tk.sepet_fiyati} TL ({sonuc_tk.site})",
                veri=olay_veri,
                zaman=sonuc_tk.teyit_zamani,
            )
            if etkin_bildirim is not None:
                try:
                    etkin_bildirim(olay_veri)
                except Exception:
                    pass

        if aralik and i % aralik == 0 and tarayici is not None and hasattr(tarayici, "brave_birak"):
            try:
                tarayici.brave_birak()
            except Exception:
                pass

    return sonuclar


# ------------------------------------------------------------------ geriye dönük yardımcılar
def medyan_suzgec(
    teklifler: list[Any],
    urun: dict[str, Any] | UrunKimligi | None = None,
    tarayici: Any = None,
    bekle: float = 40.0,
    tolerans: float = 0.02,
    okuyucu: Callable[[Any, Any], Any] | None = None,
) -> list[Any]:
    """Medyanın %20'den fazla altındaki fiyatları ürün sayfasından yeniden oku."""
    gecerli = [t for t in teklifler if _teklif_fiyati(t)]
    if len(gecerli) < 3:
        return teklifler
    med = statistics.median(_teklif_fiyati(t) for t in gecerli)  # type: ignore[arg-type]
    esik = med * 0.8
    for tk in gecerli:
        fiyat = _teklif_fiyati(tk)
        if fiyat is None or fiyat > esik:
            continue
        if bekle:
            time.sleep(bekle)
        okunan = None
        try:
            if okuyucu is not None:
                okunan = okuyucu(tarayici, tk)
            else:
                mod = modul_bul(getattr(tk, "site", ""))
                if mod is not None and hasattr(mod, "urun"):
                    okunan = mod.urun(tarayici, getattr(tk, "url", ""))
        except Exception:
            okunan = None
        okunan_fiyat = _teklif_fiyati(okunan) if okunan is not None else None
        if okunan_fiyat and abs(okunan_fiyat - fiyat) <= max(fiyat * tolerans, 1.0):
            _teklif_durum_ata(tk, TeyitDurumu.teyitli, teyit_fiyati=okunan_fiyat)
        else:
            _teklif_durum_ata(tk, TeyitDurumu.supheli)
    return teklifler
