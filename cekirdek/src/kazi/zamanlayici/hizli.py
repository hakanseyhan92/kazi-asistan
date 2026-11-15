"""Hızlı HTTP taraması (15.11 kullanıcı isteği): tarayıcı açmadan bot korumasına takılmayan siteleri (akakce, vatan,
pazarama…; uçuşta Google) varsayılan 10 dakikada bir tara, sonuçları ve alarmları hemen işle.

- Siteler: son başarılı katmanı K0 (düz HTTP) olan ETKİN siteler (`site.katman_tercihi`). Bir site HTTP'de engellenirse
  Kademe tercihini değiştirir → kendiliğinden bu listeden çıkar, tarayıcılı kategori taramalarına geçer.
- Bu turda tarayıcı HİÇ açılmaz (`HttpTarayici`: Brave/Chromium bağlantısı ve K1/K2 yasak).
- Zamanlayıcı sürecinde AYRI bir iş parçacığında çalışır; Brave kategori taramalarını beklemez, onları bekletmez.
- Ayarlar `~/.config/kazi/tarama.json`: http_etkin, http_aralik_dk, http_ucus, http_tarayicida_atla
  (paralel_ayar.VARSAYILAN). Durum tablosu `tarama_hizli` (tek satır, id='http').
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from . import plan as plan_mod

ID = "http"
UCUS_HTTP_KAYNAKLARI = ("google",)
ENGEL_ESIGI = 2                       # bir turda bu kadar engelden sonra site o turda bırakılır

SEMA = """CREATE TABLE IF NOT EXISTS tarama_hizli (
    id TEXT PRIMARY KEY,
    son_baslangic TEXT,
    son_bitis TEXT,
    son_sonuc TEXT,
    manuel_istek_zamani TEXT,
    sayac_sifir TEXT
)"""

_calisiyor = threading.Event()        # aynı süreçte üst üste binmesin


# ------------------------------------------------------------------ ayarlar
def ayarlar() -> dict:
    from ..tarayici.paralel_ayar import paralel_ayarlar
    a = paralel_ayarlar()
    return {"etkin": a["http_etkin"], "aralik_dk": a["http_aralik_dk"], "ucus": a["http_ucus"],
            "tarayicida_atla": a["http_tarayicida_atla"], "tarayici_otomatik": a.get("tarayici_otomatik", True)}


# ------------------------------------------------------------------ siteler
def k0_siteleri(v: Any) -> set[str]:
    """Etkin ve son tercihi K0 olan siteler (ürün + uçuş adları karışık)."""
    try:
        return {s.ad for s in v.siteler_listele()
                if str(s.katman_tercihi or "") in ("K0", "Katman.K0") and getattr(s, "etkin", True)}
    except Exception:
        return set()


def brave_zorunlu_siteler(adlar: set[str]) -> set[str]:
    """HTTP'de son 24 sa'te art arda ≥3 engel yiyen siteler (Tarayici._brave_zorunlu): DB'de K0 görünse de
    taramada zaten Brave'e geçer → hızlı turda değil, tarayıcılı taramada kalır (15.11: cimri)."""
    try:
        from ..tarayici import cekirdek as tc
        t = tc.Tarayici.__new__(tc.Tarayici)               # yalnız durum dosyası okunur; bağlantı/tarayıcı yok
        t._durum = tc._oku(tc.DURUM_DOSYA, {})
        t.kademe = None
        t._simdi_fn = lambda: datetime.now(plan_mod.TZ_ISTANBUL)
        return {a for a in adlar if t._brave_zorunlu(a)}
    except Exception:
        return set()


def urun_siteleri(v: Any) -> list[str]:
    """Hızlı HTTP turunun ürün siteleri = 'http' turuna atanmış, etkin siteler (tur_atama)."""
    from . import tur_atama
    kapali = _kapali(v)
    return [s for s in tur_atama.siteler(v, tur_atama.HTTP, "urun") if s not in kapali]


def ucus_kaynaklari(v: Any) -> list[str]:
    from . import tur_atama
    return tur_atama.siteler(v, tur_atama.HTTP, "ucus")


def _kapali(v: Any) -> set[str]:
    try:
        return set(v.kapali_siteler())
    except Exception:
        return set()


def basarisiz_ciftler(v: Any) -> set[tuple[str, str]]:
    """Son HTTP turunda başarısız (engel/hata) ürün×site işleri → Tarayıcı turu bunları da tarar."""
    try:
        return {(u, s) for u, s in (durum_oku(v).get("son_sonuc") or {}).get("basarisiz") or []}
    except Exception:
        return set()


def basarisiz_ucus(v: Any) -> set[str]:
    try:
        return set((durum_oku(v).get("son_sonuc") or {}).get("basarisiz_ucus") or [])
    except Exception:
        return set()


def son_tur_engelli(v: Any) -> set[str]:
    try:
        return set((durum_oku(v).get("son_sonuc") or {}).get("engelli") or [])
    except Exception:
        return set()


def tarayici_turu_urun(v: Any, urun_idler: list[str] | None = None) -> tuple[list[str] | None, set[tuple[str, str]]]:
    """Tarayıcı turunun (siteler, ek ürün×site işleri). HTTP turu KAPALIYSA (None, ∅) = bütün siteler taranır."""
    from . import tur_atama
    if not ayarlar()["etkin"]:
        return None, set()
    siteler = tur_atama.siteler(v, tur_atama.TARAYICI, "urun")
    ek = basarisiz_ciftler(v)
    if urun_idler is not None:
        ek = {(u, s) for u, s in ek if u in set(urun_idler)}
    return siteler, ek


def tarayici_turu_ucus(v: Any) -> list[str] | None:
    """Tarayıcı turunun uçuş kaynakları (+ son HTTP turunda başarısız olanlar). HTTP turu kapalıysa None = hepsi."""
    from . import tur_atama
    if not ayarlar()["etkin"]:
        return None
    k = set(tur_atama.siteler(v, tur_atama.TARAYICI, "ucus")) | (basarisiz_ucus(v) & set(ucus_kaynaklari(v)))
    return sorted(k)


# ------------------------------------------------------------------ tarayıcısız tarayıcı
def _http_tarayici_sinifi():
    from ..modeller import Katman
    from ..tarayici.cekirdek import BotEngeli, Tarayici

    class HttpTarayici(Tarayici):
        """Yalnız K0 (düz HTTP). Brave/Chromium'a bağlanmaya çalışan her yol BotEngeli fırlatır → site o turda atlanır."""

        def __init__(self, **kw: Any) -> None:
            super().__init__(**kw)
            self.kademe.k1 = None
            self.kademe._denenecek_katmanlar = lambda site: [Katman.K0]

            def _k2_yok(url: str, site: str):
                raise BotEngeli(site, "hızlı HTTP turu: tarayıcı kullanılmaz")
            self.kademe._k2_dene = _k2_yok

        @property
        def gunluk_adi(self) -> str:
            return "HTTP (K0)"

        def _brave_zorunlu(self, site: str) -> bool:
            return False                     # site HTTP turuna atanmış: K0 her zaman denenir (geçiş eşiği tur_atama'da)

        def _brave_baglan(self) -> Any:
            raise BotEngeli("http", "hızlı HTTP turu: tarayıcı kullanılmaz")

        def hiz_sinirla(self, site: str) -> None:
            """Bu turda ENGEL_ESIGI kez engellenen siteye bir daha istek atılmaz (bekleme 2× büyüyüp turu
            dakikalarca uzatıyordu: itopya 225 sn). Site, tarayıcılı taramada taranır (son_tur_engelli)."""
            if self.oto_hiz is not None:
                if self.oto_hiz.durum().get(site, {}).get("engel_sayisi", 0) >= ENGEL_ESIGI:
                    raise BotEngeli(site, "hızlı HTTP turu: bu turda tekrar engellendi, tarayıcılı taramaya bırakıldı")
            super().hiz_sinirla(site)

    return HttpTarayici


def http_fabrikasi(v: Any = None, bildirim_fn: Callable | None = None):
    """Havuz işçileri için HttpTarayici üreticisi; site bekleme durumu (2,5 sn kuralı + OtoHiz) işçiler arasında ortak."""
    from ..tarayici.oto_hiz import OtoHiz
    sinif = _http_tarayici_sinifi()
    ortak_son_istek: dict[str, float] = {}
    ortak_hiz = OtoHiz(alt_sn=3.0, ust_sn=20.0, baslangic_sn=3.0)   # akakce ~2 sn aralıkta 429 verdi

    def _uret(no: int):
        t = sinif(veritabani=v, bildirim_fn=bildirim_fn or (lambda o: None))
        t._son_istek = ortak_son_istek
        t.oto_hiz = ortak_hiz
        return t
    return _uret


# ------------------------------------------------------------------ durum tablosu
def _con(v: Any):
    if hasattr(v, "baglanti"):
        return v.baglanti, getattr(v, "_kilit", None) or nullcontext()
    return v, nullcontext()


def kur(v: Any) -> None:
    con, kilit = _con(v)
    with kilit:
        con.execute(SEMA)
        con.execute("INSERT OR IGNORE INTO tarama_hizli (id) VALUES (?)", (ID,))
        con.commit()


def durum_oku(v: Any) -> dict:
    kur(v)
    con, kilit = _con(v)
    with kilit:
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM tarama_hizli WHERE id = ?", (ID,)).fetchone()
    d = dict(r) if r else {}
    import json
    try:
        d["son_sonuc"] = json.loads(d.get("son_sonuc") or "null")
    except ValueError:
        d["son_sonuc"] = None
    return d


def _yaz(v: Any, **alan: Any) -> None:
    kur(v)
    con, kilit = _con(v)
    with kilit:
        con.execute(f"UPDATE tarama_hizli SET {', '.join(f'{k} = ?' for k in alan)} WHERE id = ?", (*alan.values(), ID))
        con.commit()


def manuel_iste(v: Any, zaman_iso: str) -> None:
    _yaz(v, manuel_istek_zamani=zaman_iso)


def sayac_sifirla(v: Any, zaman_iso: str) -> None:
    _yaz(v, sayac_sifir=zaman_iso)


def calisiyor_mu(d: dict) -> bool:
    bas, bit = plan_mod.iso_coz(d.get("son_baslangic")), plan_mod.iso_coz(d.get("son_bitis"))
    return bas is not None and (bit is None or bit < bas)


def sonraki(d: dict, aralik_dk: int) -> datetime | None:
    """Vade: son bitiş (ya da sayaç sıfırlama, hangisi yeniyse) + aralık; hiç taranmadıysa None (= hemen)."""
    taban = max([z for z in (plan_mod.iso_coz(d.get("son_bitis")), plan_mod.iso_coz(d.get("sayac_sifir"))) if z],
                default=None)
    return taban + timedelta(minutes=int(aralik_dk)) if taban else None


def vadesi_geldi_mi(d: dict, aralik_dk: int, simdi: datetime) -> bool:
    if calisiyor_mu(d):
        return False
    manuel = plan_mod.iso_coz(d.get("manuel_istek_zamani"))
    bas = plan_mod.iso_coz(d.get("son_baslangic"))
    if manuel and (bas is None or manuel > bas):
        return True
    s = sonraki(d, aralik_dk)
    return s is None or s <= simdi


def plan_ozeti(v: Any, simdi: datetime) -> dict:
    """GET /tarama/http gövdesi."""
    from ..tarayici.paralel_ayar import SECENEKLER
    a = ayarlar()
    d = durum_oku(v)
    s = sonraki(d, a["aralik_dk"])
    from . import tur_atama
    kapali = _kapali(v)
    atama = [{**x, "http_arama": tur_atama.http_ile_aranabilir(x["site"]), "etkin": x["site"] not in kapali}
             for x in tur_atama.atamalar(v).values()]
    return {**a, "secenekler_dk": list(SECENEKLER["http_aralik_dk"]), "atamalar": atama,
            "hata_esigi": tur_atama.HATA_ESIGI,
            "urun_siteleri": urun_siteleri(v), "ucus_kaynaklari": ucus_kaynaklari(v),
            "durum": "calisiyor" if calisiyor_mu(d) else ("kapali" if not a["etkin"] else "bekliyor"),
            "son_baslangic": d.get("son_baslangic"), "son_bitis": d.get("son_bitis"), "son_sonuc": d.get("son_sonuc"),
            "sonraki": plan_mod.iso_bicim(s) if s else None,
            "kalan_sn": max(0, int((s - simdi).total_seconds())) if s else 0}


# ------------------------------------------------------------------ tarama
def tara(v: Any, simdi_fn: Callable[[], datetime] | None = None, log: Callable[[str], Any] = print,
         urun_tara_fn: Callable | None = None, ucus_tara_fn: Callable | None = None,
         alarm_fn: Callable | None = None) -> dict:
    """Bir hızlı tur: tüm takipteki ürünler × HTTP siteleri (+ uçuşta Google). Özet döndürür."""
    import json
    from ..bildirim.telegram import alarm_denetle, anlik_alarm
    from ..modeller import Olay
    from ..tarama.tara import HTTP_ISCI
    from ..tarama.tara import tara as urun_tara

    simdi_fn = simdi_fn or (lambda: datetime.now(plan_mod.TZ_ISTANBUL))
    a = ayarlar()
    t0 = time.monotonic()
    _yaz(v, son_baslangic=plan_mod.iso_bicim(simdi_fn()))
    teklif = hata = ucus = 0
    engelli: set[str] = set()
    siteler = urun_siteleri(v)
    kaynaklar = ucus_kaynaklari(v) if a["ucus"] else []

    site_hata: dict[str, int] = {}           # tur_atama sayacı: ürün×site (uçuşta bacak×tarih×kaynak) işi başına
    basarisiz: set[tuple[str, str]] = set()   # Tarayıcı turu bunları da tarar
    basarisiz_u: set[str] = set()

    def _engelleri_topla(kapsam: dict, ucus_mu: bool = False) -> int:
        """Site 'engelli' sayılır: turda ≥ENGEL_ESIGI engel ya da hiç başarı yok (tek 429'dan toparlanan akakce
        sayılmaz; o ürün 10 dk sonraki turda gelir)."""
        n = 0
        blok: dict[str, int] = {}
        basari: dict[str, int] = {}
        for anahtar, k in (kapsam or {}).items():
            if not isinstance(k, dict):
                continue
            site = anahtar[-1] if isinstance(anahtar, tuple) else str(anahtar)
            tur = k.get("tur")
            if tur in ("blok", "blok_atlandi"):
                blok[site] = blok.get(site, 0) + 1
            elif tur in ("ok", "bos") and not k.get("hata"):
                basari[site] = basari.get(site, 0) + 1
            if k.get("hata") or tur in ("blok", "blok_atlandi", "hata"):
                n += 1
                site_hata[site] = site_hata.get(site, 0) + 1
                if ucus_mu:
                    basarisiz_u.add(site)
                elif isinstance(anahtar, tuple) and len(anahtar) == 2:
                    basarisiz.add((str(anahtar[0]), site))
        engelli.update(s for s, b in blok.items() if b >= ENGEL_ESIGI or not basari.get(s))
        return n
    try:
        if siteler:
            fab = http_fabrikasi(v)
            t = fab(0)
            try:
                sonuc = (urun_tara_fn or urun_tara)(v, t, siteler=siteler, sekme_sayisi=HTTP_ISCI,
                                                    tarayici_fabrika=fab, anlik_alarm_fn=anlik_alarm)
            finally:
                t.kapat()
            teklif = sum(len(x) for x in (getattr(sonuc, "teklifler", None) or {}).values())
            hata += _engelleri_topla(getattr(sonuc, "kapsam", None))
        if kaynaklar:
            from .calistir import _kayitli_ucus_plani
            from ..ucus import tara as ucus_mod
            kayitli_plan, bacak_idler = _kayitli_ucus_plani()
            ref = simdi_fn()
            kw: dict[str, Any] = {"plan": kayitli_plan, "azami_tarih": 1,
                                  "rotasyon": ref.timetuple().tm_yday * 8 + ref.hour // 3, "kaynaklar": kaynaklar}
            if bacak_idler is not None:
                kw["bacak_idler"] = bacak_idler
            t = _http_tarayici_sinifi()(veritabani=v, bildirim_fn=lambda o: None)
            try:
                us = (ucus_tara_fn or ucus_mod.tara)(v, t, **kw)
            finally:
                t.kapat()
            ucus = sum(len(s) for s in (getattr(us, "segmentler", None) or {}).values())
            hata += _engelleri_topla(getattr(us, "kapsam", None), ucus_mu=True)
        try:
            (alarm_fn or alarm_denetle)(v)
        except Exception as exc:                               # noqa: BLE001
            log(f"hızlı tur alarm hatası: {type(exc).__name__}: {exc}")
    except Exception as exc:                                   # noqa: BLE001
        hata += 1
        log(f"hızlı HTTP turu hatası: {type(exc).__name__}: {exc}")
    sure = int(round(time.monotonic() - t0))
    bitis = plan_mod.iso_bicim(simdi_fn())
    tasinan: list[str] = []
    try:
        from . import tur_atama
        tasinan = tur_atama.http_hatalarini_isle(v, site_hata, set(siteler) | set(kaynaklar), bitis)
        for s_ in tasinan:
            log(f"⇄ {s_}: HTTP turunda hata sayısı {tur_atama.HATA_ESIGI}'ü aştı → Tarayıcı turuna geçti")
    except Exception as exc:                                   # noqa: BLE001
        log(f"tur ataması güncellenemedi: {type(exc).__name__}: {exc}")
    ozet = {"teklif": teklif, "ucus": ucus, "hata": hata, "sure_sn": sure, "siteler": siteler + kaynaklar,
            "engelli": sorted(engelli), "site_hata": site_hata, "basarisiz": sorted([list(c) for c in basarisiz]),
            "basarisiz_ucus": sorted(basarisiz_u), "tasinan": tasinan}
    _yaz(v, son_bitis=bitis, son_sonuc=json.dumps(ozet, ensure_ascii=False))
    try:
        v.olay_ekle(Olay(tur="tarama_bitti", zaman=bitis,
                         mesaj=f"Hızlı HTTP: {teklif} teklif, {ucus} uçuş, {hata} hata ({sure}s)",
                         veri={"kategori": ID, **ozet}))
    except Exception:
        pass
    log(f"hızlı HTTP turu bitti — {teklif} teklif, {ucus} uçuş, {hata} hata, {sure} sn ({', '.join(siteler + kaynaklar)})"
        + (f"; başarısız {len(basarisiz)} iş Tarayıcı turuna bırakıldı" if basarisiz else ""))
    return ozet


def dongu(db_yolu: Path, dur_event: threading.Event, log: Callable[[str], Any] = print, aralik_sn: float = 15.0,
          simdi_fn: Callable[[], datetime] | None = None) -> None:
    """Zamanlayıcı sürecinde ayrı iş parçacığı: 15 sn'de bir vade denetimi; vadesi gelince bir tur."""
    from ..db import VeriTabani
    simdi_fn = simdi_fn or (lambda: datetime.now(plan_mod.TZ_ISTANBUL))
    v = VeriTabani(db_yolu)
    try:
        # süreç yeniden başladıysa yarım kalmış tur kaydı kapatılır (yoksa sonsuza dek "çalışıyor" görünür)
        d = durum_oku(v)
        if calisiyor_mu(d):
            _yaz(v, son_bitis=plan_mod.iso_bicim(simdi_fn()))
        while not dur_event.is_set():
            try:
                a = ayarlar()
                if a["etkin"] and vadesi_geldi_mi(durum_oku(v), a["aralik_dk"], simdi_fn()) and not _calisiyor.is_set():
                    _calisiyor.set()
                    try:
                        log("hızlı HTTP turu başladı")
                        tara(v, simdi_fn=simdi_fn, log=log)
                    finally:
                        _calisiyor.clear()
            except Exception as exc:                           # noqa: BLE001
                log(f"hızlı tur döngü hatası: {type(exc).__name__}: {exc}")
            dur_event.wait(timeout=aralik_sn)
    finally:
        v.kapat()
