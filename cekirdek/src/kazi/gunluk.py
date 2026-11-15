"""Kayıt defteri (günlük / log) — 15.11 kullanıcı isteği: "hangi tarayıcıdan hangi bilgi çekildi, hangi tarayıcı hangi
siteye gidince ne hata verdi, hepsi kayıtlı olsun; ben de sen de takip edelim".

Ayrı SQLite dosyası (`$XDG_DATA_HOME/kazi/gunluk.db`, `KAZI_GUNLUK` ile değişir; ana veritabanıyla kilit yarışmasın).
Çekirdek, zamanlayıcı ve onun alt süreçleri (her tarama ayrı süreç) aynı dosyaya yazar; her iş parçacığının kendi
bağlantısı vardır. Yazma ASLA istisna fırlatmaz: kayıt tutulamazsa tarama yine sürer.

Seviyeler: bilgi | uyari | hata. Kaynak: zamanlayici | tarama | ucus | tarayici | alarm | cekirdek | python.
14 günden eski kayıtlar silinir (`temizle`).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Istanbul")
SEVIYELER = ("bilgi", "uyari", "hata")
SAKLAMA_GUN = 14

SEMA = """
CREATE TABLE IF NOT EXISTS kayit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zaman TEXT NOT NULL,
    seviye TEXT NOT NULL,
    kaynak TEXT NOT NULL,
    olay TEXT,
    kategori TEXT,
    site TEXT,
    tarayici TEXT,
    url TEXT,
    adet INTEGER,
    sure_sn REAL,
    mesaj TEXT NOT NULL,
    veri TEXT NOT NULL DEFAULT '{}',
    pid INTEGER
);
CREATE INDEX IF NOT EXISTS ix_kayit_zaman ON kayit(zaman);
CREATE INDEX IF NOT EXISTS ix_kayit_site ON kayit(site);
"""

_yerel = threading.local()
_kategori = {"id": None}          # süreç başına etkin tarama kategorisi (zamanlayıcı alt süreci ayarlar)


def yol() -> Path | None:
    deger = os.environ.get("KAZI_GUNLUK")
    if deger == "kapali":
        return None
    if deger:
        return Path(deger)
    from .ayar import varsayilan_veri_dizini
    return varsayilan_veri_dizini() / "gunluk.db"


def _baglanti() -> sqlite3.Connection | None:
    p = yol()
    if p is None:
        return None
    b = getattr(_yerel, "b", None)
    if b is not None and getattr(_yerel, "yol", None) == p and getattr(_yerel, "pid", None) == os.getpid():
        return b
    p.parent.mkdir(parents=True, exist_ok=True)
    b = sqlite3.connect(str(p), timeout=5.0, isolation_level=None, check_same_thread=False)
    b.execute("PRAGMA journal_mode=WAL")
    b.execute("PRAGMA busy_timeout=5000")
    b.executescript(SEMA)
    _yerel.b, _yerel.yol, _yerel.pid = b, p, os.getpid()
    return b


def kategori_ayarla(kategori: str | None) -> None:
    """Bu süreçteki sonraki kayıtlar bu tarama kategorisiyle etiketlenir (zamanlayıcı alt süreci)."""
    _kategori["id"] = kategori


def _simdi() -> str:
    return datetime.now(TZ).isoformat(timespec="milliseconds")


def yaz(seviye: str, kaynak: str, mesaj: str, *, olay: str | None = None, kategori: str | None = None,
        site: str | None = None, tarayici: str | None = None, url: str | None = None, adet: int | None = None,
        sure_sn: float | None = None, veri: dict[str, Any] | None = None) -> None:
    try:
        b = _baglanti()
        if b is None:
            return
        if seviye not in SEVIYELER:
            seviye = "bilgi"
        b.execute(
            "INSERT INTO kayit (zaman, seviye, kaynak, olay, kategori, site, tarayici, url, adet, sure_sn, mesaj, veri, pid)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_simdi(), seviye, kaynak, olay, kategori or _kategori["id"], site, tarayici,
             (url or None) and str(url)[:500], adet, None if sure_sn is None else round(float(sure_sn), 2),
             str(mesaj)[:2000], json.dumps(veri or {}, ensure_ascii=False, default=str)[:8000], os.getpid()))
    except Exception:
        pass


# ---------------------------------------------------------------- tarayıcı olayları → kayıt
_OLAY_SEVIYE = {
    "dogrulama_gerekli": "uyari", "k2_kullanilamiyor": "hata", "reklam_engeli_kurulamadi": "uyari",
    "cerez_reddedildi": "bilgi", "popup_kapatildi": "bilgi",
}
_OLAY_METIN = {
    "dogrulama_gerekli": "doğrulama sayfası (bot koruması)", "k2_kullanilamiyor": "Brave kullanılamıyor",
    "reklam_engeli_kurulamadi": "reklam engeli kurulamadı", "cerez_reddedildi": "çerez penceresi reddedildi",
    "popup_kapatildi": "açılır pencere kapatıldı",
}


def tarayici_olayi(o: dict[str, Any], tarayici: str | None = None) -> None:
    """Tarayici/Kademe bildirim olaylarını kayda çevir (site bitişleri ayrıca `tarama`/`ucus` kaynağıyla yazılır)."""
    try:
        tur = str(o.get("tur") or "olay")
        if tur == "tarama_site_bitti":
            return
        hata = o.get("hata")
        seviye = _OLAY_SEVIYE.get(tur) or ("hata" if hata else "bilgi")
        if any(k in tur for k in ("blok", "engel", "hata", "basarisiz", "dustu")):
            seviye = "uyari" if seviye == "bilgi" else seviye
        mesaj = _OLAY_METIN.get(tur) or o.get("mesaj") or tur.replace("_", " ")
        if hata:
            mesaj = f"{mesaj}: {hata}"
        if o.get("dugme"):
            mesaj = f"{mesaj} ({o['dugme']})"
        yaz(seviye, "tarayici", mesaj, olay=tur, site=o.get("site"),
            tarayici=tarayici or (str(o["katman"]) if o.get("katman") else None), url=o.get("url"),
            veri={k: v for k, v in o.items() if k not in ("tur", "site", "url", "mesaj", "hata")} or None)
    except Exception:
        pass


# ---------------------------------------------------------------- python logging → kayıt
class KayitIsleyici(logging.Handler):
    """WARNING ve üstü Python log satırları (ör. 'kayak form istisnası') kayda düşer."""

    def emit(self, r: logging.LogRecord) -> None:
        if r.name.startswith(("uvicorn.access", "kazi.gunluk")):
            return
        try:
            seviye = "hata" if r.levelno >= logging.ERROR else ("uyari" if r.levelno >= logging.WARNING else "bilgi")
            mesaj = r.getMessage()
            if r.exc_info and r.exc_info[1] is not None:
                mesaj += f" — {type(r.exc_info[1]).__name__}: {r.exc_info[1]}"
            yaz(seviye, "python", mesaj, olay=r.name)
        except Exception:
            pass


def python_logu_bagla(seviye: int = logging.WARNING) -> None:
    kok = logging.getLogger()
    if not any(isinstance(h, KayitIsleyici) for h in kok.handlers):
        h = KayitIsleyici(seviye)
        kok.addHandler(h)
        if kok.level > seviye or kok.level == logging.NOTSET:
            kok.setLevel(seviye)


# ---------------------------------------------------------------- okuma / temizlik
def oku(*, limit: int = 300, once_id: int | None = None, sonra_id: int | None = None, seviye: str | None = None,
        kaynak: str | None = None, site: str | None = None, kategori: str | None = None,
        ara: str | None = None) -> dict[str, Any]:
    """En yeniden eskiye. `sonra_id`: yalnız daha yeni kayıtlar (canlı izleme); `once_id`: sayfalama.
    `seviye='uyari'` uyarı+hata, `seviye='hata'` yalnız hata döndürür."""
    b = _baglanti()
    if b is None:
        return {"kayitlar": [], "son_id": 0}
    kosul, arg = [], []
    if once_id:
        kosul.append("id < ?"); arg.append(int(once_id))
    if sonra_id:
        kosul.append("id > ?"); arg.append(int(sonra_id))
    if seviye == "uyari":
        kosul.append("seviye IN ('uyari','hata')")
    elif seviye == "hata":
        kosul.append("seviye = 'hata'")
    for sutun, deger in (("kaynak", kaynak), ("site", site), ("kategori", kategori)):
        if deger:
            kosul.append(f"{sutun} = ?"); arg.append(deger)
    if ara:
        kosul.append("(mesaj LIKE ? OR url LIKE ? OR site LIKE ? OR tarayici LIKE ?)")
        arg += [f"%{ara}%"] * 4
    sql = ("SELECT id, zaman, seviye, kaynak, olay, kategori, site, tarayici, url, adet, sure_sn, mesaj, veri FROM kayit"
           + (" WHERE " + " AND ".join(kosul) if kosul else "") + " ORDER BY id DESC LIMIT ?")
    arg.append(max(1, min(int(limit), 2000)))
    sutunlar = ("id", "zaman", "seviye", "kaynak", "olay", "kategori", "site", "tarayici", "url", "adet", "sure_sn",
                "mesaj", "veri")
    kayitlar = []
    for satir in b.execute(sql, arg):
        k = dict(zip(sutunlar, satir))
        try:
            k["veri"] = json.loads(k["veri"] or "{}")
        except Exception:
            k["veri"] = {}
        kayitlar.append(k)
    son = b.execute("SELECT COALESCE(MAX(id), 0) FROM kayit").fetchone()[0]
    return {"kayitlar": kayitlar, "son_id": son}


def ozet(saat: int = 24) -> dict[str, Any]:
    """Son `saat` saatte seviye sayıları + filtre seçenekleri (site/kaynak/kategori listeleri)."""
    b = _baglanti()
    if b is None:
        return {"seviye": {}, "siteler": [], "kaynaklar": [], "kategoriler": []}
    sinir = (datetime.now(TZ) - timedelta(hours=saat)).isoformat(timespec="seconds")
    sev = dict(b.execute("SELECT seviye, COUNT(*) FROM kayit WHERE zaman >= ? GROUP BY seviye", (sinir,)).fetchall())

    def _liste(s: str) -> list[str]:
        return [r[0] for r in b.execute(f"SELECT DISTINCT {s} FROM kayit WHERE {s} IS NOT NULL ORDER BY {s}")]
    return {"seviye": sev, "siteler": _liste("site"), "kaynaklar": _liste("kaynak"), "kategoriler": _liste("kategori")}


def temizle(gun: int = SAKLAMA_GUN) -> int:
    try:
        b = _baglanti()
        if b is None:
            return 0
        sinir = (datetime.now(TZ) - timedelta(days=gun)).isoformat(timespec="seconds")
        return b.execute("DELETE FROM kayit WHERE zaman < ?", (sinir,)).rowcount
    except Exception:
        return 0
