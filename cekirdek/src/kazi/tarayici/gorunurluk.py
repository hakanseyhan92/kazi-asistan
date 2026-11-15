"""Brave penceresini göster/gizle (kullanıcı isteği 14.11: "fiyat taramaları arka planda olsun, Brave sayfasını göster/gizle").

Yöntem: Brave GERÇEK, başlıklı (headed) tarayıcı olarak kalır; pencere DevTools protokolüyle simge durumuna küçültülür.
Headless kullanılmaz (UA'da "HeadlessChrome" görünür, bot korumasına takılır; UA'yı değiştirmek de sahte parmak izi = YASAK).
Ayar dosyası: $XDG_CONFIG_HOME/kazi/tarama.json  {"brave_gorunur": false}. Aynı dosyayı eski evren-asistan tarayıcısı da okur
(kopyası: ~/evren-asistan/fiyat/gorunurluk.py — ikisini birlikte değiştir).
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def ayar_yolu() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kazi" / "tarama.json"


def ayar_oku() -> dict:
    try:
        d = json.loads(ayar_yolu().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def gorunur_mu() -> bool:
    return bool(ayar_oku().get("brave_gorunur", True))


def ayar_yaz(**degerler) -> dict:
    d = ayar_oku()
    d.update(degerler)
    yol = ayar_yolu()
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_suffix(".json.tmp")
    gecici.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    gecici.replace(yol)
    return d


def pencereleri_ayarla(browser, gorunur: bool | None = None) -> list[str]:
    """Bağlı Brave'in tüm pencerelerini küçült (gizli) ya da normale döndür. Hata yutar; durumları döndürür."""
    gorunur = gorunur_mu() if gorunur is None else gorunur
    durumlar: list[str] = []
    try:
        s = browser.new_browser_cdp_session()
    except Exception:
        return durumlar
    try:
        pencereler = set()
        for h in s.send("Target.getTargets").get("targetInfos", []):
            if h.get("type") == "page":
                try:
                    pencereler.add(s.send("Browser.getWindowForTarget", {"targetId": h["targetId"]})["windowId"])
                except Exception:
                    pass
        for w in pencereler:
            try:
                s.send("Browser.setWindowBounds", {"windowId": w, "bounds": {"windowState": "normal" if gorunur else "minimized"}})
                durumlar.append(s.send("Browser.getWindowBounds", {"windowId": w})["bounds"].get("windowState", "?"))
            except Exception:
                pass
    finally:
        try:
            s.detach()
        except Exception:
            pass
    return durumlar


# Küçültülmüş pencerede sayfalar yavaşlatılmasın (yalnız performans; parmak izini değiştirmez)
ARKA_PLAN_BAYRAKLARI = ["--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                        "--disable-background-timer-throttling"]
