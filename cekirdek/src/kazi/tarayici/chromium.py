"""K1 katmanı: Playwright'ın paketli Chromium'u (başsız, ek bayrak yok).

Kurallar:
- Gizleme (stealth), sahte parmak izi veya ek Chromium bayrağı YOK.
- `playwright install chromium` yapılmamışsa K1 çökmez; `kullanilabilir = False`
  olarak işaretlenir, olay yayınlanır ve `(None, "")` döner (Kademe K1'i atlar).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


class ChromiumKullanilamiyor(RuntimeError):
    """Playwright paketli Chromium kurulu değil veya başlatılamadı."""


class ChromiumKatmani:
    """Playwright başsız Chromium sarmalayıcısı (K1)."""

    def __init__(
        self,
        bildirim_fn: Callable[[dict[str, Any]], Any] | None = None,
        zaman_asimi: float = 40.0,
    ) -> None:
        self.bildirim_fn = bildirim_fn
        self.zaman_asimi = zaman_asimi
        self.kullanilabilir: bool | None = None
        self._pw = None
        self._browser = None

    def _bildir(self, olay: dict[str, Any]) -> None:
        if self.bildirim_fn is not None:
            try:
                self.bildirim_fn(olay)
            except Exception:
                pass

    def _baslat(self, url: str = "") -> Any:
        if self._browser is not None:
            return self._browser
        if self.kullanilabilir is False:
            self._bildir({
                "tur": "k1_atlandi",
                "katman": "K1",
                "url": url,
                "sebep": "chromium_kurulu_degil",
            })
            return None
        try:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            exe_yolu = getattr(self._pw.chromium, "executable_path", None)
            if exe_yolu and not Path(exe_yolu).is_file():
                raise ChromiumKullanilamiyor(
                    f"Chromium ikilisi bulunamadı: {exe_yolu}"
                )
            # Ek bayrak yok, yalnız başsız mod
            self._browser = self._pw.chromium.launch(headless=True)
            self.kullanilabilir = True
            return self._browser
        except Exception as e:
            self.kullanilabilir = False
            self.kapat()
            self._bildir({
                "tur": "k1_atlandi",
                "katman": "K1",
                "url": url,
                "sebep": "chromium_kurulu_degil",
                "ayrinti": str(e),
            })
            return None

    def getir(self, url: str, zaman_asimi: float | None = None) -> tuple[int | None, str]:
        """URL'yi başsız Chromium ile aç, `(durum, html)` döndür.

        Chromium kurulu değilse olayı yayınlar ve `(None, "")` döndürür.
        """
        browser = self._baslat(url=url)
        if browser is None:
            return None, ""

        sure_ms = int((zaman_asimi if zaman_asimi is not None else self.zaman_asimi) * 1000)
        sayfa = browser.new_page()
        try:
            yanit = sayfa.goto(url, wait_until="domcontentloaded", timeout=sure_ms)
            durum = yanit.status if yanit is not None else 200
            html = sayfa.content()
            return durum, html
        finally:
            try:
                sayfa.close()
            except Exception:
                pass

    def kapat(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._pw = None

    def __enter__(self) -> "ChromiumKatmani":
        return self

    def __exit__(self, *_: Any) -> None:
        self.kapat()


def getir(
    url: str,
    zaman_asimi: float = 40.0,
    bildirim_fn: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[int | None, str]:
    """Tek seferlik K1 Chromium isteği: `getir(url) -> (durum, html)`."""
    with ChromiumKatmani(bildirim_fn=bildirim_fn, zaman_asimi=zaman_asimi) as k1:
        return k1.getir(url, zaman_asimi=zaman_asimi)
