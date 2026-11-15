"""Elle doğrulama istekleri (15.11): bot koruması çıkınca kullanıcı arayüzden "elle geçeceğim" der → tarama o sitede
doğrulama sayfasını KAPATMADAN bekler; kullanıcı CAPTCHA'yı / "basılı tut"u kendisi geçer (otomatik atlatma YOK).

Süreçler arası paylaşım için küçük JSON dosyası: `$KAZI_VERI|$XDG_DATA_HOME/kazi/dogrulama_istek.json` =
{"<site>": <bitiş epoch sn>, ...}; "*" tüm siteler. Çekirdek (API) yazar, zamanlayıcının tarama alt süreçleri okur.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

TUM = "*"
VARSAYILAN_SURE_SN = 300
AZAMI_SURE_SN = 900
# Kullanıcı istemese bile doğrulama sayfasında kısa süre beklenir: arayüzdeki "elle geç" düğmesine basma fırsatı.
ISTEK_FIRSATI_SN = 20.0

_kilit = threading.Lock()


def yol() -> Path:
    deger = os.environ.get("KAZI_DOGRULAMA_ISTEK")
    if deger:
        return Path(deger)
    from ..ayar import varsayilan_veri_dizini
    return varsayilan_veri_dizini() / "dogrulama_istek.json"


def _oku() -> dict[str, float]:
    try:
        d = json.loads(yol().read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except Exception:
        return {}


def _yaz(d: dict[str, float]) -> None:
    p = yol()
    p.parent.mkdir(parents=True, exist_ok=True)
    gecici = p.with_suffix(".tmp")
    gecici.write_text(json.dumps(d), encoding="utf-8")
    gecici.replace(p)


def _site(s: str) -> str:
    s = str(s or "").strip().lower()
    return s[4:] if s.startswith("www.") else s


def istek_yaz(site: str, sure_sn: float = VARSAYILAN_SURE_SN, simdi: float | None = None) -> float:
    """Site için elle doğrulama isteği; bitiş zamanını (epoch) döndürür. Süre 10..900 sn'ye kırpılır."""
    simdi = time.time() if simdi is None else simdi
    bitis = simdi + max(10.0, min(float(sure_sn), AZAMI_SURE_SN))
    with _kilit:
        d = {k: v for k, v in _oku().items() if v > simdi}
        d[_site(site) or TUM] = bitis
        _yaz(d)
    return bitis


def istek_sil(site: str) -> bool:
    with _kilit:
        d = _oku()
        var = d.pop(_site(site) or TUM, None) is not None
        _yaz(d)
    return var


def istekler(simdi: float | None = None) -> dict[str, int]:
    """Süresi dolmamış istekler → {site: kalan_sn}."""
    simdi = time.time() if simdi is None else simdi
    return {k: int(v - simdi) for k, v in _oku().items() if v > simdi}


def istek_bitisi(site: str, simdi: float | None = None) -> float | None:
    """Bu site (ya da "*") için etkin istek varsa bitiş epoch'u. `site` alan adı ya da modül adı olabilir
    ("hepsiburada" ↔ "www.hepsiburada.com" eşleşir)."""
    simdi = time.time() if simdi is None else simdi
    s = _site(site)
    en_gec = None
    for k, v in _oku().items():
        if v <= simdi:
            continue
        if k == TUM or k == s or (k and s and (s.endswith("." + k) or k.endswith("." + s)
                                              or s.split(".")[0] == k or k.split(".")[0] == s)):
            en_gec = v if en_gec is None else max(en_gec, v)
    return en_gec


def bekleme_bitisi(site: str, baslangic: float, ayar_sn: float, simdi_fn: Callable[[], float] = time.time) -> float:
    """K3 döngüsünün o anki bitiş zamanı: ayardaki bekleme, istek fırsatı ve kullanıcının elle doğrulama isteğinden en geç olanı."""
    sinir = baslangic + max(float(ayar_sn or 0.0), ISTEK_FIRSATI_SN)
    istek = istek_bitisi(site, simdi_fn())
    return max(sinir, istek) if istek else sinir
