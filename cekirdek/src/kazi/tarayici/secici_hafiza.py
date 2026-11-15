"""Site başına son çalışan arama kutusu seçicisinin hafızası.

Dosya: `<veri dizini>/secici_hafiza.json` (KAZI_SECICI_HAFIZA ile değişir).
Biçim: {site: {"secici": "...", "zaman": iso, "basari": n}}.
Atomik yazım (geçici dosya + os.replace) ve süreç içi kilit; bozuk dosya boş sayılır.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from ..ayar import varsayilan_veri_dizini
from ..modeller import TZ_ISTANBUL

_KILIT = threading.RLock()


def yol() -> Path:
    ozel = os.environ.get("KAZI_SECICI_HAFIZA")
    if ozel:
        return Path(ozel).expanduser()
    return varsayilan_veri_dizini() / "secici_hafiza.json"


def _yukle() -> dict:
    try:
        veri = json.loads(yol().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(veri, dict):
        return {}
    return {k: v for k, v in veri.items() if isinstance(v, dict) and isinstance(v.get("secici"), str)}


def _yaz(veri: dict) -> None:
    p = yol()
    p.parent.mkdir(parents=True, exist_ok=True)
    gecici = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(gecici, p)


def oku(site: str) -> str | None:
    with _KILIT:
        k = _yukle().get(site)
    return k["secici"] if k and k["secici"] else None


def kaydet(site: str, secici: str) -> None:
    """Seçiciyi yaz; aynı seçici yeniden başarırsa `basari` artar."""
    if not site or not secici:
        return
    with _KILIT:
        veri = _yukle()
        eski = veri.get(site)
        n = 1
        if eski and eski.get("secici") == secici:
            try:
                n = int(eski.get("basari", 0)) + 1
            except (TypeError, ValueError):
                n = 1
        veri[site] = {"secici": secici, "zaman": datetime.now(TZ_ISTANBUL).isoformat(timespec="seconds"),
                      "basari": n}
        try:
            _yaz(veri)
        except OSError:
            pass


def unut(site: str) -> None:
    with _KILIT:
        veri = _yukle()
        if site in veri:
            del veri[site]
            try:
                _yaz(veri)
            except OSError:
                pass
