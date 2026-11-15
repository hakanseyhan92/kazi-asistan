"""Süreçler arası ve iş parçacığı içi yeniden girilebilir Brave muteks kilidi.

Kilit dosyası: ~/.local/share/kazi/brave.lock (fcntl.flock).
Aynı iş parçacığında iç içe çağrılabilir; farklı iş parçacıkları ve süreçler
birbirini dışlar.
"""
from __future__ import annotations

import fcntl
import threading
import time
from contextlib import contextmanager
from pathlib import Path

KILIT_YOL = Path.home() / ".local" / "share" / "kazi" / "brave.lock"


class KilitZamanAsimi(TimeoutError):
    """Brave kilidi zaman aşımına uğradı."""


# (yol, iş parçacığı kimliği) -> [açık dosya nesnesi, iç içe giriş sayacı]
_TUTULAN: dict[tuple[str, int], list] = {}
_TABLO_KILIDI = threading.Lock()


def _anahtar(kilit: Path) -> tuple[str, int]:
    return (str(kilit.resolve()), threading.get_ident())


@contextmanager
def brave_kilidi(zaman_asimi: float = 900.0, yol: Path | str | None = None):
    """Brave'i tek sürece ve tek iş parçacığına kilitle (fcntl.flock).

    `zaman_asimi` saniye içinde kilit alınamazsa `KilitZamanAsimi` fırlatılır.
    Aynı iş parçacığında yeniden girilebilir (re-entrant).
    """
    kilit = Path(yol).expanduser() if yol else KILIT_YOL
    kilit.parent.mkdir(parents=True, exist_ok=True)
    kilit.touch(exist_ok=True)
    anahtar = _anahtar(kilit)

    with _TABLO_KILIDI:
        kayit = _TUTULAN.get(anahtar)

    if kayit is None:
        dosya = open(kilit, "a+", encoding="utf-8")
        bitis = time.monotonic() + max(0.0, float(zaman_asimi))
        while True:
            with _TABLO_KILIDI:
                # Aynı süreçte başka bir iş parçacığı tutuyorsa flock aynı süreçte
                # geçebileceği için tablo üzerinden de denetle.
                baska_tutuyor = any(
                    k[0] == anahtar[0] and k[1] != anahtar[1] for k in _TUTULAN
                )
                if not baska_tutuyor:
                    try:
                        fcntl.flock(dosya.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        kayit = _TUTULAN[anahtar] = [dosya, 0]
                        break
                    except OSError:
                        pass
            if time.monotonic() >= bitis:
                dosya.close()
                raise KilitZamanAsimi(
                    f"Brave kilidi alınamadı ({zaman_asimi}s): {kilit}"
                )
            time.sleep(0.05)

    kayit[1] += 1
    try:
        yield kilit
    finally:
        with _TABLO_KILIDI:
            kayit[1] -= 1
            if kayit[1] <= 0:
                _TUTULAN.pop(anahtar, None)
                try:
                    fcntl.flock(kayit[0].fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                kayit[0].close()
