"""Görünür (headless=False) Playwright Chromium yedek katmanı (15.11).

Brave'in engellendiği bazı sitelerde (Epey, İnceHesap, Cimri, Hepsiburada) aynı yöntem (ana sayfa → çerez reddi →
arama kutusu → Enter) görünür Chromium'da çalıştı; başsız Chromium çalışmadı. Gizleme, parmak izi, UA değiştirme,
stealth eklentisi ve CAPTCHA çözme YOKTUR: sıradan bir kullanıcı penceresi.
Yalnız K2 yolu kullanılır; Brave kilidi alınmaz, kendi süreçler arası kilidi vardır.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..modeller import Katman
from .cekirdek import Tarayici
from .kilit import brave_kilidi

CHROMIUM_PROFIL = Path.home() / ".local" / "share" / "kazi" / "chromium_profil"
CHROMIUM_KILIT = Path.home() / ".local" / "share" / "kazi" / "chromium.lock"


class GorunurChromium(Tarayici):
    """`Tarayici` ile aynı arayüz; K2 yolu Brave CDP yerine kalıcı görünür Chromium bağlamına bağlanır."""

    @property
    def gunluk_adi(self) -> str:
        return "Chromium (görünür yedek)"


    def __init__(self, profil: Path | str | None = None, kilit_dosya: Path | str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self._profil = Path(profil) if profil else CHROMIUM_PROFIL
        self._chromium_kilit_yolu = Path(kilit_dosya) if kilit_dosya else CHROMIUM_KILIT
        self._chromium_kilit: Any = None
        self.kilit_paylasimli = True                      # Brave kilidi alınmaz
        self.kademe._denenecek_katmanlar = lambda site: [Katman.K2]
        self.kademe.k1 = None

    def _chromium_kilidi_al(self) -> None:
        if self._chromium_kilit is not None:
            return
        kilit = brave_kilidi(self.kilit_zaman_asimi, self._chromium_kilit_yolu)
        kilit.__enter__()
        self._chromium_kilit = kilit

    def _kilidi_birak(self) -> None:
        if self._chromium_kilit is not None:
            try:
                self._chromium_kilit.__exit__(None, None, None)
            except Exception:
                pass
            self._chromium_kilit = None

    def _brave_baglan(self) -> Any:
        if self._ctx is not None:
            return self._ctx
        self._chromium_kilidi_al()
        try:
            from playwright.sync_api import sync_playwright
            self._profil.mkdir(parents=True, exist_ok=True)
            self._pw = sync_playwright().start()
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(self._profil), headless=False, locale="tr-TR", viewport={"width": 1366, "height": 850})
        except Exception:
            self.kapat()
            raise
        self._browser = self._ctx
        return self._ctx

    def _sekme(self, site: str) -> Any:
        self._brave_baglan()
        pg = self._sekmeler.get(site)
        if pg is None or pg.is_closed():
            # kalıcı bağlam bir boş başlangıç sayfasıyla açılır: onu kullan (yanında boş about:blank kalmasın)
            bos = [p for p in self._ctx.pages if p.url in ("about:blank", "") and p not in self._sekmeler.values()]
            pg = bos[0] if bos else self._ctx.new_page()
            self._sekmeler[site] = pg
        self._gunluk_dinle(pg)
        return pg

    def kapat(self) -> None:
        for pg in list(self._sekmeler.values()):
            try:
                pg.close()
            except Exception:
                pass
        self._sekmeler.clear()
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = self._browser = self._pw = None
        self._kilidi_birak()
