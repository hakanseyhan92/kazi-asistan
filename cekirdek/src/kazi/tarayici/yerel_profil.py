"""Kullanıcının yerel Brave profili üzerinden son çare tarama (15.11, kullanıcı isteği).

Bot koruması çıkan sitelerde (Brave blok → görünür Chromium da blok) kullanıcının kişisel Brave oturumunun
çerezleriyle bir kez daha denenir. Kişisel profile ASLA yazılmaz ve üzerinde Brave başlatılmaz (Chromium varsayılan
veri klasöründe CDP'yi reddeder, kullanıcının pencereleri kapanabilirdi). Bunun yerine küçük bir KLON tutulur:
yalnız çerez veritabanı ve `Default/Local Storage/` kopyalanır (Preferences, Local State, Login Data, History, Web Data,
Yer imleri, eklentiler KOPYALANMAZ). 15.11 canlı: Preferences kopyalanınca klon kullanıcının eklentilerini yüklemeye ve
Brave Sync'e bağlanmaya çalıştı → ayarlar artık kopyalanmaz, klonun kendi temiz ayarları `profil_tohumla` ile yazılır. Linux'ta çerez şifresi anahtarlıktaki uygulama anahtarıyla
çözüldüğü için klondaki çerezler okunur kalır. Klon en çok `KLON_TAZELE_SN` (6 saat) bir yenilenir.
Gizleme, parmak izi/UA değişikliği, stealth ve CAPTCHA çözme YOKTUR; yalnız K2 yolu, kendi süreçler arası kilidi.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from ..modeller import Katman
from .cekirdek import BotEngeli, Tarayici, _cdp_hazir, brave_hazirla, brave_kapat, pencereleri_ayarla
from .kilit import brave_kilidi

YEREL_PROFIL = Path.home() / ".var" / "app" / "com.brave.Browser" / "config" / "BraveSoftware" / "Brave-Browser"
YEREL_KLON = Path.home() / ".var" / "app" / "com.brave.Browser" / "data" / "kazi_yerel_klon"
YEREL_PORT = 9335
YEREL_KILIT = Path.home() / ".local" / "share" / "kazi" / "yerel.lock"
KLON_TAZELE_SN = 6 * 3600
ZAMAN_DOSYASI = ".kazi_klon_zamani"
# Yeni Chromium çerezleri Default/Network/Cookies altında tutar; eskiler Default/Cookies
_CEREZ_ADAYLARI = (Path("Default") / "Network" / "Cookies", Path("Default") / "Cookies")
_DUZ_DOSYALAR: tuple[Path, ...] = ()        # ayar dosyaları KOPYALANMAZ (eklenti/Sync sızmasın)
KLON_SURUM = "2"                            # eski (ayar kopyalı) klonlar bir kez tamamen silinir
_SURUM_DOSYASI = ".kazi_klon_surum"
_LOCAL_STORAGE = Path("Default") / "Local Storage"


def _cerez_yolu(kok: Path) -> Path | None:
    for a in _CEREZ_ADAYLARI:
        if (kok / a).is_file():
            return a
    return None


def yerel_profil_var(kaynak: Path | str = YEREL_PROFIL) -> bool:
    """Kullanıcının profilinde çerez veritabanı var mı?"""
    return _cerez_yolu(Path(kaynak)) is not None


def _sqlite_kopyala(kaynak: Path, hedef: Path) -> None:
    """Salt okunur bağlantıdan yedekleme API'siyle tutarlı kopya (çalışan Brave'in DB'si bozulmaz, `-journal` atlanır)."""
    hedef.parent.mkdir(parents=True, exist_ok=True)
    gecici = hedef.with_name(hedef.name + ".kazi-tmp")
    gecici.unlink(missing_ok=True)
    uri = "file:" + urllib.parse.quote(str(kaynak)) + "?mode=ro"
    src = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        dst = sqlite3.connect(str(gecici))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.replace(gecici, hedef)


def _son_tazeleme(hedef: Path) -> float | None:
    try:
        return float((hedef / ZAMAN_DOSYASI).read_text().strip())
    except (OSError, ValueError):
        return None


def klon_tazele(kaynak: Path | str = YEREL_PROFIL, hedef: Path | str = YEREL_KLON, zorla: bool = False,
                simdi: Callable[[], float] = time.time) -> bool:
    """Kaynak profilden klonu yenile (kaynağa yazılmaz). Kopyalandıysa True; taze klon ya da kaynak yoksa False."""
    kaynak, hedef = Path(kaynak), Path(hedef)
    cerez = _cerez_yolu(kaynak)
    if cerez is None:
        return False
    son = _son_tazeleme(hedef)
    try:
        surum_tamam = (hedef / _SURUM_DOSYASI).read_text().strip() == KLON_SURUM
    except OSError:
        surum_tamam = False
    if not zorla and surum_tamam and son is not None and _cerez_yolu(hedef) is not None and 0 <= simdi() - son < KLON_TAZELE_SN:
        return False
    try:
        eski_surum = (hedef / _SURUM_DOSYASI).read_text().strip()
    except OSError:
        eski_surum = None
    if hedef.exists() and eski_surum != KLON_SURUM:
        shutil.rmtree(hedef, ignore_errors=True)      # eski klonda kullanıcının ayarları/eklenti listesi vardı
    hedef.mkdir(parents=True, exist_ok=True)
    (hedef / _SURUM_DOSYASI).write_text(KLON_SURUM)
    for g in _CEREZ_ADAYLARI:                            # eski klondaki çerez konumları temizlensin
        (hedef / g).unlink(missing_ok=True)
    for g in _DUZ_DOSYALAR:
        if (kaynak / g).is_file():
            (hedef / g).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(kaynak / g, hedef / g)
    _sqlite_kopyala(kaynak / cerez, hedef / cerez)
    ls_kaynak = kaynak / _LOCAL_STORAGE
    ls_hedef = hedef / _LOCAL_STORAGE
    shutil.rmtree(ls_hedef, ignore_errors=True)
    if ls_kaynak.is_dir():
        try:
            shutil.copytree(ls_kaynak, ls_hedef, ignore=shutil.ignore_patterns("LOCK"))
        except (OSError, shutil.Error):
            pass                                          # yarım Local Storage çerezleri engellemesin
    (hedef / ZAMAN_DOSYASI).write_text(str(simdi()))
    try:
        from .profil import profil_tohumla
        profil_tohumla(hedef)
    except Exception:
        pass
    return True


class YerelBrave(Tarayici):
    """`Tarayici` ile aynı arayüz; K2 yolu, kullanıcının profilinin KLONUYLA açılan ayrı Brave'e CDP ile bağlanır."""

    @property
    def gunluk_adi(self) -> str:
        return "Brave (yerel profil klonu)"


    def __init__(self, klon: Path | str | None = None, port: int = YEREL_PORT, kilit_dosya: Path | str | None = None,
                 kaynak: Path | str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self._klon = Path(klon) if klon else YEREL_KLON
        self._kaynak = Path(kaynak) if kaynak else YEREL_PROFIL
        self._port = port
        self.cdp_url = f"http://localhost:{port}"
        self._yerel_kilit_yolu = Path(kilit_dosya) if kilit_dosya else YEREL_KILIT
        self._yerel_kilit: Any = None
        self.kilit_paylasimli = True                      # kazı Brave kilidi alınmaz
        self.kademe._denenecek_katmanlar = lambda site: [Katman.K2]
        self.kademe.k1 = None

    def _yerel_kilidi_al(self) -> None:
        if self._yerel_kilit is not None:
            return
        kilit = brave_kilidi(self.kilit_zaman_asimi, self._yerel_kilit_yolu)
        kilit.__enter__()
        self._yerel_kilit = kilit

    def _kilidi_birak(self) -> None:
        if self._yerel_kilit is not None:
            try:
                self._yerel_kilit.__exit__(None, None, None)
            except Exception:
                pass
            self._yerel_kilit = None

    def _brave_baglan(self) -> Any:
        if self._browser is not None:
            return self._browser
        self._yerel_kilidi_al()
        try:
            klon_tazele(self._kaynak, self._klon)
            if not brave_hazirla(port=self._port, profil_dizini=self._klon):
                self.k2_durumu = "kullanılamıyor"
                raise BotEngeli("yerel_brave", self.cdp_url)
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
            self._ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        except Exception:
            self.kapat()
            raise
        self.k2_durumu = "hazir"
        try:
            pencereleri_ayarla(self._browser)
        except Exception:
            pass
        return self._browser

    def kapat(self) -> None:
        for pg in list(self._sekmeler.values()):
            try:
                pg.close()
            except Exception:
                pass
        self._sekmeler.clear()
        self._yonlendirilen.clear()
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        baglanmisti = self._browser is not None
        self._browser = self._pw = self._ctx = None
        if baglanmisti:
            try:
                brave_kapat(cdp_url=self.cdp_url, kilit_yolu=self._yerel_kilit_yolu)
            except Exception:
                pass
        self._kilidi_birak()
