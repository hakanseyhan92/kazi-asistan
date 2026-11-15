"""Telegram bildirimi + alarm fiyatı.

Ayar: $XDG_CONFIG_HOME/kazi/telegram.json ({"token", "chat_id", "bot", "etkin"}); yoksa evren-asistan'ın
~/.config/evren/telegram.json dosyası OKUNUR (kopyalanmaz). Token hiçbir yanıtta/logda açık yazılmaz (maskeli).

Alarm: ürünün alarm fiyatı (kullanıcı arayüzden girer) en iyi geçerli teklif fiyatına EŞİT ya da ÜSTÜNDEYSE
(fiyat ≤ alarm) Telegram'a anında ayrı mesaj gider. Aynı ürün + aynı link + aynı fiyat için 12 saatte bir kez.
"""
from __future__ import annotations

import json
import re
import logging
import os
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("kazi.bildirim")
TR = timezone(timedelta(hours=3))
EVREN_AYAR = Path.home() / ".config" / "evren" / "telegram.json"
TEKRAR_SAAT = 12


def kazi_ayar_yolu() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kazi" / "telegram.json"


def _oku(yol: Path) -> dict:
    try:
        d = json.loads(yol.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def ayar_oku() -> dict:
    """Etkin ayar: kazi dosyasındaki alanlar, eksikleri evren-asistan dosyasından. `kaynak` alanı nereden geldiğini söyler."""
    kazi, evren = _oku(kazi_ayar_yolu()), _oku(EVREN_AYAR)
    d = {"etkin": True, **{k: v for k, v in evren.items() if k in ("token", "chat_id", "bot")}, **kazi}
    d["kaynak"] = "kazi" if kazi.get("token") else ("evren-asistan" if evren.get("token") else "yok")
    return d


def maskele(token: str | None) -> str | None:
    if not token:
        return None
    return token[:4] + "…" + token[-4:] if len(token) > 12 else "…"


def ayar_ozeti() -> dict:
    """Arayüze gidecek güvenli özet (token maskeli)."""
    d = ayar_oku()
    return {"etkin": bool(d.get("etkin", True)), "bot": d.get("bot"), "chat_id": d.get("chat_id"),
            "token_maskeli": maskele(d.get("token")), "token_var": bool(d.get("token")), "kaynak": d["kaynak"],
            "ayar_dosyasi": str(kazi_ayar_yolu()), **_rapor_ozeti(d)}


def _rapor_ozeti(d: dict) -> dict:
    from .rapor import rapor_ayari
    return rapor_ayari(d)


def ayar_yaz(**alanlar) -> dict:
    """Yalnız verilen alanları kazi dosyasına yazar (0600). token boş/None ise mevcut korunur."""
    yol = kazi_ayar_yolu()
    d = _oku(yol)
    if not d.get("token"):                          # ilk kez: mevcut etkin değerlerle başla (evren-asistan'dan)
        d.update({k: v for k, v in ayar_oku().items() if k in ("token", "chat_id", "bot")})
    for k, v in alanlar.items():
        if k == "token" and not v:
            continue
        if v is not None:
            d[k] = v
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_suffix(".json.tmp")
    gecici.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(gecici, 0o600)
    gecici.replace(yol)
    return ayar_ozeti()


def gonder(metin: str, ayar: dict | None = None, zaman_asimi: float = 15.0) -> bool:
    """HTML biçimli mesaj gönder. Ayar yoksa/kapalıysa False; ağ hatası yutulur (log'a token yazılmaz)."""
    a = ayar or ayar_oku()
    if not a.get("etkin", True) or not a.get("token") or not a.get("chat_id"):
        return False
    veri = urllib.parse.urlencode({"chat_id": a["chat_id"], "text": metin[:4000], "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(
                f"https://api.telegram.org/bot{a['token']}/sendMessage", data=veri), timeout=zaman_asimi) as r:
            ok = json.loads(r.read()).get("ok", False)
        from .. import gunluk
        ilk = re.sub(r"<[^>]+>", "", metin).strip().split("\n")[0][:200]
        gunluk.yaz("bilgi" if ok else "uyari", "alarm", f"Telegram {'gönderildi' if ok else 'reddetti'}: {ilk}",
                   olay="telegram")
        return ok
    except Exception as e:                          # noqa: BLE001 — mesaj kaybı taramayı durdurmasın
        log.warning("Telegram gönderilemedi: %s", type(e).__name__)
        return False


def _tl(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".") + " TL"


def _esc(s: Any) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def alarm_mesaji(urun_ad: str, alarm: float, t: dict, kategori: str | None = None,
                 zaman: datetime | None = None) -> str:
    """Rapor ile aynı düzen (15.11 kullanıcı): 🔔 ALARM (tarih) → kategori → marka/model (kalın) → Fiyat (kalın) ·
    vade farksız taksit (kalın) · satıcı linki → ilan adı → alarm fiyatı. Teyit bilgisi yok."""
    from .rapor import KATEGORI_AD, KATEGORI_SIMGE, _kisalt, marka_model
    zaman = zaman or datetime.now(TR)
    fiyat = t["sepet_fiyati"]
    satirlar = [f"🔔 <b>ALARM ({zaman:%d.%m.%Y %H:%M})</b>", ""]
    if kategori:
        satirlar.append(f"{KATEGORI_SIMGE.get(kategori, '•')} <b>{_esc(KATEGORI_AD.get(kategori, kategori.capitalize()))}</b>")
    satirlar.append(f"<b>{_esc(marka_model(urun_ad))}</b>")
    f_satir = f"<b>Fiyat: {_tl(fiyat)}</b>"
    taksit = (t.get("taksit") or {}).get("pesin_fiyatina")
    if taksit:
        f_satir += f" · <b>{taksit} ay vade farksız</b>"
    kim = " · ".join(x for x in (t.get("site"), t.get("satici")) if x) or "satıcı"
    f_satir += f' · <a href="{_esc(t["url"])}">{_esc(kim)}</a>' if t.get("url") else f" · {_esc(kim)}"
    satirlar.append(f_satir)
    if t.get("baslik"):
        satirlar.append(f"📦 <i>{_esc(_kisalt(t['baslik']))}</i>")
    alt = alarm - fiyat
    satirlar.append(f"🎯 Alarm fiyatı {_tl(alarm)} · "
                    + ("tam alarm fiyatında" if abs(alt) < 1 else f"<b>{_tl(alt)} altında</b>"))
    return "\n".join(satirlar)


def alarm_denetle(v, gonder_fn=None, simdi: datetime | None = None) -> list[dict]:
    """Alarm fiyatı olan her takipteki ürün için en iyi geçerli teklif ≤ alarm ise mesaj gönder (tekrar koruması).
    Gönderilen alarmları döndürür."""
    from ..pano import pano
    gonder_fn = gonder_fn or gonder
    simdi = simdi or datetime.now(TR)
    sinir = (simdi - timedelta(hours=TEKRAR_SAAT)).isoformat()
    con = v.baglanti
    gonderilen = []
    for u in pano(v)["urunler"]:
        alarm, t = u.get("alarm_fiyat"), u.get("en_iyi")
        if alarm is None or not t or t.get("sepet_fiyati") is None or t["sepet_fiyati"] > alarm:
            continue
        anahtar = f"{u['id']}|{t.get('url')}|{round(t['sepet_fiyati'])}"
        if con.execute("SELECT 1 FROM alarm_gonderim WHERE anahtar=? AND zaman>=?", (anahtar, sinir)).fetchone():
            continue
        if gonder_fn(alarm_mesaji(u["ad"], alarm, t, kategori=u.get("kategori"), zaman=simdi)):
            con.execute("INSERT INTO alarm_gonderim (anahtar, zaman) VALUES (?, ?) "
                        "ON CONFLICT(anahtar) DO UPDATE SET zaman=excluded.zaman", (anahtar, simdi.isoformat()))
            con.commit()
            gonderilen.append({"urun_id": u["id"], "fiyat": t["sepet_fiyati"], "alarm": alarm})
    return gonderilen


# 15.11 kullanıcı: "alarm mesajları direkt Telegram'a düşsün, beklemesin". Önceden alarm, kategorinin TÜM siteleri + teyit
# (ürün sayfasında yeniden okuma, 40 sn arayla) + Brave kapanışı bittikten sonra denetleniyordu (dakikalar). Artık her site
# bittiği anda o sitenin kartlarına bakılır. Tekrar koruması alarm_denetle ile AYNI anahtar (ürün|url|fiyat): sonradan gelen
# normal denetim aynı ilanı ikinci kez göndermez.
SUPHELI_ORAN = 0.80          # piyasa medyanının %20'sinden fazla altı → anlık gönderme, teyit sonrası alarm_denetle'ye bırak
_anlik_kilit = threading.Lock()


def anlik_alarm(v, urun_id: str, kartlar, gonder_fn=None, simdi: datetime | None = None) -> list[dict]:
    """Yeni bulunan kartlardan alarm fiyatına inen en ucuz geçerli ilanı HEMEN gönder. Hata yutar (tarama düşmez)."""
    try:
        from ..pano import _hedef, _taze_teklifler
        from ..kurallar.piyasa import UrunAyar, piyasa_ozeti
        gonder_fn = gonder_fn or gonder
        simdi = simdi or datetime.now(TR)
        alarm = _hedef(v, urun_id)
        if alarm is None:
            return []
        adaylar = sorted((k for k in kartlar or [] if getattr(k, "sepet_fiyati", None) is not None
                          and getattr(k, "stok", None) is not False and getattr(k, "durum", "gecerli") == "gecerli"
                          and k.sepet_fiyati <= alarm), key=lambda k: k.sepet_fiyati)
        if not adaylar:
            return []
        ayar = UrunAyar()
        medyan = piyasa_ozeti(_taze_teklifler(v, urun_id, simdi, ayar), simdi, ayar).get("medyan")
        adaylar = [k for k in adaylar if not medyan or k.sepet_fiyati >= medyan * SUPHELI_ORAN]
        if not adaylar:
            return []
        k = adaylar[0]
        anahtar = f"{urun_id}|{k.url}|{round(k.sepet_fiyati)}"
        sinir = (simdi - timedelta(hours=TEKRAR_SAAT)).isoformat()
        u = v.urun_getir(urun_id) if hasattr(v, "urun_getir") else None
        ad = getattr(u, "kanonik_ad", None) or urun_id
        t = {"sepet_fiyati": k.sepet_fiyati, "site": k.site, "satici": k.satici, "url": k.url, "baslik": k.baslik,
             "teyit_durumu": "anlik", "taksit": {"pesin_fiyatina": k.pesin_taksit} if k.pesin_taksit else {}}
        kategori = ((getattr(u, "eslesme_kurallari", None) or {}).get("kategori")) if u is not None else None
        with _anlik_kilit:
            con = v.baglanti
            if con.execute("SELECT 1 FROM alarm_gonderim WHERE anahtar=? AND zaman>=?", (anahtar, sinir)).fetchone():
                return []
            if not gonder_fn(alarm_mesaji(ad, alarm, t, kategori=kategori, zaman=simdi)):
                return []
            con.execute("INSERT INTO alarm_gonderim (anahtar, zaman) VALUES (?, ?) "
                        "ON CONFLICT(anahtar) DO UPDATE SET zaman=excluded.zaman", (anahtar, simdi.isoformat()))
            con.commit()
        return [{"urun_id": urun_id, "fiyat": k.sepet_fiyati, "alarm": alarm, "site": k.site}]
    except Exception as e:                                   # noqa: BLE001
        log.warning("anlık alarm denetlenemedi: %s", type(e).__name__)
        return []
