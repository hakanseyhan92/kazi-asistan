"""Tarayıcı motorları izleme (15.11, sözleşme: `gorevler/TARAYICI_IZLEME_SOZLESME.md`).

Kullanıcı arayüzünün sağ panelindeki motor kartlarının verisi. Tarayıcı PENCERESİ açmadan:
- motorun kurulu olup olmadığı (Brave / yerel klon / görünür Chromium / HTTP / Chrome / Firefox),
- CDP açıksa sekmeler,
- kayıt defterindeki (`kazi.gunluk`) olaylara göre NE İŞ yaptığı ve bot korumasıyla karşı karşıya olup olmadığı.

Renkler: aktif = yeşil, bot = sarı, çalışmayan/boşta = gri. Bot koruması YALNIZ kullanıcı tarafından elle geçilir;
bu modül pencereyi öne getirmekten başka bir şey yapmaz (stealth / CAPTCHA çözme YOK).

`motor_durumlari` SAF'tır: dış dünya (CDP, kurulum tespiti, saat, doğrulama istekleri) parametreyle enjekte edilir.
"""
from __future__ import annotations

import base64
import html
import json
import time
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .. import gunluk
from . import dogrulama

TZ = ZoneInfo("Europe/Istanbul")
BOT_PENCERE_DK = 10            # bot durumu bu kadar dakika içindeki olaylardan hesaplanır
AKTIF_PENCERE_SN = 30          # "son iş" bu kadar saniye içindeyse motor aktif sayılır
SON_KAYIT_SAYISI = 5

# Sabit sıra ve etiketler kayıt defterindeki `tarayici` alanıyla BİREBİR eşleşir.
MOTORLAR: list[dict[str, Any]] = [
    {"ad": "brave_kazi", "etiket": "Brave (kazı)", "cdp_port": 9334, "destekli": True,
     "aciklama": "Ana tarama tarayıcısı (kalıcı profil)"},
    {"ad": "brave_yerel", "etiket": "Brave (yerel profil klonu)", "cdp_port": 9335, "destekli": True,
     "aciklama": "Kullanıcının yerel profil klonuyla son çare"},
    {"ad": "chromium_gorunur", "etiket": "Chromium (görünür yedek)", "cdp_port": None, "destekli": True,
     "aciklama": "Görünür yedek tarayıcı (CDP yok)"},
    {"ad": "http_k0", "etiket": "HTTP (K0)", "cdp_port": None, "destekli": True,
     "aciklama": "Tarayıcısız düz HTTP isteği"},
    {"ad": "google_chrome", "etiket": "Google Chrome", "cdp_port": None, "destekli": False,
     "aciklama": "Tarama motoru değil (kurulu değilse gri)"},
    {"ad": "firefox", "etiket": "Firefox", "cdp_port": None, "destekli": False,
     "aciklama": "Playwright sistem Firefox'una bağlanamıyor"},
]

# Bot / insan doğrulaması sayfa başlıkları (büyük/küçük harf duyarsız).
BOT_BASLIK = re.compile(
    r"Just a moment|Bir dakika|Attention Required|Access Denied|captcha|robot|"
    r"Erişim engellendi|Pardon Our Interruption|px-captcha",
    re.IGNORECASE,
)

_BOT_OLAYLARI = ("dogrulama_gerekli", "dogrulama_bekleniyor")
_BOT_TEMIZ_OLAYLARI = ("dogrulama_gecildi", "dogrulama_zaman_asimi")


# ---------------------------------------------------------------- yardımcılar
def site_adi(url: str | None) -> str:
    """URL'den host; baştaki `www.` atılır. URL yoksa ""."""
    try:
        host = urllib.parse.urlparse(str(url or "")).hostname or ""
    except ValueError:
        host = ""
    return host[4:] if host.startswith("www.") else host


def _zaman_coz(deger: Any) -> datetime | None:
    if isinstance(deger, datetime):
        dt = deger
    else:
        try:
            dt = datetime.fromisoformat(str(deger))
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def _simdi_coz(simdi: Any) -> datetime:
    if simdi is None:
        return datetime.now(TZ)
    if isinstance(simdi, (int, float)):
        return datetime.fromtimestamp(float(simdi), TZ)
    dt = _zaman_coz(simdi)
    return dt or datetime.now(TZ)


def _sekme(t: dict[str, Any]) -> dict[str, Any]:
    url = str(t.get("url") or "")
    baslik = html.unescape(str(t.get("baslik") or t.get("title") or ""))      # CDP başlıkta &#39; vb. bırakabiliyor
    return {"id": t.get("id"), "url": url, "baslik": baslik, "site": site_adi(url),
            "bot": bool(BOT_BASLIK.search(baslik))}


def _site_eslesir(a: Any, b: Any) -> bool:
    a = str(a or "").strip().lower()
    b = str(b or "").strip().lower()
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


# ---------------------------------------------------------------- kurulum tespiti
def _flatpak_var(betik: str) -> bool:
    fp = shutil.which("flatpak")
    if not fp:
        return False
    try:
        return subprocess.run([fp, "info", betik], capture_output=True, timeout=2).returncode == 0
    except Exception:
        return False


def kurulu_mu(ad: str) -> bool:
    """Motor PC'de kurulu mu (sözleşmedeki tespit). Hata → False."""
    try:
        if ad == "brave_kazi":
            from .cekirdek import brave_yolu_bul
            return bool(brave_yolu_bul())
        if ad == "brave_yerel":
            from .yerel_profil import yerel_profil_var
            return bool(yerel_profil_var())
        if ad == "chromium_gorunur":
            kok = Path.home() / ".cache" / "ms-playwright"
            return kok.is_dir() and any(p.is_dir() for p in kok.glob("chromium-*"))
        if ad == "http_k0":
            return True
        if ad == "google_chrome":
            return bool(shutil.which("google-chrome") or shutil.which("google-chrome-stable")
                        or _flatpak_var("com.google.Chrome"))
        if ad == "firefox":
            return bool(shutil.which("firefox") or _flatpak_var("org.mozilla.firefox"))
    except Exception:
        return False
    return False


# ---------------------------------------------------------------- CDP
def _cdp_listesi(port: int, zaman_asimi: float) -> list[dict[str, Any]]:
    """`http://127.0.0.1:<port>/json` hedefleri; CDP kapalıysa ConnectionError."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=zaman_asimi) as r:
            veri = json.loads(r.read().decode("utf-8"))
    except Exception as exc:                      # bağlantı reddedildi, zaman aşımı, bozuk JSON…
        raise ConnectionError(f"CDP {port} yanıt vermiyor: {exc}") from exc
    return [h for h in veri if isinstance(h, dict)] if isinstance(veri, list) else []


def cdp_sekmeleri(port: int) -> list[dict[str, Any]] | None:
    """Açık sekmeler (`type == "page"`), about:blank DAHİL. CDP kapalı/yanıtsız → None (boş liste = CDP açık, sekme yok)."""
    try:
        hedefler = _cdp_listesi(port, 1.5)
    except ConnectionError:
        return None
    return [_sekme(h) for h in hedefler if h.get("type") == "page"]


def _ws_baglan(ws_url: str, zaman_asimi: float):
    from websockets.sync.client import connect
    return connect(ws_url, open_timeout=min(3.0, zaman_asimi), close_timeout=0.5, max_size=None)


def ekran_goruntusu(port: int, sekme_id: str, zaman_asimi: float = 4.0) -> bytes:
    """Sekmenin JPEG ekran görüntüsü (Page.captureScreenshot, quality=55; Emulation KULLANILMAZ).

    Sekme yok → LookupError, CDP kapalı → ConnectionError, yanıt gecikti → TimeoutError.
    """
    son = time.monotonic() + zaman_asimi                         # TOPLAM süre bütçesi (inceleme 15.11)
    hedefler = _cdp_listesi(port, min(1.5, zaman_asimi))          # CDP kapalıysa ConnectionError
    hedef = next((h for h in hedefler if h.get("id") == sekme_id), None)
    ws_url = (hedef or {}).get("webSocketDebuggerUrl")
    if not ws_url:
        raise LookupError(f"sekme bulunamadı: {sekme_id}")
    try:
        with _ws_baglan(ws_url, max(0.2, son - time.monotonic())) as ws:
            ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot",
                                "params": {"format": "jpeg", "quality": 55}}))
            kalan = son - time.monotonic()
            if kalan <= 0:
                raise TimeoutError("ekran görüntüsü süre bütçesi doldu")
            yanit = json.loads(ws.recv(timeout=kalan))
    except TimeoutError:
        raise
    except (ConnectionError, LookupError):
        raise
    except Exception as exc:
        raise ConnectionError(f"CDP bağlantısı başarısız: {exc}") from exc
    veri = (yanit.get("result") or {}).get("data")
    if not veri:
        raise LookupError(f"ekran görüntüsü alınamadı: {yanit.get('error')}")
    return base64.b64decode(veri)


def pencereyi_goster(port: int, sekme_id: str) -> bool:
    """Bot penceresini kullanıcının önüne getir (Browser.getWindowForTarget → setWindowBounds + /json/activate)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2.0) as r:
            surum = json.loads(r.read().decode("utf-8"))
        ws_url = surum.get("webSocketDebuggerUrl")
        if not ws_url:
            return False
        with _ws_baglan(ws_url, 2.0) as ws:
            ws.send(json.dumps({"id": 1, "method": "Browser.getWindowForTarget",
                                "params": {"targetId": sekme_id}}))
            yanit = json.loads(ws.recv(timeout=2.0))
            pencere = (yanit.get("result") or {}).get("windowId")
            if pencere is None:
                return False
            # Önce normal duruma al (simge durumundaysa bounds yok sayılır), sonra konum/boyut.
            ws.send(json.dumps({"id": 2, "method": "Browser.setWindowBounds",
                                "params": {"windowId": pencere, "bounds": {"windowState": "normal"}}}))
            ws.recv(timeout=2.0)
            ws.send(json.dumps({"id": 3, "method": "Browser.setWindowBounds",
                                "params": {"windowId": pencere,
                                           "bounds": {"left": 80, "top": 60, "width": 1366, "height": 900}}}))
            ws.recv(timeout=2.0)
        hedef = "http://127.0.0.1:%d/json/activate/%s" % (port, urllib.parse.quote(str(sekme_id)))
        with urllib.request.urlopen(hedef, timeout=2.0) as r:
            r.read()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- durum hesabı (SAF)
def motor_durumlari(
    kayitlar: list[dict[str, Any]],
    sekme_fn: Callable[[int], list[dict[str, Any]]],
    kurulu_fn: Callable[[str], bool],
    simdi: Any,
    istekler: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Kayıt defteri + CDP sekmeleri + kurulumdan motor kartlarını üret (dış dünyaya dokunmaz).

    `kayitlar`: `gunluk.oku()` biçimi, EN YENİ ÖNCE. `simdi`: datetime / ISO / epoch. `istekler`: {site: kalan_sn}.
    """
    simdi_dt = _simdi_coz(simdi)
    epoch = simdi_dt.timestamp()
    istekler = istekler or {}
    bot_siniri = simdi_dt - timedelta(minutes=BOT_PENCERE_DK)
    aktif_siniri = simdi_dt - timedelta(seconds=AKTIF_PENCERE_SN)
    simdi_iso = simdi_dt.isoformat(timespec="seconds")

    sonuc: list[dict[str, Any]] = []
    for motor in MOTORLAR:
        etiket = motor["etiket"]
        kayitli = bool(kurulu_fn(motor["ad"]))

        ham_sekmeler: list[dict[str, Any]] = []
        cdp_acik = False
        if kayitli and motor["cdp_port"]:
            try:
                liste = sekme_fn(motor["cdp_port"])      # None = CDP kapalı; [] = açık ama sekme yok (inceleme 15.11)
            except Exception:
                liste = None
            cdp_acik = liste is not None
            ham_sekmeler = [_sekme(t) for t in (liste or [])]
        sekmeler = [s for s in ham_sekmeler if not str(s.get("url", "")).startswith("about:blank")]

        # Bu motora ait kayıtlar (en yeni önce).
        ozel = [k for k in kayitlar if k.get("tarayici") == etiket]
        son_kayitlar = [
            {"zaman": k.get("zaman"), "seviye": k.get("seviye"),
             "site": k.get("site"), "mesaj": k.get("mesaj")}
            for k in ozel[:SON_KAYIT_SAYISI]
        ]
        if ozel:
            ilk = ozel[0]
            is_ozeti = (f"{ilk.get('site')} — {ilk.get('mesaj')}" if ilk.get("site")
                        else str(ilk.get("mesaj") or ""))
        else:
            is_ozeti = ""

        son30 = False
        for k in ozel:
            z = _zaman_coz(k.get("zaman"))
            if z is not None and z >= aktif_siniri:
                son30 = True
                break

        # --- bot tespiti: 10 dk içindeki en yeni tetik kaydı, sonrasında temizleyen olay yoksa ---
        tetik = None
        for k in ozel:
            if k.get("olay") in _BOT_OLAYLARI:
                z = _zaman_coz(k.get("zaman"))
                if z is not None and z >= bot_siniri:
                    tetik = k
                    break

        kayit_bot = False
        if tetik is not None:
            site = tetik.get("site")
            temiz = False
            for k in ozel:                        # tetikten DAHA YENİ kayıtlar (liste en yeni önce)
                if k is tetik:
                    break
                if not _site_eslesir(k.get("site"), site):
                    continue
                olay = str(k.get("olay") or "")
                if olay in _BOT_TEMIZ_OLAYLARI:
                    temiz = True
                    break
                if k.get("seviye") == "bilgi" and (olay == "gezinme"
                                                   or olay.startswith(("site_bitti:ok", "ucus_bitti:ok"))):
                    temiz = True
                    break
            kayit_bot = not temiz

        bot_sekme = next((s for s in sekmeler if s.get("bot")), None)
        bot_var = kayit_bot or bot_sekme is not None

        bot: dict[str, Any] | None = None
        if bot_var:
            if bot_sekme is not None:
                b_site, b_url, b_id = bot_sekme.get("site"), bot_sekme.get("url"), bot_sekme.get("id")
            else:
                b_site = (tetik or {}).get("site")
                b_url = (tetik or {}).get("url")
                b_id = next((s.get("id") for s in sekmeler
                             if _site_eslesir(s.get("site"), b_site)), None)
            bekliyor, kalan = False, None
            bekleyen = next((k for k in ozel if k.get("olay") == "dogrulama_bekleniyor"
                             and _site_eslesir(k.get("site"), b_site)), None)
            if bekleyen is not None:
                try:
                    bitis = float((bekleyen.get("veri") or {}).get("bitis"))
                except (TypeError, ValueError):
                    bitis = None
                if bitis is not None and bitis > epoch:
                    bekliyor = True
                    kalan = max(0, int(round(bitis - epoch)))
            if not bekliyor and b_site:
                for anahtar, deger in istekler.items():
                    if _site_eslesir(anahtar, b_site):
                        kalan = int(deger)
                        break
            bot = {
                "site": b_site,
                "url": b_url,
                "zaman": (tetik or {}).get("zaman") or simdi_iso,
                "bekliyor": bekliyor,
                "kalan_sn": kalan,
                "sekme_id": b_id,
            }

        # --- durum / renk ---
        if not kayitli:
            durum, renk = "kurulu_degil", "gri"
        elif bot_var:
            durum, renk = "bot", "sari"
        elif motor["cdp_port"]:
            durum, renk = ("aktif", "yesil") if (cdp_acik and (sekmeler or son30)) else ("bosta", "gri")
        else:
            durum, renk = ("aktif", "yesil") if son30 else ("bosta", "gri")

        if motor["cdp_port"]:
            calisiyor = cdp_acik
        else:
            calisiyor = durum == "aktif"

        sonuc.append({
            "ad": motor["ad"],
            "etiket": etiket,
            "kurulu": kayitli,
            "destekli": motor["destekli"],
            "durum": durum,
            "renk": renk,
            "calisiyor": calisiyor,
            "cdp_port": motor["cdp_port"],
            "sekmeler": sekmeler,
            "is_ozeti": is_ozeti,
            "son_kayitlar": son_kayitlar,
            "bot": bot,
            "aciklama": motor["aciklama"],
        })
    return sonuc


# ---------------------------------------------------------------- gerçek bağımlılıklarla
def durum(simdi: Any = None) -> dict[str, Any]:
    """GET /tarayicilar gövdesi: gerçek CDP + kurulum + kayıt defteri + doğrulama istekleri."""
    simdi_dt = _simdi_coz(simdi)
    kayitlar = gunluk.oku(limit=400).get("kayitlar", [])
    istekler = dogrulama.istekler(simdi=simdi_dt.timestamp())
    return {
        "zaman": simdi_dt.isoformat(timespec="seconds"),
        "dogrulama_istekleri": istekler,
        "motorlar": motor_durumlari(kayitlar, cdp_sekmeleri, kurulu_mu, simdi_dt, istekler),
    }
