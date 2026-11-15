"""Reklam ve izleyici alan adlarına giden istekleri iptal ederek sayfa açılışını hızlandırır.

Kaynak (BSD-3-Clause): https://github.com/D4Vinci/Scrapling —
`scrapling/engines/toolbelt/ad_domains.py` (özgün veri: Peter Lowe'un reklam/izleyici sunucu listesi).
Alan adları `veri/reklam_alanlari.txt` dosyasında tutulur; lisans metni `veri/UCUNCU_TARAF.md` içindedir.

Bu modül yalnız ağ isteği engeller. Bot korumasını aşma, gizleme, sahte parmak izi veya UA değiştirme YOKTUR.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Protocol
from urllib.parse import urlparse

VERI_YOLU = Path(__file__).with_name("veri") / "reklam_alanlari.txt"

#: Engellenebilecek kaynak türleri (Playwright `resource_type` değerleri). 15.11: ayrı ayrı açılıp kapanır.
GORSEL_TURLERI = ("image", "media", "font")      # eski tek anahtar (kur(gorsel=True) hepsini engeller)
TUR_GORSEL, TUR_MEDYA, TUR_YAZI_TIPI = ("image",), ("media",), ("font",)

#: Canlı destek / anlık bildirim / kişiselleştirme-açılır pencere / anket betikleri (15.11). Fiyat verisi taşımazlar;
#: Türk e-ticaret sitelerinde açılır pencerelerin çoğu (Insider, Segmentify) buradan gelir.
WIDGET_ALANLARI = frozenset({
    "useinsider.com", "segmentify.com", "intercom.io", "intercomcdn.com", "zdassets.com",
    "zopim.com", "zendesk.com", "livechatinc.com", "tawk.to", "crisp.chat", "tidio.co", "tidiochat.com",
    "onesignal.com", "pushwoosh.com", "izooto.com", "webpushr.com", "pushengage.com", "sendpulse.com",
    "freshchat.com", "drift.com", "jivosite.com", "userguiding.com", "getsitecontrol.com",
    "optinmonster.com", "wisepops.com", "hotjar.com", "clarity.ms", "smartlook.com", "mouseflow.com",
    "fullstory.com", "elfsight.com", "smartsupp.com", "olark.com", "chatra.io",
})

#: Engellenen istek sayaçları; `kur` çağrıları arasında korunur (sıfırlanmaz).
sayac: dict[str, int] = {"reklam": 0, "gorsel": 0, "widget": 0, "gomulu": 0, "gif": 0}


class _Istek(Protocol):
    url: str
    resource_type: str


class _Yol(Protocol):
    request: _Istek

    def abort(self, *args, **kwargs): ...

    def continue_(self, *args, **kwargs): ...


@lru_cache(maxsize=1)
def alanlar() -> frozenset[str]:
    """`veri/reklam_alanlari.txt` içindeki alan adlarını bir kez yükleyip önbellekler.

    `#` ile başlayan yorum satırları ve boş satırlar atlanır; adlar küçük harfe çevrilir.
    """
    kume: set[str] = set()
    try:
        for satir in VERI_YOLU.read_text(encoding="utf-8").splitlines():
            ad = satir.strip().lower()
            if ad and not ad.startswith("#"):
                kume.add(ad)
    except OSError:
        return frozenset()
    return frozenset(kume)


def _host_al(url: str) -> str | None:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.rstrip(".").lower()


def engellenecek_mi(url: str, ek_alanlar: Iterable[str] = ()) -> bool:
    """URL'nin host'u listedeki bir alan adına eşitse ya da onun alt alanıysa True.

    Örnek: `a.doubleclick.net` → `doubleclick.net` eşleşir; `notdoubleclick.net` eşleşmez.
    Geçersiz URL ya da host'suz URL için False döner.
    """
    host = _host_al(url)
    if host is None:
        return False
    adaylar: frozenset[str] | set[str] = alanlar()      # her istekte 3.500'lük kümeyi kopyalama
    ekler = {e.strip().lower().rstrip(".") for e in ek_alanlar if isinstance(e, str) and e.strip()}
    if ekler:
        adaylar = adaylar | ekler
    if host in adaylar:
        return True
    parcalar = host.split(".")
    for i in range(1, len(parcalar)):
        if ".".join(parcalar[i:]) in adaylar:
            return True
    return False


def _widget_mi(url: str) -> bool:
    host = _host_al(url)
    if not host:
        return False
    parcalar = host.split(".")
    return any(".".join(parcalar[i:]) in WIDGET_ALANLARI for i in range(len(parcalar) - 1))


# 15.11: gömülü içerik (fiyat kazımada hiç gerekmeyen, sayfayı en çok ağırlaştıran üçüncü taraf iframe/betikler)
GOMULU_ALANLARI = frozenset({
    "youtube.com", "youtube-nocookie.com", "ytimg.com", "googlevideo.com", "vimeo.com", "vimeocdn.com",
    "maps.googleapis.com", "maps.gstatic.com", "maps.google.com", "connect.facebook.net", "platform.twitter.com",
    "syndication.twitter.com", "platform.x.com", "instagram.com", "cdninstagram.com", "tiktok.com", "tiktokcdn.com",
    "pinterest.com", "pinimg.com", "player.vimeo.com", "dailymotion.com", "embed.spotify.com",
})


def _gomulu_mi(url: str) -> bool:
    host = _host_al(url) or ""
    return any(host == a or host.endswith("." + a) for a in GOMULU_ALANLARI)


def _gif_mi(url: str) -> bool:
    yol = str(url or "").split("?", 1)[0].split("#", 1)[0].lower()
    return yol.endswith(".gif")


def kur(baglam, reklam: bool = True, gorsel: bool = False, *, medya: bool = False, yazi_tipi: bool = False,
        widget: bool = False, gorsel_turleri: tuple[str, ...] | None = None, gif: bool = False,
        gomulu: bool = False) -> None:
    """Playwright `BrowserContext` veya `Page` üzerine reklam/görsel engelleyici kurar.

    `baglam.route("**/*", isleyici)` ile tüm istekler yakalanır. `reklam=True` iken listedeki
    alan adlarına giden istekler, `gorsel=True` iken `GORSEL_TURLERI` türündeki istekler iptal edilir.
    Ana belge (`resource_type == "document"`) hiçbir zaman engellenmez. İşleyici istisna fırlatmaz;
    beklenmedik hata durumunda istek devam ettirilir.
    """

    if gorsel_turleri is None:
        turler = set(GORSEL_TURLERI if gorsel else ())
    else:
        turler = set(gorsel_turleri) if gorsel else set()
    if medya:
        turler.update(TUR_MEDYA)
    if yazi_tipi:
        turler.update(TUR_YAZI_TIPI)

    def isleyici(route: _Yol) -> None:
        try:
            istek = route.request
            tur = getattr(istek, "resource_type", "") or ""
            if tur == "document":
                route.continue_()
                return
            url = getattr(istek, "url", "") or ""
            if reklam and engellenecek_mi(url):
                sayac["reklam"] += 1
                route.abort()
                return
            if widget and _widget_mi(url):
                sayac["widget"] += 1
                route.abort()
                return
            if gomulu and _gomulu_mi(url):
                sayac["gomulu"] = sayac.get("gomulu", 0) + 1
                route.abort()
                return
            if gif and tur == "image" and _gif_mi(url):
                sayac["gif"] = sayac.get("gif", 0) + 1
                route.abort()
                return
            if tur in turler:
                sayac["gorsel"] += 1
                route.abort()
                return
            route.continue_()
        except Exception:  # noqa: BLE001 — işleyici asla tarayıcıyı düşürmesin
            try:
                route.continue_()
            except Exception:  # noqa: BLE001
                pass

    baglam.route("**/*", isleyici)
