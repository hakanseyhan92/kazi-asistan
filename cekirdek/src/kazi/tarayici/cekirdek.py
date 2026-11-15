"""Tarayıcı çekirdeği: düz HTTP (K0) ve Brave CDP (K2) erişim katmanı.

Tasarım notları
---------------
- Senkron API. Playwright sync API yalnız Brave/Chromium kademesinde ve tembel başlatılır.
- Brave profili: ~/.local/share/kazi/brave_profil (eski sistemle çakışmaz).
- CDP portu: 9334.
- Kilit dosyası: ~/.local/share/kazi/brave.lock (fcntl, aynı iş parçacığında yeniden girilebilir).
- Brave yolu otomatik: flatpak com.brave.Browser -> brave -> brave-browser -> brave-browser-stable.
- YASAK: stealth, parmak izi gizleme, CAPTCHA çözme.
- Bildirim: Telegram yerine `bildirim_fn(olay: dict)` geri çağırımı (varsayılan: hiçbir şey).
"""
from __future__ import annotations

from .gorunurluk import ARKA_PLAN_BAYRAKLARI, pencereleri_ayarla
import gzip
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..modeller import TZ_ISTANBUL, Katman, simdi_iso
from .kilit import KILIT_YOL, KilitZamanAsimi, brave_kilidi

VERI_DIZINI = Path.home() / ".local" / "share" / "kazi"
BRAVE_PROFIL = VERI_DIZINI / "brave_profil"
DURUM_DOSYA = VERI_DIZINI / "durum.json"
BILDIRIM_DOSYA = VERI_DIZINI / "bildirim.json"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
CDP_PORT = 9334
CDP = f"http://localhost:{CDP_PORT}"

# ---------------------------------------------------------------- tespit
BLOK_IMZALARI = [
    r"just a moment",
    r"cf-chl",
    r"challenge-platform",
    r"captcha",
    r"px-captcha",
    r"access denied",
    r"olağan dışı trafik",
    r"olagan disi trafik",
    r"robot olmadığınızı",
    r"robot olmadiginizi",
    r"bot koruması",
    r"bot korumasi",
    r"are you a robot",
    r"verify you are human",
]
_BLOK_RX = re.compile("|".join(BLOK_IMZALARI), re.IGNORECASE)
_FIYAT_RX = re.compile(r"(₺|\bTL\b|\d[\d.,]*\s*(?:TL|₺))", re.IGNORECASE)


def blok_mu(html: str | None) -> bool:
    """Sayfa bot doğrulaması/erişim engeli gösteriyor mu?"""
    if not html:
        return True
    if _BLOK_RX.search(html):
        # Cloudflare normal sayfalara da /cdn-cgi/challenge-platform betiği koyar; "captcha"
        # giriş formlarında geçer. Büyük ve bol fiyatlı sayfa gerçek içeriktir, engel değil.
        if len(html) > 150_000 and len(_FIYAT_RX.findall(html)) >= 5:
            return False
        return True
    # çok kısa ve fiyat içermeyen gövde: büyük olasılıkla engel/boş yanıt
    if len(html) < 2048 and not _FIYAT_RX.search(html):
        return True
    return False


class BotEngeli(Exception):
    def __init__(self, site: str, url: str = ""):
        self.site = site
        self.url = url
        super().__init__(f"{site}: bot doğrulaması/erişim engeli ({url})")


@dataclass
class Yanit:
    html: str
    yontem: str            # "http" | "chromium" | "brave" | "brave_fetch"
    durum: int | None      # HTTP durum kodu (brave'de None)
    url: str
    katman: Katman | None = None


# ---------------------------------------------------------------- yardımcılar
def _oku(yol: Path, varsayilan: Any) -> Any:
    try:
        return json.loads(yol.read_text(encoding="utf-8"))
    except Exception:
        return varsayilan


_YAZ_KILIDI = threading.Lock()


def _yaz(yol: Path, veri: Any) -> None:
    """Atomik yaz. Çok sekmeli havuzda işçiler aynı dosyaya yazar (C2 #1): süreç içi kilit + iş parçacığına özgü geçici dosya."""
    yol.parent.mkdir(parents=True, exist_ok=True)
    with _YAZ_KILIDI:
        gecici = Path(f"{yol}.{os.getpid()}.{threading.get_ident()}.tmp")
        gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=1), encoding="utf-8")
        gecici.replace(yol)


def site_adi(url: str) -> str:
    try:
        h = urllib.parse.urlparse(url).hostname or url
    except Exception:
        h = str(url)
    h = h.lower()
    if h.startswith("www."):
        h = h[4:]
    return h


def _site_eslesir(site: str, url: str) -> bool:
    """`site` kimliği (alan adı ya da kısa ad, ör. "cimri") bu URL'ye ait mi?"""
    try:
        host = (urllib.parse.urlparse(url or "").hostname or "").lower()
    except Exception:
        return False
    if not host:
        return False
    s = str(site or "").lower().strip()
    if s.startswith("www."):
        s = s[4:]
    if host.startswith("www."):
        host = host[4:]
    if not s:
        return False
    if "." in s:
        return host == s or host.endswith("." + s)
    return s in host.split(".")


def varsayilan_bildirim(olay: dict[str, Any]) -> None:
    """Varsayılan bildirim geri çağırımı: hiçbir şey yapmaz."""
    return None


def bildir(
    site: str,
    url: str = "",
    bildirim_fn: Callable[[dict[str, Any]], Any] | None = None,
) -> bool:
    """Doğrulama gerekli olayını `bildirim_fn` ile ilet."""
    olay = {"tur": "dogrulama_gerekli", "site": site, "url": url}
    if bildirim_fn is not None:
        try:
            bildirim_fn(olay)
        except Exception:
            pass
    return True


# ---------------------------------------------------------------- Brave yolu ve başlatma
def _flatpak_brave_var_mi(flatpak_bin: str) -> bool:
    """Flatpak içinde `com.brave.Browser` kurulu mu?"""
    try:
        p = Path(flatpak_bin)
        if p.is_file() and p.stat().st_size == 0:
            # Test ortamında 0 baytlık sahte çalıştırılabilir dosya
            return True
    except OSError:
        pass
    try:
        r = subprocess.run(
            [flatpak_bin, "info", "com.brave.Browser"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return r.returncode == 0
    except OSError:
        # Sahte test betiği shebang içermiyorsa ama çalıştırılabilirse
        return True
    except Exception:
        return False


def brave_yolu_bul(
    path: str | None = None,
    flatpak_kontrol: Callable[[str], bool] | None = None,
) -> list[str] | None:
    """Brave çalıştırma ön ekini sırasıyla ara:
    1) flatpak `com.brave.Browser` -> `["flatpak", "run", "com.brave.Browser"]`
    2) `brave` -> `[yol]`
    3) `brave-browser` -> `[yol]`
    4) `brave-browser-stable` -> `[yol]`
    Bulunamazsa `None` döner.
    """
    arama_yolu = os.environ.get("PATH", "") if path is None else path
    if not arama_yolu:
        return None

    fp = shutil.which("flatpak", path=arama_yolu)
    if fp:
        kontrol = flatpak_kontrol or _flatpak_brave_var_mi
        if kontrol(fp):
            return ["flatpak", "run", "com.brave.Browser"]

    for aday in ("brave", "brave-browser", "brave-browser-stable"):
        bulunan = shutil.which(aday, path=arama_yolu)
        if bulunan:
            return [bulunan]

    return None


def brave_komutu(
    profil_dizini: Path | str | None = None,
    port: int = CDP_PORT,
    path: str | None = None,
    flatpak_kontrol: Callable[[str], bool] | None = None,
) -> list[str] | None:
    """Brave başlatma komut dizisini oluştur; Brave bulunamazsa `None`."""
    taban = brave_yolu_bul(path=path, flatpak_kontrol=flatpak_kontrol)
    if not taban:
        return None
    profil = Path(profil_dizini).expanduser() if profil_dizini else _profil_sec(taban)
    return [
        *taban,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profil}",
        "--no-first-run",
        *ARKA_PLAN_BAYRAKLARI,
        *_oynatma_bayragi(),
        "about:blank",
    ]




def yanit_jsonlari(resp: Any) -> list[Any]:
    """Ağ yanıtındaki JSON gövde(ler)i: application/json → [gövde]; text/event-stream (SSE) → her `data:` satırı ayrı
    (15.11: Trip.com uçuş listesini FlightListSearchSSE akışıyla veriyor; yalnız JSON toplandığı için hiç okunmuyordu).
    JSON değilse []."""
    ct = ((resp.headers or {}).get("content-type", "") or "").lower()
    if "json" in ct:
        return [resp.json()]
    if "event-stream" in ct:
        sonuc = []
        for satir in (resp.text() or "").splitlines():
            if satir.startswith("data:"):
                try:
                    sonuc.append(json.loads(satir[5:].strip()))
                except ValueError:
                    continue
        return sonuc
    return []

def _oynatma_bayragi() -> list[str]:
    """Ayar `otomatik_oynatma_kapat` (15.11): video/ses kullanıcı hareketi olmadan başlamaz (Chromium resmî bayrağı)."""
    try:
        from .paralel_ayar import paralel_ayarlar
        if paralel_ayarlar().get("otomatik_oynatma_kapat"):
            return ["--autoplay-policy=user-gesture-required"]
    except Exception:
        pass
    return []

def _profil_sec(taban: list[str] | None) -> Path:
    """Flatpak Brave'de kalıcı veri klasörü (bkz. profil.py: ~/.local/share/kazi'ye yazamıyor), diğerlerinde BRAVE_PROFIL."""
    from .profil import profil_dizini as _pd
    return _pd(taban, BRAVE_PROFIL)


def sayfa_kapat_cdp(hedef_id: str, cdp_url: str = CDP) -> bool:
    """DevTools HTTP ucuyla tek sayfayı kapat (Playwright gerekmez)."""
    try:
        with urllib.request.urlopen(f"{cdp_url}/json/close/{hedef_id}", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def artik_pencereleri_kapat(cdp_url: str = CDP) -> int:
    """Çökmüş/yarım taramalardan kalan işçi pencerelerini (about:blank#kazi-…) ve fazladan boş sekmeleri kapat;
    en az bir sayfa açık kalır (son pencere kapanınca Brave de kapanır). Kapatılan sayısını döndürür."""
    try:
        with urllib.request.urlopen(f"{cdp_url}/json", timeout=3) as r:
            sayfalar = [t for t in json.loads(r.read().decode()) if t.get("type") == "page"]
    except Exception:
        return 0
    bos = [t for t in sayfalar if str(t.get("url", "")).startswith("about:blank")]
    dolu = len(sayfalar) - len(bos)
    kapatilacak = bos if dolu else bos[1:]
    return sum(1 for t in kapatilacak if sayfa_kapat_cdp(t["id"], cdp_url))


def brave_kapat(cdp_url: str = CDP, kilit_bekle: float = 5.0, kilit_yolu: Path | str | None = None) -> bool:
    """Tarama bitince kazı Brave'ini DÜZGÜN kapat (CDP Browser.close; çerezler kalıcı profile yazılır). Boşta boş
    about:blank penceresi kalmaz, ~1,3 GB RAM boşalır; sonraki tarama brave_hazirla ile yeniden açar (~3 sn).
    Brave'i başka biri kullanıyorsa (kilit alınamazsa) dokunmaz."""
    if not _cdp_hazir(cdp_url=cdp_url):
        return False
    try:
        with brave_kilidi(kilit_bekle, kilit_yolu):
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                b = pw.chromium.connect_over_cdp(cdp_url)
                try:
                    b.new_browser_cdp_session().send("Browser.close")
                except Exception:
                    pass
        from .. import gunluk
        gunluk.yaz("bilgi", "tarayici", "Brave kapatıldı", olay="brave_kapat", tarayici="Brave")
        return True
    except Exception:
        return False


def _cdp_hazir(zaman_asimi: float = 2.0, cdp_url: str = CDP) -> bool:
    try:
        with urllib.request.urlopen(cdp_url + "/json/version", timeout=zaman_asimi) as r:
            return r.status == 200
    except Exception:
        return False


def brave_hazirla(
    bekle: float = 30.0,
    port: int = CDP_PORT,
    profil_dizini: Path | str | None = None,
    path: str | None = None,
) -> bool:
    """Brave CDP hazır değilse başlat ve en çok `bekle` saniye bekle.
    Brave bulunamazsa çökmez, `False` döner.
    """
    cdp_url = f"http://localhost:{port}"
    if _cdp_hazir(cdp_url=cdp_url):
        return True
    # 15.11 canlı: 5 paralel işçi Brave'i AYNI ANDA başlatıyordu → ilki açıyor, diğerleri "mevcut oturumda açılıyor"
    # deyip her biri fazladan about:blank penceresi açıyor (uçuş taramasında 11 boş pencere), biri ikinci Brave
    # süreci kuruyordu ([::1]:9334) ve tarama sonu brave_kapat onu kapatamıyordu. Başlatma tek iş parçacığında olur.
    with _BASLAT_KILIDI:
        if _cdp_hazir(cdp_url=cdp_url):
            return True
        return _brave_baslat(cdp_url, bekle, port, profil_dizini, path)


_BASLAT_KILIDI = threading.Lock()


def _brave_baslat(cdp_url: str, bekle: float, port: int, profil_dizini, path) -> bool:
    komut = brave_komutu(profil_dizini=profil_dizini, port=port, path=path)
    if not komut:
        return False
    try:
        profil = Path(profil_dizini).expanduser() if profil_dizini else _profil_sec(brave_yolu_bul(path=path))
        profil.mkdir(parents=True, exist_ok=True)
        try:   # Brave kapalıyken: ürün analizi kapalı, bildirim/açılır pencere engelli (Ayarlar → tarama.json)
            from .profil import profil_tohumla
            profil_tohumla(profil)
        except Exception:
            pass
        subprocess.Popen(komut, start_new_session=True)
    except Exception as e:
        from .. import gunluk
        gunluk.yaz("hata", "tarayici", f"Brave başlatılamadı: {type(e).__name__}: {e}", olay="brave_baslat")
        return False
    from .. import gunluk
    gunluk.yaz("bilgi", "tarayici", f"Brave başlatıldı (CDP {port})", olay="brave_baslat", tarayici="Brave")
    bitis = time.time() + bekle
    while time.time() < bitis:
        time.sleep(1.0)
        if _cdp_hazir(cdp_url=cdp_url):
            return True
    return False


def sekme_fabrikasi(veritabani: Any = None, bildirim_fn: Callable[[dict[str, Any]], Any] | None = None):
    """Çok sekmeli havuz için işçi Tarayici üreticisi: her işçi kendi CDP bağlantısı/sekmeleriyle, ortak site bekleme
    durumuyla (`_son_istek` paylaşılır → aynı siteye işçiler arası da 2,5 sn kuralı) ve paylaşımlı Brave kilidiyle."""
    from .oto_hiz import OtoHiz
    ortak_son_istek: dict[str, float] = {}
    ortak_hiz = OtoHiz(alt_sn=2.0, ust_sn=60.0, baslangic_sn=2.5)    # tüm sekmeler aynı site öğrenmesini paylaşır

    def _uret(no: int) -> "Tarayici":
        t = Tarayici(veritabani=veritabani, bildirim_fn=bildirim_fn)
        t._son_istek = ortak_son_istek
        t.oto_hiz = ortak_hiz
        # 15.11 canlı hata: K0 düşünce K1 (başsız Chromium) aynı iş parçacığında İKİNCİ sync_playwright açıyor →
        # "Sync API inside the asyncio loop" ve o işçinin sonraki tüm siteleri düşüyordu (7/17 site). Başsız Chromium
        # deneyde zaten neredeyse hiçbir sitede çalışmadı → işçilerde K1 kapalı (K0 → K2).
        t.kademe.k1 = None
        t.kilit_paylasimli = True
        return t
    return _uret



# 15.11: animasyon/geçiş kapatma — "hareketi azalt" erişilebilirlik tercihi (sıradan kullanıcı ayarı) + aynı etkili CSS.
_ANIMASYON_CSS = ("*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;"
                  "animation-iteration-count:1!important;transition:none!important;scroll-behavior:auto!important}")
_ANIMASYON_JS = """(() => { const ekle = () => { if (document.getElementById('kazi-hareket')) return;
  const s = document.createElement('style'); s.id = 'kazi-hareket'; s.textContent = %s;
  (document.head || document.documentElement).appendChild(s); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ekle, {once: true}); else ekle(); })();"""


def animasyonlari_kapat(pg: Any) -> None:
    try:
        pg.emulate_media(reduced_motion="reduce")
    except Exception:
        pass
    try:
        pg.add_init_script(_ANIMASYON_JS % json.dumps(_ANIMASYON_CSS))
    except Exception:
        pass


def bot_gecmisi_var(site: str, saat: int = 24) -> bool:
    """Kayıt defterinde son `saat` saatte bu site için doğrulama sayfası (dogrulama_gerekli) var mı?"""
    try:
        from .. import gunluk
        from datetime import datetime, timedelta
        b = gunluk._baglanti()
        if b is None:
            return False
        sinir = (datetime.now(gunluk.TZ) - timedelta(hours=saat)).isoformat(timespec="seconds")
        anahtar = str(site).lower().removeprefix("www.").split(".")[0]
        if not anahtar:
            return False
        r = b.execute("SELECT 1 FROM kayit WHERE olay = 'dogrulama_gerekli' AND zaman >= ? AND lower(site) LIKE ? LIMIT 1",
                      (sinir, f"%{anahtar}%")).fetchone()
        return r is not None
    except Exception:
        return False

class Tarayici:
    """Eski `Tarayici` arayüzünü koruyan, içte `Kademe` kullanan tarayıcı sınıfı."""

    def __init__(
        self,
        min_aralik: float = 2.5,
        jitter: float = 0.5,
        durum_dosya: Path | str | None = None,
        veritabani: Any = None,
        bildirim_fn: Callable[[dict[str, Any]], Any] | None = None,
        bekle_k3: float | None = None,
        k1: Any = None,
        zaman_fn: Callable[[], float] | None = None,
        uyku_fn: Callable[[float], None] | None = None,
        simdi_fn: Callable[[], datetime] | None = None,
    ) -> None:
        if bekle_k3 is None:
            # 15.11: Ayarlar → "Doğrulama çıkınca bekle" (varsayılan 0: beklemeden geç). Önceden sabit 300 sn'ydi;
            # otomatik taramada her doğrulama sayfasında 5 dk boş bekleniyordu.
            from .paralel_ayar import dogrulama_bekle_sn
            bekle_k3 = dogrulama_bekle_sn()
        self.min_aralik = min_aralik
        self.jitter = jitter
        self.durum_dosya = Path(durum_dosya) if durum_dosya else DURUM_DOSYA
        self.veritabani = veritabani
        self.bildirim_fn: Callable[[dict[str, Any]], Any] = bildirim_fn or varsayilan_bildirim
        self._zaman_fn = zaman_fn or time.monotonic
        self._uyku_fn = uyku_fn or time.sleep
        self._simdi_fn = simdi_fn or (lambda: datetime.now(TZ_ISTANBUL))
        self._son_istek: dict[str, float] = {}
        self._durum: dict[str, Any] = _oku(self.durum_dosya, {})
        self._pw = None
        self._browser = None
        self._ctx = None
        self._sekmeler: dict[str, Any] = {}
        self.bildir_kanca: Callable[[str, str], Any] | None = None
        self.son_arama_secici: str | None = None
        self.kilit_yolu: Path | str | None = KILIT_YOL
        self.kilit_zaman_asimi: float = 900.0
        # Çok sekmeli havuzda Brave kilidini havuz (ana iş parçacığı) tutar; işçiler kilit almaz (15.11)
        self.kilit_paylasimli: bool = False
        self.oto_hiz: Any = None                 # OtoHiz (S2): varsa sabit 2,5 sn yerine siteye göre öğrenen bekleme
        self._yonlendirilen: set[int] = set()     # reklam/görsel engeli kurulmuş sekmeler (bu bağlantıda)
        self._kilit = None
        self.cdp_url: str = CDP
        self.brave_profil: Path | None = None        # None = otomatik (flatpak'te kalıcı veri klasörü)
        self.k2_durumu: str = "hazir"

        from .yonetici import Kademe

        self.kademe = Kademe(
            veritabani=veritabani,
            bildirim_fn=self._olay_ilet,
            tarayici=self,
            k1=k1,
            durum_dosya=self.durum_dosya,
            bekle_k3=bekle_k3,
            min_aralik=min_aralik,
            jitter=jitter,
            zaman_fn=zaman_fn,
            uyku_fn=uyku_fn,
            simdi_fn=simdi_fn,
        )

    @property
    def gunluk_adi(self) -> str:
        """Kayıt defterinde görünen tarayıcı adı (alt sınıflar ezer)."""
        return "Brave (kazı)"

    def _olay_ilet(self, olay: dict[str, Any]) -> None:
        try:
            self.bildirim_fn(olay)
        except Exception:
            pass
        from .. import gunluk
        gunluk.tarayici_olayi(olay, self.gunluk_adi if not olay.get("katman") else None)
        if olay.get("tur") == "dogrulama_gerekli" and self.bildir_kanca is not None:
            try:
                self.bildir_kanca(str(olay.get("site") or ""), str(olay.get("url") or ""))
            except TypeError:
                try:
                    self.bildir_kanca(str(olay.get("site") or ""))  # type: ignore[call-arg]
                except Exception:
                    pass
            except Exception:
                pass

    # -------------------------------------------------- durum / hız sınırı
    def durum(self, site: str) -> dict[str, Any]:
        return self._durum.setdefault(site, {})

    def kaydet(self) -> None:
        _yaz(self.durum_dosya, self._durum)

    def hiz_sinirla(self, site: str) -> None:
        """Site başına bekleme: OtoHiz varsa öğrenen (siteye göre), yoksa min_aralik (+rastgele jitter)."""
        if self.oto_hiz is not None:
            self.oto_hiz.bekle(site)
            return
        once = self._son_istek.get(site)
        if once is not None:
            gecen = self._zaman_fn() - once
            gereken = self.min_aralik + (random.uniform(0, self.jitter) if self.jitter > 0 else 0.0)
            if gecen < gereken:
                self._uyku_fn(gereken - gecen)
        self._son_istek[site] = self._zaman_fn()

    def _brave_zorunlu(self, site: str) -> bool:
        if hasattr(self, "kademe") and self.kademe is not None:
            if self.kademe.site_bilgisi(site).katman_tercihi in (Katman.K2, Katman.K3):
                return True
        d = self.durum(site)
        if d.get("http_ardisik_blok", 0) < 3:
            return False
        son = d.get("son_http_blok")
        if not son:
            return False
        try:
            son_dt = datetime.fromisoformat(son)
            simdi = self._simdi_fn()
            if son_dt.tzinfo is None and simdi.tzinfo is not None:
                simdi = simdi.replace(tzinfo=None)
            elif son_dt.tzinfo is not None and simdi.tzinfo is None:
                son_dt = son_dt.replace(tzinfo=None)
            return simdi - son_dt < timedelta(hours=24)
        except Exception:
            return False

    def _basari(self, site: str, yontem: str) -> None:
        d = self.durum(site)
        d["son_yontem"] = yontem
        d["son_basari"] = self._simdi_fn().isoformat(timespec="seconds")
        d["ardisik_hata"] = 0
        if yontem == "http":
            d["http_ardisik_blok"] = 0
        self.kaydet()

    def _hata(self, site: str, sebep: str, http_blok: bool = False) -> None:
        d = self.durum(site)
        simdi_str = self._simdi_fn().isoformat(timespec="seconds")
        d["ardisik_hata"] = int(d.get("ardisik_hata", 0)) + 1
        d["son_hata"] = f"{simdi_str} {sebep}"
        if http_blok:
            d["http_ardisik_blok"] = int(d.get("http_ardisik_blok", 0)) + 1
            d["son_http_blok"] = simdi_str
        self.kaydet()

    # -------------------------------------------------- HTTP (K0)
    def http_getir(self, url: str, site: str = "", zaman_asimi: float = 40.0) -> tuple[int, str]:
        istek = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept-Language": "tr-TR,tr;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "gzip",
            },
        )
        with urllib.request.urlopen(istek, timeout=zaman_asimi) as r:
            govde = r.read()
            if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                govde = gzip.decompress(govde)
            try:
                return r.status, govde.decode("utf-8")
            except UnicodeDecodeError:
                return r.status, govde.decode("cp1254", "replace")

    # -------------------------------------------------- Brave oturumu (K2)
    @property
    def brave_acik(self) -> bool:
        """Brave kademesi bağlı mı (Playwright bağlantısı ya da kilit tutuluyor)?"""
        return self._browser is not None or self._kilit is not None

    def brave_birak(self) -> None:
        """Brave'i serbest bırak: site sekmelerini ve Playwright bağlantısını kapat,
        süreçler arası kilidi bırak.
        """
        self.kapat()

    def _brave_kilidi_al(self) -> None:
        """Brave kademesine girmeden önce süreçler arası kilidi al."""
        if self._kilit is not None or self.kilit_paylasimli:
            return
        self._kilit = brave_kilidi(self.kilit_zaman_asimi, self.kilit_yolu)
        try:
            self._kilit.__enter__()
        except Exception:
            self._kilit = None
            raise

    def _brave_baglan(self) -> Any:
        if self._browser is not None:
            return self._browser
        if not _cdp_hazir(cdp_url=self.cdp_url) and brave_yolu_bul() is None:
            self.k2_durumu = "kullanılamıyor"
            self._olay_ilet({
                "tur": "k2_kullanilamiyor",
                "katman": "K2",
                "mesaj": "Brave bulunamadı, K2 kullanılamıyor",
            })
            raise BotEngeli("brave", "kullanılamıyor")
        self._brave_kilidi_al()
        if not brave_hazirla(profil_dizini=self.brave_profil):
            self.k2_durumu = "kullanılamıyor"
            raise BotEngeli("brave", self.cdp_url)
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
        except Exception:      # C2 #3: başarısız bağlantıda Playwright sürücüsü açık kalmasın
            try:
                self._pw.stop()
            finally:
                self._pw = None
            raise
        self._ctx = (
            self._browser.contexts[0]
            if self._browser.contexts
            else self._browser.new_context()
        )
        self.k2_durumu = "hazir"
        if not self.kilit_paylasimli:     # paralel işçi temizlerse DİĞER işçilerin pencerelerini kapatıyordu (TargetClosedError)
            self._sekme_temizle()
        pencereleri_ayarla(self._browser)          # kullanıcı ayarı: göster/gizle (tarama.json)
        return self._browser

    def _sekme_temizle(self, azami_fazla: int = 6) -> None:
        """Bağlamdaki sekme yığınını sınırla: ilk sekme + en çok `azami_fazla`."""
        try:
            sayfalar = list(self._ctx.pages)
        except Exception:
            return
        for pg in sayfalar[1 + azami_fazla :]:
            try:
                pg.close()
            except Exception:
                pass

    def _gunluk_dinle(self, pg: Any) -> None:
        """Kayıt defteri (15.11): bu sayfanın ana çerçeve gezinmeleri + belge düzeyi HTTP hataları + çökme."""
        if getattr(pg, "_kazi_gunluk", False):
            return
        try:
            from .. import gunluk
            ad = self.gunluk_adi

            def _gezinme(fr: Any) -> None:
                try:
                    if fr is pg.main_frame and fr.url and not fr.url.startswith("about:"):
                        yol = urllib.parse.urlsplit(fr.url)
                        kisa = (yol.path or "/") + ("?" + yol.query if yol.query else "")
                        gunluk.yaz("bilgi", "tarayici", f"sayfa açıldı: {kisa[:140]}", olay="gezinme", site=site_adi(fr.url),
                                   tarayici=ad, url=fr.url)
                except Exception:
                    pass

            def _yanit(r: Any) -> None:
                try:
                    if r.status >= 400 and r.request.resource_type == "document" and r.frame is pg.main_frame:
                        gunluk.yaz("uyari" if r.status < 500 else "hata", "tarayici", f"HTTP {r.status}",
                                   olay="http_hata", site=site_adi(r.url), tarayici=ad, url=r.url,
                                   veri={"durum": r.status})
                except Exception:
                    pass
            pg.on("framenavigated", _gezinme)
            pg.on("response", _yanit)
            pg.on("crash", lambda *_: gunluk.yaz("hata", "tarayici", "sekme çöktü", olay="cokme", tarayici=ad,
                                                   url=getattr(pg, "url", None)))
            pg._kazi_gunluk = True
        except Exception:
            pass

    def _sayfa_hazirla(self, pg: Any, site: str | None = None) -> Any:
        """Ayarlar → reklam/izleyici, görsel, medya, yazı tipi, widget, GIF, gömülü içerik engeli ve animasyon kapatma:
        sekme başına bir kez. `bot_muafiyeti`: son 24 sa'te bu sitede doğrulama sayfası çıktıysa YALNIZ reklam engeli
        (sayfa sıradan ziyaretçideki gibi tam yüklenir; 15.11)."""
        self._gunluk_dinle(pg)
        if id(pg) in self._yonlendirilen:
            return pg
        try:
            from .paralel_ayar import paralel_ayarlar
            from . import reklam_engel
            a = dict(paralel_ayarlar())
            if site and a.get("bot_muafiyeti") and bot_gecmisi_var(site):
                for k in ("gorsel_engelle", "medya_engelle", "yazi_tipi_engelle", "widget_engelle", "gif_engelle",
                          "gomulu_engelle", "animasyon_kapat"):
                    a[k] = False
                from .. import gunluk
                gunluk.yaz("bilgi", "tarayici", "son 24 sa'te doğrulama çıkmış: bu sitede yalnız reklam engeli (tam yükleme)",
                           olay="bot_muafiyeti", site=site, tarayici=self.gunluk_adi)
            if any(a[k] for k in ("reklam_engelle", "gorsel_engelle", "medya_engelle", "yazi_tipi_engelle",
                                  "widget_engelle", "gif_engelle", "gomulu_engelle")):
                reklam_engel.kur(pg, reklam=a["reklam_engelle"], gorsel=a["gorsel_engelle"],
                                 gorsel_turleri=reklam_engel.TUR_GORSEL, medya=a["medya_engelle"],
                                 yazi_tipi=a["yazi_tipi_engelle"], widget=a["widget_engelle"],
                                 gif=a["gif_engelle"], gomulu=a["gomulu_engelle"])
            if a.get("animasyon_kapat"):
                animasyonlari_kapat(pg)
            self._yonlendirilen.add(id(pg))          # C2 #7: yalnız başarıdan sonra
        except Exception as e:
            self._olay_ilet({"tur": "reklam_engeli_kurulamadi", "hata": f"{type(e).__name__}: {e}"})
        return pg

    def _yeni_sayfa(self) -> Any:
        """Paralel işçide her site AYRI PENCEREDE açılır (15.11): aynı penceredeki arka plan sekmeleri çizilmiyor →
        kaydırınca yüklenen kartlar (Hepsiburada) hiç gelmiyordu (5 sekmeli GPU taramasında 71 → 0). Sıralı yolda sekme."""
        if self.kilit_paylasimli and self._browser is not None and hasattr(self._browser, "new_browser_cdp_session"):
            try:
                # Her bağlantı TÜM yeni sayfaları görür: aynı anda pencere açan iki işçi birbirinin penceresini kapabiliyordu
                # (TargetClosedError) → benzersiz işaretli adres, yalnız kendi penceresini al.
                import uuid
                isaret = f"about:blank#kazi-{uuid.uuid4().hex}"
                hedef_id = None
                try:
                    with self._ctx.expect_page(lambda p: p.url == isaret, timeout=10000) as bilgi:
                        s = self._browser.new_browser_cdp_session()
                        try:
                            hedef_id = s.send("Target.createTarget", {"url": isaret, "newWindow": True,
                                                                      "width": 1366, "height": 900}).get("targetId")
                        finally:
                            try:
                                s.detach()
                            except Exception:
                                pass
                    return bilgi.value
                except Exception:
                    if hedef_id:      # açılmış ama sahiplenilememiş pencere boşta kalmasın (15.11, kullanıcı: about:blank birikiyor)
                        sayfa_kapat_cdp(hedef_id, self.cdp_url)
                    raise
            except Exception:
                pass
        return self._ctx.new_page()

    def _sekme(self, site: str) -> Any:
        return self._sayfa_hazirla(self._sekme_bul(site), site)

    @staticmethod
    def _anahtar_eslesir(is_sitesi: str, anahtar: str) -> bool:
        """İş anahtarı ("enuygun", "hepsiburada") ↔ sekme anahtarı ("enuygun.com", "www.kayak.com.tr")."""
        a = str(is_sitesi or "").lower().removeprefix("www.")
        k = str(anahtar or "").lower().removeprefix("www.")
        if not a or not k:
            return False
        return a == k or k.split(".")[0] == a or a.split(".")[0] == k.split(".")[0]

    def sekme_kapat(self, site: str) -> int:
        """Biten sitenin bu işçideki pencerelerini kapat (15.11: işi biten site pencereleri tarama sonuna kadar açık
        kalıyordu). Kapatılan sayısını döndürür. Başka işçinin/kullanıcının sekmelerine dokunmaz (yalnız `_sekmeler`)."""
        n = 0
        for anahtar in [k for k in self._sekmeler if self._anahtar_eslesir(site, k)]:
            pg = self._sekmeler.pop(anahtar, None)
            if pg is None:
                continue
            self._yonlendirilen.discard(id(pg))
            try:
                if not pg.is_closed():
                    pg.close()
                    n += 1
            except Exception:
                pass
        if n:
            from .. import gunluk
            gunluk.yaz("bilgi", "tarayici", f"işi biten sitenin penceresi kapatıldı ({n})", olay="pencere_kapat",
                       site=site, tarayici=self.gunluk_adi)
        return n

    def _sekme_bul(self, site: str) -> Any:
        self._brave_baglan()
        pg = self._sekmeler.get(site)
        if pg is not None and not pg.is_closed():
            return pg
        # paralel işçi başkasının/eski taramanın sekmesini sahiplenmez (o sekme arka planda kalır, çizilmez) → kendi penceresi
        for aday in ([] if self.kilit_paylasimli else (getattr(self._ctx, "pages", []) or [])):
            try:
                if aday.is_closed():
                    continue
                if _site_eslesir(site, aday.url):
                    self._sekmeler[site] = aday
                    return aday
            except Exception:
                continue
        pg = self._yeni_sayfa()
        pencereleri_ayarla(self._browser)
        self._sekmeler[site] = pg
        return pg

    def _cerez_reddet(self, pg: Any, site: str) -> None:
        """Ayar açıksa çerez penceresinde 'Tümünü reddet'e bas (profil kalıcı → seçim hatırlanır, pencere bir daha çıkmaz)."""
        try:
            from .profil import cerezleri_reddet, gizlilik_ayarlari
            if not gizlilik_ayarlari()["cerez_reddet"]:
                return
            basilan = cerezleri_reddet(pg)
        except Exception:
            return
        if basilan:
            self._olay_ilet({"tur": "cerez_reddedildi", "site": site, "dugme": basilan})

    def _hazir_bekle(self, pg: Any, kutu_secicileri: Any, site: str, ust_ms: int = 8000) -> None:
        """Ana sayfa: arama kutusu (hafızadaki ya da sitenin seçicisi) görünür olduğu an dön; yoksa en çok `ust_ms`."""
        seciciler = [str(x) for x in (kutu_secicileri or ()) if isinstance(x, str)]
        try:
            from .secici_hafiza import oku as _hafiza_oku
            h = _hafiza_oku(site)
            if h:
                seciciler.insert(0, h)
        except Exception:
            pass
        if not seciciler:
            try:
                pg.wait_for_load_state("load", timeout=ust_ms)
            except Exception:
                pass
            return
        try:
            pg.wait_for_selector(", ".join(seciciler), state="visible", timeout=ust_ms)
        except Exception:
            pass

    def _popup_kapat(self, pg: Any, site: str) -> None:
        """Sitenin açılır penceresini (Trendyol kadın/erkek seçimi vb.) kapat — arama kutusunun önünü açar."""
        try:
            from .profil import acilir_kapat
            from ..siteler import SITELER
            ozel = tuple(getattr(SITELER.get(site), "POPUP_KAPAT", ()) or ())
            basilan = acilir_kapat(pg, ozel)
        except Exception:
            return
        if basilan:
            self._olay_ilet({"tur": "popup_kapatildi", "site": site, "dugme": basilan})

    def brave_getir(
        self,
        url: str,
        site: str = "",
        bekle_alt: float = 2.0,
        bekle_ust: float = 4.0,
        kaydir: bool = True,
    ) -> str:
        site = site or site_adi(url)
        pg = self._sekme(site)
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            try:
                pg.goto(url, wait_until="commit", timeout=60000)
            except Exception:
                pass
        pg.wait_for_timeout(int(random.uniform(bekle_alt, bekle_ust) * 1000))
        self._cerez_reddet(pg, site)
        self._popup_kapat(pg, site)
        if kaydir:
            try:
                pg.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.6)")
                pg.wait_for_timeout(1200)
                pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                pg.wait_for_timeout(800)
            except Exception:
                pass
        return pg.content()

    def brave_fetch(
        self,
        site_ana_url: str,
        istek_url: str,
        site: str | None = None,
        yontem: str = "GET",
    ) -> str:
        """Sekme site ana sayfasındayken sayfa içinden fetch: çerez/oturum geçerli kalır."""
        site = site or site_adi(site_ana_url)
        pg = self._sekme(site)
        if not pg.url.startswith(site_ana_url.rstrip("/")):
            try:
                pg.goto(site_ana_url, wait_until="domcontentloaded", timeout=60000)
                pg.wait_for_timeout(2000)
            except Exception:
                pass
        sonuc = pg.evaluate(
            """async ([u, m]) => {
                const r = await fetch(u, {method: m, credentials: 'include',
                    headers: {'Accept': 'application/json, text/html;q=0.9, */*;q=0.8'}});
                return {durum: r.status, metin: await r.text()};
            }""",
            [istek_url, yontem],
        )
        return (sonuc or {}).get("metin") or ""

    # -------------------------------------------------- site içi arama (§2.7)
    _KUTU_ROLLERI = ("searchbox", "combobox", "textbox")

    def _kutu_adaylari(self, pg: Any, kutu_secicileri: Any):
        for s in kutu_secicileri or ():
            yield f"secici:{s}", pg.locator(s).first
        for ph in (
            "Ara",
            "Ürün, kategori veya marka ara",
            "Ürün ara",
            "Marka veya ürün ara",
            "Search",
        ):
            yield f"placeholder:{ph}", pg.get_by_placeholder(ph, exact=False).first
        for rol in self._KUTU_ROLLERI:
            yield f"role:{rol}", pg.get_by_role(rol).first
        for s in (
            '[role="dialog"] input',
            'input[type="search"]',
            "input#search-input",
            'input[name="q"]',
            'input[type="text"]',
        ):
            yield f"gorunen:{s}", pg.locator(s).first

    _SEZGISEL_KUTU = (
        'input[type="search"]',
        '[role="searchbox"]',
        'input[placeholder*="ara" i]',
        'input[placeholder*="search" i]',
        'input[name="q"]',
        'input[name*="search" i]',
        'input[aria-label*="ara" i]',
    )
    _SEZGISEL_TETIK = (
        'div[role="search"] button',
        'form[role="search"] button',
        'button[aria-haspopup="dialog"]',
        'button[aria-label*="ara" i]',
        'button[aria-label*="search" i]',
        'button[class*="search" i]',
        '[class*="search-box"]',
        '[class*="searchBox"]',
    )

    @staticmethod
    def _kutu_kullanilabilir(loc: Any) -> bool:
        """Var, görünür ve etkin mi (is_enabled desteklenmiyorsa etkin sayılır)."""
        try:
            if not (loc.count() > 0 and loc.is_visible()):
                return False
        except Exception:
            return False
        try:
            return bool(loc.is_enabled())
        except AttributeError:
            return True
        except Exception:
            return False

    def _kutu_tara(self, pg: Any, kutu_secicileri: Any):
        for etiket, loc in self._kutu_adaylari(pg, kutu_secicileri):
            if self._kutu_kullanilabilir(loc):
                return etiket, loc
        for s in self._SEZGISEL_KUTU:
            loc = pg.locator(s).first
            if self._kutu_kullanilabilir(loc):
                return f"sezgisel:{s}", loc
        return None, None

    @staticmethod
    def _etiket_secici(etiket: str | None) -> str | None:
        """Etiketten kaydedilebilir CSS seçicisi (rol/placeholder etiketleri kaydedilmez)."""
        for onek in ("secici:", "gorunen:", "sezgisel:"):
            if etiket and etiket.startswith(onek):
                return etiket[len(onek):]
        return None

    def _kutu_bul(self, pg: Any, kutu_secicileri: Any, site: str | None = None):
        """Sıra: hafıza -> site KUTU -> rol/placeholder -> genel sezgisel -> arama düğmesine tıkla."""
        from . import secici_hafiza
        if site:
            kayitli = secici_hafiza.oku(site)
            if kayitli:
                try:
                    loc = pg.locator(kayitli).first
                except Exception:
                    loc = None
                if loc is not None and self._kutu_kullanilabilir(loc):
                    secici_hafiza.kaydet(site, kayitli)     # başarı sayısı artar
                    return f"hafiza:{kayitli}", loc
                secici_hafiza.unut(site)      # site tasarımı değişmiş: unut, aramaya devam
        etiket, loc = self._kutu_tara(pg, kutu_secicileri)
        if loc is not None:
            sec = self._etiket_secici(etiket)
            if site and sec:
                secici_hafiza.kaydet(site, sec)
            return etiket, loc
        for tetik in self._SEZGISEL_TETIK:
            try:
                b = pg.locator(tetik).first
                if not self._kutu_kullanilabilir(b):
                    continue
                b.click(timeout=4000)
                pg.wait_for_timeout(900)
                etiket2, loc2 = self._kutu_tara(pg, kutu_secicileri)
                if loc2 is not None:
                    return f"{tetik} -> {etiket2}", loc2
            except Exception:
                continue
        return None, None

    def _dogrulama_firsati(self, pg: Any, site: str, url: str) -> bool:
        """Bot sayfası (15.11): kısa fırsat süresi + kullanıcının arayüzden 'Elle doğrula' isteği boyunca AYNI sekmede bekle.
        Doğrulamayı yalnız kullanıcı geçer (otomatik atlatma yok). Sayfa temizlenirse True."""
        if pg is None or getattr(self, "_dogrulama_icinde", False):
            return False
        from . import dogrulama
        from .. import gunluk
        bas = time.time()
        ayar = float(getattr(self.kademe, "bekle_k3", 0) or 0)
        sinir = dogrulama.bekleme_bitisi(site, bas, ayar)
        gunluk.yaz("uyari", "tarayici", f"doğrulama sayfasında bekleniyor ({int(sinir - bas)} sn) — arayüzden "
                   "'Elle doğrula' ile süre uzar", olay="dogrulama_bekleniyor", site=site, tarayici=self.gunluk_adi,
                   url=url, veri={"kalan_sn": int(sinir - bas), "bitis": sinir})
        while time.time() < sinir:
            try:
                pg.wait_for_timeout(2000)
                html = pg.content()
            except Exception:
                return False
            if not blok_mu(html):
                gunluk.yaz("bilgi", "tarayici", f"doğrulama geçildi ({int(time.time() - bas)} sn sonra), tarama sürüyor",
                           olay="dogrulama_gecildi", site=site, tarayici=self.gunluk_adi, url=url)
                dogrulama.istek_sil(site)
                return True
            yeni = dogrulama.bekleme_bitisi(site, bas, ayar)
            if yeni > sinir + 1:
                gunluk.yaz("uyari", "tarayici", f"elle doğrulama bekleniyor ({int(yeni - time.time())} sn kaldı)",
                           olay="dogrulama_bekleniyor", site=site, tarayici=self.gunluk_adi, url=url,
                           veri={"kalan_sn": int(yeni - time.time()), "bitis": yeni})
            sinir = yeni
        gunluk.yaz("uyari", "tarayici", f"doğrulama {int(time.time() - bas)} sn içinde geçilmedi — site bu turda atlandı",
                   olay="dogrulama_zaman_asimi", site=site, tarayici=self.gunluk_adi, url=url)
        return False

    def site_ici_ara(
        self,
        site: str,
        ana_url: str,
        sorgu: str,
        kutu_secicileri: Any = (),
        sonuc_bekle_secici: str | None = None,
        ag_desen: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Ana sayfayı aç, site içi arama kutusuna yaz, Enter'a bas."""
        pg = self._sekme(site)
        self.hiz_sinirla(site)

        yanitlar: list[dict[str, Any]] = []
        rx = re.compile(ag_desen) if ag_desen else None

        def _yanit(resp: Any) -> None:
            try:
                if rx is None or not rx.search(resp.url):
                    return
                for j in yanit_jsonlari(resp):
                    yanitlar.append({"url": resp.url, "durum": resp.status, "json": j})
            except Exception:
                pass

        if rx is not None:
            pg.on("response", _yanit)
        try:
            # Hız (15.11): sekme zaten bu sitedeyse ana sayfaya dönme — aynı arama kutusuna yeni sorgu yazılır.
            # Sabit 2–4 sn uyku yerine: arama kutusu görünür olduğu an devam (üst sınır 8 sn) + kısa insani duraklama.
            ayni_site = bool(pg.url) and not pg.url.startswith("about:") and _site_eslesir(site, pg.url)
            if not ayni_site:
                try:
                    pg.goto(ana_url, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pg.goto(ana_url, wait_until="commit", timeout=60000)
                self._hazir_bekle(pg, kutu_secicileri, site)
            pg.wait_for_timeout(int(random.uniform(300, 700)))
            self._cerez_reddet(pg, site)
            self._popup_kapat(pg, site)
            try:
                pg.evaluate("window.scrollTo(0, 0)")
            except Exception:
                pass

            etiket, kutu = self._kutu_bul(pg, kutu_secicileri, site)
            if kutu is None and ayni_site:     # eski sonuç sayfasında kutu farklı olabilir → ana sayfaya dön (15.11 idefix)
                try:
                    pg.goto(ana_url, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
                self._hazir_bekle(pg, kutu_secicileri, site)
                etiket, kutu = self._kutu_bul(pg, kutu_secicileri, site)
            if kutu is None:      # geç açılan pencere kutuyu örtüyor olabilir: bir kez daha kapat ve yeniden ara
                self._cerez_reddet(pg, site)
                self._popup_kapat(pg, site)
                pg.wait_for_timeout(1500)
                etiket, kutu = self._kutu_bul(pg, kutu_secicileri, site)
            if kutu is None:
                raise RuntimeError(f"arama kutusu yok: {site}")
            self.son_arama_secici = etiket

            try:
                kutu.click(timeout=5000)
            except Exception:
                pass
            try:
                kutu.fill("")
            except Exception:
                pass
            eski_url = pg.url
            pg.keyboard.type(sorgu, delay=random.randint(60, 120))
            pg.keyboard.press("Enter")

            # Sonuç: sabit 2,5–4 sn yerine olay bekleme — adres değişimi → DOM → (seçici | ağ sakinliği), hepsi üst sınırlı
            try:
                pg.wait_for_url(lambda u: u != eski_url, timeout=10000)
            except Exception:
                pass
            try:
                pg.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            if sonuc_bekle_secici:
                try:
                    pg.wait_for_selector(sonuc_bekle_secici, timeout=12000)
                except Exception:
                    pass
            else:
                try:
                    pg.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
            pg.wait_for_timeout(int(random.uniform(400, 800)))
            try:
                pg.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
                pg.wait_for_timeout(500)
            except Exception:
                pass

            html = pg.content()
            son_url = pg.url
        finally:
            if rx is not None:
                try:
                    pg.remove_listener("response", _yanit)
                except Exception:
                    pass

        if blok_mu(html):
            self._hata(site, f"site ici arama blok ({son_url})")
            self._olay_ilet({"tur": "dogrulama_gerekli", "site": site, "url": son_url})
            if self._dogrulama_firsati(pg, site, son_url):
                self._dogrulama_icinde = True
                try:
                    return self.site_ici_ara(site, ana_url, sorgu, kutu_secicileri, sonuc_bekle_secici, ag_desen)
                finally:
                    self._dogrulama_icinde = False
            raise BotEngeli(site, son_url)
        self._basari(site, "brave")
        return html, yanitlar

    def brave_ag_yakala(
        self,
        url: str,
        desen: str,
        site: str | None = None,
        bekle: float = 4.0,
    ) -> list[dict[str, Any]]:
        """Sayfa açılırken URL'si `desen` regex'ine uyan JSON yanıtlarını topla."""
        site = site or site_adi(url)
        self._brave_baglan()
        rx = re.compile(desen)
        toplanan: list[dict[str, Any]] = []
        pg = None

        def _yanit(resp: Any) -> None:
            try:
                if not rx.search(resp.url):
                    return
                for j in yanit_jsonlari(resp):
                    toplanan.append({"url": resp.url, "durum": resp.status, "json": j})
            except Exception:
                pass

        try:
            pg = self._ctx.new_page()
            pencereleri_ayarla(self._browser)
            pg.on("response", _yanit)
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(int(bekle * 1000))
            try:
                pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                pg.wait_for_timeout(1500)
            except Exception:
                pass
        finally:
            if pg is not None:
                try:
                    pg.close()
                except Exception:
                    pass
        return toplanan

    def brave_ag_ve_govde(
        self,
        url: str,
        desen: str,
        site: str | None = None,
        azami_bekle: float = 60.0,
        hazir_desen: str | None = None,
        kaydir: bool = True,
        sayfa: Any = None,
        hazir_fn: Callable[[list[dict[str, Any]]], bool] | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Sayfayı aç; `desen`e uyan JSON yanıtlarını VE gövde metnini döndür.
        `hazir_fn(toplanan)` True dönerse (ör. arama API'si "complete" dedi) beklemeden biter (15.11)."""
        site = site or site_adi(url)
        self._brave_baglan()
        kendi_sekme = sayfa is None
        pg = self._ctx.new_page() if kendi_sekme else sayfa
        pencereleri_ayarla(self._browser)
        rx = re.compile(desen)
        hrx = re.compile(hazir_desen) if hazir_desen else None
        toplanan: list[dict[str, Any]] = []

        def _yanit(resp: Any) -> None:
            try:
                if not rx.search(resp.url):
                    return
                for j in yanit_jsonlari(resp):
                    toplanan.append({"url": resp.url, "durum": resp.status, "json": j})
            except Exception:
                pass

        pg.on("response", _yanit)
        metin = ""
        html = ""
        try:
            zaten = False
            try:
                zaten = bool(sayfa is not None and pg.url == url)
            except Exception:
                zaten = False
            if zaten:
                try:
                    pg.reload(wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    pass
            else:
                try:
                    pg.goto(url, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    try:
                        pg.goto(url, wait_until="commit", timeout=60000)
                    except Exception:
                        pass
            bitis = self._zaman_fn() + max(float(azami_bekle), 1.0)
            son_kaydirma = 0.0
            while True:
                pg.wait_for_timeout(1000)
                try:
                    metin = pg.inner_text("body")
                except Exception:
                    metin = ""
                hazir = bool(hrx and metin and hrx.search(metin))
                if not hazir and hazir_fn is not None:
                    try:
                        hazir = bool(hazir_fn(toplanan))
                    except Exception:
                        hazir = False
                if not hazir and hrx is None and hazir_fn is None and toplanan:
                    hazir = True
                if hazir or self._zaman_fn() >= bitis:
                    break
                if kaydir and self._zaman_fn() - son_kaydirma > 8:
                    try:
                        pg.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                    except Exception:
                        pass
                    son_kaydirma = self._zaman_fn()
            try:
                metin = pg.inner_text("body")
            except Exception:
                pass
            try:
                html = pg.content()
            except Exception:
                html = ""
        finally:
            try:
                pg.remove_listener("response", _yanit)
            except Exception:
                pass
            if kendi_sekme:
                try:
                    pg.close()
                except Exception:
                    pass

        if blok_mu(html or metin):
            self._hata(site, f"brave ag+gövde blok ({url})")
            self._olay_ilet({"tur": "dogrulama_gerekli", "site": site, "url": url})
            if not kendi_sekme and self._dogrulama_firsati(pg, site, url):
                self._dogrulama_icinde = True
                try:
                    return self.brave_ag_ve_govde(url, desen, site=site, azami_bekle=azami_bekle, hazir_desen=hazir_desen,
                                                  kaydir=kaydir, sayfa=sayfa, hazir_fn=hazir_fn)
                finally:
                    self._dogrulama_icinde = False
            raise BotEngeli(site, url)
        self._basari(site, "brave")
        return toplanan, metin

    def kapat(self) -> None:
        for pg in list(self._sekmeler.values()):
            try:
                pg.close()
            except Exception:
                pass
        self._sekmeler.clear()
        self._yonlendirilen.clear()
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._browser = self._pw = self._ctx = None
        if self._kilit is not None:
            try:
                self._kilit.__exit__(None, None, None)
            except Exception:
                pass
            self._kilit = None
        if hasattr(self, "kademe") and self.kademe is not None:
            try:
                self.kademe.kapat_k1()
            except Exception:
                pass

    def __enter__(self) -> "Tarayici":
        return self

    def __exit__(self, *_: Any) -> None:
        self.kapat()

    # -------------------------------------------------- ana giriş
    def getir(self, url: str, site: str | None = None, zaman_asimi: float = 40.0) -> Yanit:
        """URL'yi kademeli katman yöneticisi (K0->K1->K2->K3) üzerinden getir."""
        return self.kademe.getir(url, site=site, zaman_asimi=zaman_asimi)
