"""Tarama orkestrası: ürün × sorgu × site → eşleştirme → teklifler ve olay yayını.

Kurallar (SARTNAME §5, §6):
- Her (site, ürün) denemesi ayrı try/except bloğunda çalışır; biri hata verse diğerleri sürer.
- Eşleşme süzgeci: `eslestir.uygun_mu(kurallar, kart)` ve `eslestir.neden(kurallar, kart)`.
  Elenen kartların sayısı ve örnek gerekçeleri `TaramaSonucu` içinde tutulur.
- Geçerli kartlar `kart.teklife(urun_id)` ile `Teklif`'e dönüştürülüp DB'ye (`v.teklif_ekle`) yazılır.
- Her site sonunda `tarama_site_bitti` olayı (`{site, urun_id, adet, sure, katman}`) yayınlanır.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import inspect
import json
from pathlib import Path
import re
import sys
import threading
import time
import types
from typing import Any, Callable

from .. import eslestir
from ..modeller import TZ_ISTANBUL, Katman, Teklif, UrunKimligi, simdi_iso
from ..siteler import SITELER
from ..siteler._kart import _KATMAN_HARITASI, Kart
from ..tarayici import DURUM_DOSYA, BotEngeli, Tarayici
from ..tarayici.paralel_ayar import paralel_ayarlar
from . import depo

TOPLAYICILAR = {"akakce", "cimri", "epey"}
HTTP_VARSAYILAN = [
    "akakce",
    "n11",
    "vatan",
    "itopya",
    "gaminggentr",
    "gamegaraj",
    "pazarama",
]
BAYAT_SAAT = 24.0
AZAMI_ELENEN_ORNEK = 20


@dataclass
class TaramaSonucu:
    zaman: str
    urun_idler: list[str] = field(default_factory=list)
    siteler: list[str] = field(default_factory=list)
    teklifler: dict[str, list[Teklif]] = field(default_factory=dict)
    kapsam: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    elenen_sayisi: dict[str, int] = field(default_factory=dict)
    elenen_ornekleri: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    elenen_gerekceleri: dict[str, list[str]] = field(default_factory=dict)

    @property
    def elenen(self) -> dict[str, int]:
        return self.elenen_sayisi

    @property
    def toplam_elenen(self) -> int:
        return sum(self.elenen_sayisi.values())

    def site_ok(self) -> list[str]:
        ok = set()
        for (_u, site), k in self.kapsam.items():
            if k.get("hata") is None and k.get("tur") in ("ok", "bos"):
                ok.add(site)
        return sorted(ok)

    def urun_ok(self) -> list[str]:
        ok = []
        for u in self.urun_idler:
            if sum(v.get("sayi", 0) for (uu, _s), v in self.kapsam.items() if uu == u):
                ok.append(u)
        return ok

    def kapsam_ozeti(self) -> str:
        return (
            f"kapsam: {len(self.site_ok())}/{len(self.siteler)} site, "
            f"{len(self.urun_ok())}/{len(self.urun_idler)} ürün"
        )

    def tablo(self) -> str:
        """Ürün × site matrisi: sayı/katman, HATA, blok."""
        if not self.kapsam:
            return "(kapsam kaydı yok)"
        siteler = self.siteler
        bas = "ürün".ljust(16) + " " + " ".join(s[:9].rjust(10) for s in siteler)
        satirlar = [bas]
        for u in self.urun_idler:
            hucreler = []
            for s in siteler:
                k = self.kapsam.get((u, s))
                if k is None:
                    hucreler.append("-".rjust(10))
                elif k.get("hata"):
                    etiket = "blok" if k.get("tur") == "blok" else "HATA"
                    hucreler.append(f"{etiket}:{k.get('sayi', 0)}".rjust(10))
                else:
                    hucreler.append(f"{k.get('sayi', 0)}/{k.get('yontem', '?')}".rjust(10))
            satirlar.append(u[:16].ljust(16) + " " + " ".join(hucreler))
        satirlar.append(self.kapsam_ozeti())
        return "\n".join(satirlar)


# ------------------------------------------------------------------ yardımcılar

def _site_penceresi_kapat(t, site: str) -> None:
    """Havuz: bekleyen işi kalmayan sitenin bu işçideki penceresini kapat (sahte/eski tarayıcıda yöntem yoksa geç)."""
    kapat = getattr(t, "sekme_kapat", None)
    if callable(kapat):
        kapat(site)

def http_siteleri(v: Any = None, durum_dosya: Path | str | None = None) -> list[str]:
    """Son yöntemi K0/HTTP olan siteler; bulunamazsa varsayılan HTTP siteleri."""
    secilen: set[str] = set()
    if v is not None and hasattr(v, "siteler_listele"):
        try:
            for s in v.siteler_listele(etkin=True):
                if s.ad in SITELER and s.katman_tercihi == Katman.K0:
                    secilen.add(s.ad)
        except Exception:
            pass

    yol = Path(durum_dosya) if durum_dosya else DURUM_DOSYA
    try:
        d = json.loads(yol.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    if isinstance(d, dict):
        for k, val in d.items():
            if k in SITELER and isinstance(val, dict) and val.get("son_yontem") in ("http", "K0", "k0"):
                secilen.add(k)

    return sorted(secilen) if secilen else list(HTTP_VARSAYILAN)


_SIL = re.compile(r"[^0-9a-zçğıöşü ]+")


def _norm(metin: Any) -> str:
    s = str(metin or "").lower().replace("ı", "i")
    s = _SIL.sub(" ", s)
    return " ".join(s.split())


def _url_norm(url: str | None) -> str:
    return (url or "").strip().rstrip("/").lower()


def _depo_adi(kart: Kart) -> str:
    if getattr(kart, "kaynak", None):
        return _norm(kart.kaynak)
    return _norm(kart.site)


HTTP_ISCI = 4


def _k0_siteleri(v: Any) -> set[str]:
    """Son tercihi K0 (düz HTTP) olan siteler — tarayıcı sekmesi gerektirmez."""
    try:
        return {s.ad for s in v.siteler_listele() if str(s.katman_tercihi or "") in ("K0", "Katman.K0")}
    except Exception:
        return set()


def _kapali_gerekce(kart: Kart, kapali: set[str]) -> str | None:
    """Toplayıcı (Akakçe/Cimri/Epey) kartının mağazası kullanıcının kapattığı bir siteyse elenme gerekçesi."""
    if not kapali:
        return None
    from ..site_adi import site_anahtari
    for ad in (getattr(kart, "kaynak", None), kart.site, _depo_adi(kart)):
        anahtar = site_anahtari(ad) if ad else None
        if anahtar and (anahtar in kapali or str(anahtar).lower() in kapali):
            return f"site aramadan çıkarıldı: {anahtar}"
    return None


def _karta_cevir(nesne: Any, varsayilan_site: str) -> Kart | None:
    """Site modülünün döndürdüğü `Kart`, `Teklif` ya da `dict` nesnesini `Kart`'a sarar."""
    if nesne is None:
        return None
    if isinstance(nesne, Kart):
        return nesne
    if isinstance(nesne, dict):
        baslik = nesne.get("baslik") or nesne.get("ad") or ""
        fiyat = nesne.get("sepet_fiyati")
        if fiyat is None:
            fiyat = nesne.get("fiyat")
        if not baslik or fiyat is None:
            return None
        return Kart(
            site=str(nesne.get("site") or varsayilan_site),
            baslik=str(baslik),
            url=str(nesne.get("url") or ""),
            sepet_fiyati=float(fiyat),
            liste_fiyati=nesne.get("liste_fiyati") or nesne.get("liste_fiyat"),
            satici=nesne.get("satici"),
            kaynak=nesne.get("kaynak"),
            pesin_taksit=nesne.get("pesin_taksit") or nesne.get("taksit"),
            stok=nesne.get("stok"),
            yontem=str(nesne.get("yontem") or "http"),
            zaman=str(nesne.get("zaman") or nesne.get("gozlem_zamani") or simdi_iso()),
            guncelleme=nesne.get("guncelleme"),
            durum=str(nesne.get("durum") or "gecerli"),
            kampanya=nesne.get("kampanya") or nesne.get("kupon"),
        )
    # Teklif veya eski Teklif benzeri nesne
    baslik = getattr(nesne, "baslik", None) or getattr(nesne, "ad", None) or ""
    fiyat = getattr(nesne, "sepet_fiyati", None)
    if fiyat is None:
        fiyat = getattr(nesne, "fiyat", None)
    if not baslik or fiyat is None:
        return None
    katman = getattr(nesne, "kaynak_katmani", None)
    yontem = getattr(nesne, "yontem", None)
    if not yontem and katman is not None:
        yontem = {"K0": "http", "K1": "chromium", "K2": "brave", "K3": "brave"}.get(str(katman), "http")
    taksit = getattr(nesne, "pesin_taksit", None)
    if taksit is None:
        taksit = getattr(nesne, "taksit", None)
    return Kart(
        site=str(getattr(nesne, "site", None) or varsayilan_site),
        baslik=str(baslik),
        url=str(getattr(nesne, "url", None) or ""),
        sepet_fiyati=float(fiyat),
        liste_fiyati=getattr(nesne, "liste_fiyati", None) or getattr(nesne, "liste_fiyat", None),
        satici=getattr(nesne, "satici", None),
        kaynak=getattr(nesne, "kaynak", None),
        pesin_taksit=taksit,
        stok=getattr(nesne, "stok", None),
        yontem=str(yontem or "http"),
        zaman=str(getattr(nesne, "gozlem_zamani", None) or getattr(nesne, "zaman", None) or simdi_iso()),
        guncelleme=getattr(nesne, "guncelleme", None),
        durum=str(getattr(nesne, "durum", None) or "gecerli"),
        kampanya=getattr(nesne, "kampanya", None) or getattr(nesne, "kupon", None),
    )


def tekillestir(kartlar: list[Kart]) -> list[Kart]:
    """Aynı (mağaza, satıcı, ad, fiyat) ya da aynı URL → tek satır + `kaynaklar`."""
    gruplar: list[list[Kart]] = []
    indeks: dict[tuple, int] = {}
    for k in kartlar:
        anahtarlar = [
            ("p", _depo_adi(k), _norm(k.satici), _norm(k.baslik), round(float(k.sepet_fiyati))),
            ("u", _url_norm(k.url)),
        ]
        hedef = None
        for a in anahtarlar:
            if a in indeks:
                hedef = indeks[a]
                break
        if hedef is None:
            hedef = len(gruplar)
            gruplar.append([])
        gruplar[hedef].append(k)
        for a in anahtarlar:
            indeks[a] = hedef

    sonuc: list[Kart] = []
    for g in gruplar:
        gercek = [x for x in g if x.site not in TOPLAYICILAR]
        temsilci = min(gercek or g, key=lambda x: x.sepet_fiyati)
        isimler = list(dict.fromkeys(x.site for x in g))
        setattr(temsilci, "kaynaklar", "+".join(isimler))
        if (
            len(g) > 1
            and any(x.durum == "bayat" for x in g)
            and temsilci.durum == "gecerli"
            and temsilci.site in TOPLAYICILAR
        ):
            temsilci.durum = "bayat"
        sonuc.append(temsilci)
    return sorted(sonuc, key=lambda x: x.sepet_fiyati)


def bayat_mi(kart: Kart, simdi: datetime | None = None, saat: float = BAYAT_SAAT) -> bool:
    """Toplayıcı teklifinin bildirdiği güncelleme zamanı `saat` saatten eski mi?"""
    if kart.site not in TOPLAYICILAR and not getattr(kart, "kaynak", None):
        return False
    if not getattr(kart, "guncelleme", None):
        return False
    try:
        dt = datetime.fromisoformat(str(kart.guncelleme).replace("Z", "+00:00"))
    except Exception:
        return False
    simdi = simdi or datetime.now(TZ_ISTANBUL)
    if dt.tzinfo is None and simdi.tzinfo is not None:
        dt = dt.replace(tzinfo=simdi.tzinfo)
    elif dt.tzinfo is not None and simdi.tzinfo is None:
        simdi = simdi.replace(tzinfo=dt.tzinfo)
    return (simdi - dt) > timedelta(hours=saat)


def _ara_cagir(mod: Any, t: Any, sorgu: str, min_f: Any, max_f: Any, sayfa: int) -> list[Any]:
    ara_fn = mod.ara
    try:
        sig = inspect.signature(ara_fn)
        param_adlari = set(sig.parameters.keys())
        has_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        kw: dict[str, Any] = {}
        if has_var_kw or "min_f" in param_adlari:
            kw["min_f"] = min_f
        if has_var_kw or "max_f" in param_adlari:
            kw["max_f"] = max_f
        if has_var_kw or "sayfa" in param_adlari:
            kw["sayfa"] = sayfa
        return ara_fn(t, sorgu, **kw) or []
    except TypeError:
        return ara_fn(t, sorgu) or []


def _kurallari_cikar(urun: UrunKimligi) -> dict[str, Any]:
    k = dict(urun.eslesme_kurallari or {})
    if urun.fiyat_bandi is not None:
        if k.get("min_fiyat") is None and urun.fiyat_bandi.alt is not None:
            k["min_fiyat"] = urun.fiyat_bandi.alt
        if k.get("max_fiyat") is None and urun.fiyat_bandi.ust is not None:
            k["max_fiyat"] = urun.fiyat_bandi.ust
    if not k.get("sorgular"):
        k["sorgular"] = [urun.kanonik_ad]
    return k


def _bot_engeli_mi(exc: BaseException) -> bool:
    return isinstance(exc, BotEngeli) or type(exc).__name__ == "BotEngeli"


# ------------------------------------------------------------------ ana giriş
def tara(
    v: Any = None,
    tarayici: Any = None,
    urun_idler: list[str] | None = None,
    siteler: list[str] | None = None,
    hizli: bool = False,
    bildirim_fn: Callable[[dict[str, Any]], Any] | None = None,
    *,
    con: Any = None,
    sekme_sayisi: int | None = None,
    tarayici_fabrika: Callable[[int], Any] | None = None,
    yedek_fabrika: Callable[[], Any] | None = None,
    yerel_fabrika: Callable[[], Any] | None = None,
    anlik_alarm_fn: Callable[[Any, str, list[Kart]], Any] | None = None,
    ek_ciftler: set[tuple[str, str]] | None = None,
) -> TaramaSonucu:
    """Ürünleri sitelerde tara; eşleşenleri DB'ye yaz, elenenleri ve kapsamı döndür."""
    hedef_v = v if v is not None else con
    v_kendi = hedef_v is None
    if v_kendi:
        from ..ayar import db_yolu as _db_yolu
        from ..db import VeriTabani as _VT

        hedef_v = _VT(_db_yolu())
    depo.kur(hedef_v)

    t_kendi = tarayici is None
    t = (
        tarayici
        if tarayici is not None
        else Tarayici(
            veritabani=hedef_v if hasattr(hedef_v, "siteler_listele") else None,
            bildirim_fn=bildirim_fn,
        )
    )
    etkin_bildirim = bildirim_fn or getattr(t, "bildirim_fn", None)
    zaman = simdi_iso()

    urunler = depo.urunler_listele(hedef_v, urun_idler=urun_idler, aktif=True, otomatik_ekle=True)
    kapali: set[str] = set()
    if hasattr(hedef_v, "kapali_siteler"):
        try:
            kapali = set(hedef_v.kapali_siteler())
        except Exception:
            kapali = set()
    # siteler=None → hepsi; boş liste → hiçbiri (yalnız ek_ciftler; 15.11 Tarayıcı turu)
    hedef_siteler = [s for s in (list(siteler) if siteler is not None else list(SITELER)) if s not in kapali]
    ek_ciftler = {(u, s_) for u, s_ in (ek_ciftler or set()) if s_ in SITELER and s_ not in kapali}
    if hizli:
        izinli = set(http_siteleri(hedef_v))
        hedef_siteler = [s for s in hedef_siteler if s in izinli]

    sonuc = TaramaSonucu(
        zaman=zaman,
        urun_idler=[u.id or "" for u in urunler],
        siteler=list(hedef_siteler),
    )

    _kilit = threading.RLock()

    def _bir_site(t_is: Any, urun: UrunKimligi, urun_id: str, site: str, kurallar: dict[str, Any],
                  eslesme_kurallari: dict[str, Any], _ele_kaydet: Callable[..., None]) -> list[Kart]:
        """Tek (ürün, site) işi: ara → eşleştir → kapsam/olay kaydı. Bulunan kartları döndürür (DB'ye teklif yazmaz)."""
        kapali_simdi = kapali               # C2 #2: işe özel anlık kopya (paylaşılan değişkene yazılmaz)
        # Ayarlar'dan site kapatma yürüyen taramada da hemen geçerli olsun (15.11: tarama başladıktan
        # 39 sn sonra kapatılan Sinerji yine taranmıştı)
        if hasattr(hedef_v, "kapali_siteler"):
            try:
                kapali_simdi = set(hedef_v.kapali_siteler())
            except Exception:
                pass
        if site in kapali_simdi:
            return []
        mod = SITELER.get(site)
        _oto = getattr(t_is, "oto_hiz", None)
        oto_bas = _oto.sayac(site) if _oto is not None and hasattr(_oto, "sayac") else (0, 0.0)
        t0 = time.monotonic()

        def _ara_calistir(t_x: Any) -> dict[str, Any]:
            """Arama gövdesi: verilen tarayıcıyla ara → eşleştir. Sonuç sözlüğü döner."""
            bulunan: list[Kart] = []
            site_elenen = 0
            site_elenen_ornek: list[dict[str, Any]] = []
            yontem = "http"
            hata: str | None = None
            tur = "ok"
            try:
                if mod is None or not hasattr(mod, "ara"):
                    raise RuntimeError(f"bağdaştırıcı yok: {site}")
                sorgular = kurallar.get("sorgular") or [urun.kanonik_ad]
                for sorgu in sorgular:
                    parca = _ara_cagir(
                        mod,
                        t_x,
                        sorgu,
                        min_f=kurallar.get("min_fiyat"),
                        max_f=kurallar.get("max_fiyat"),
                        sayfa=int(kurallar.get("sayfa") or 2),
                    )
                    for ham in parca:
                        kart = _karta_cevir(ham, site)
                        if kart is None:
                            site_elenen += 1
                            sonuc.elenen_sayisi[urun_id] += 1
                            continue
                        if kart.yontem:
                            yontem = kart.yontem
                        gerekce = _kapali_gerekce(kart, kapali_simdi) or eslestir.neden(eslesme_kurallari, kart)
                        if eslestir.uygun_mu(eslesme_kurallari, kart) and gerekce is None:
                            bulunan.append(kart)
                        else:
                            gerekce_str = gerekce or "eşleşme kuralına uymadı"
                            site_elenen += 1
                            if len(site_elenen_ornek) < 5:
                                site_elenen_ornek.append(
                                    {"baslik": kart.baslik, "fiyat": kart.sepet_fiyati, "gerekce": gerekce_str}
                                )
                            _ele_kaydet(site, kart, gerekce_str)

                # Akakçe: en ucuz 3 modelin satıcı teklifleri de eklensin
                if site == "akakce" and hasattr(mod, "teklifler") and bulunan:
                    for model_kart in sorted(bulunan, key=lambda x: x.sepet_fiyati)[:3]:
                        try:
                            for ham_tk in mod.teklifler(t_x, model_kart.url) or []:
                                kart_tk = _karta_cevir(ham_tk, site)
                                if kart_tk is None:
                                    continue
                                gerekce = _kapali_gerekce(kart_tk, kapali_simdi) or eslestir.neden(eslesme_kurallari, kart_tk)
                                if eslestir.uygun_mu(eslesme_kurallari, kart_tk) and gerekce is None:
                                    bulunan.append(kart_tk)
                                else:
                                    site_elenen += 1
                                    _ele_kaydet(site, kart_tk, gerekce or "eşleşme kuralına uymadı")
                        except Exception as e:
                            if _bot_engeli_mi(e):
                                raise
                            hata = f"akakce teklifler: {type(e).__name__}: {e}"
                            tur = "hata"

                if not bulunan and not hata:
                    tur = "bos"
            except Exception as e:
                if _bot_engeli_mi(e):
                    hata = f"blok: {e}"
                    tur = "blok"
                else:
                    hata = f"{type(e).__name__}: {e}"
                    tur = "hata"
            return {"bulunan": bulunan, "elenen": site_elenen, "ornek": site_elenen_ornek,
                    "yontem": yontem, "hata": hata, "tur": tur}

        r = _ara_calistir(t_is)
        yedek_adi: str | None = None
        if r["tur"] == "blok" and yedek_fabrika is not None and paralel_ayarlar().get("chromium_yedek"):
            # Brave engellendi → görünür Chromium yedeği. Aynı iş parçacığında ikinci sync_playwright açılamaz:
            # önce Brave Playwright'ı mutlaka kapat.
            try:
                t_is.brave_birak()
            except Exception:
                pass
            yedek_t = None
            try:
                yedek_t = yedek_fabrika()
                r2 = _ara_calistir(yedek_t)
                if r2["tur"] in ("ok", "bos"):
                    r = r2
                    yedek_adi = "chromium_gorunur"
            except Exception:
                pass                       # yedek de düştüyse ilk (blok) sonuç korunur
            finally:
                if yedek_t is not None:
                    try:
                        yedek_t.kapat()
                    except Exception:
                        pass
        if r["tur"] == "blok" and yerel_fabrika is not None and paralel_ayarlar().get("yerel_profil_yedek"):
            # Son çare: kullanıcının yerel Brave profilinin klonu (tarayici/yerel_profil.py)
            try:
                t_is.brave_birak()
            except Exception:
                pass
            yerel_t = None
            try:
                yerel_t = yerel_fabrika()
                r3 = _ara_calistir(yerel_t)
                if r3["tur"] in ("ok", "bos"):
                    r = r3
                    yedek_adi = "yerel_brave"
            except Exception:
                pass
            finally:
                if yerel_t is not None:
                    try:
                        yerel_t.kapat()
                    except Exception:
                        pass
        bulunan, site_elenen, site_elenen_ornek = r["bulunan"], r["elenen"], r["ornek"]
        yontem, hata, tur = r["yontem"], r["hata"], r["tur"]
        if yedek_adi:
            yontem = yedek_adi

        sure = round(time.monotonic() - t0, 2)
        sayi = len(bulunan)
        katman = _KATMAN_HARITASI.get((yontem or "http").lower(), Katman.K1 if yedek_adi else Katman.K0)
        oto = getattr(t_is, "oto_hiz", None)
        if oto is not None:                  # öğrenen bekleme: siteden gelen süre/engel geri bildirimi (S2)
            try:
                n1, u1 = oto.sayac(site) if hasattr(oto, "sayac") else oto_bas
                n0, u0 = oto_bas
                istek_sayisi = (n1 - n0) or max(1, len(kurallar.get("sorgular") or [1]))
                net = max(0.0, sure - (u1 - u0))           # kendi uykumuz yanıt süresi sayılmaz
                oto.yanit(site, net / istek_sayisi, durum=None if tur == "hata" else 200, engel=(tur == "blok"))
            except Exception:
                pass

        with _kilit:
            sonuc.kapsam[(urun_id, site)] = {
                "sayi": sayi,
                "adet": sayi,
                "elenen": site_elenen,
                "elenen_ornekleri": site_elenen_ornek,
                "yontem": yontem,
                "katman": str(katman),
                "hata": hata,
                "tur": tur,
                "sure": sure,
            }

        depo.tarama_kaydet(
            hedef_v,
            site=site,
            urun_id=urun_id,
            sayi=sayi,
            elenen_sayisi=site_elenen,
            yontem=yontem,
            katman=katman,
            hata=hata,
            sure=sure,
            tur=tur,
            zaman=zaman,
        )

        olay_veri = {
            "tur": "tarama_site_bitti",
            "site": site,
            "urun_id": urun_id,
            "adet": sayi,
            "elenen": site_elenen,
            "sure": sure,
            "katman": str(katman),
            "hata": hata,
        }
        if yedek_adi:
            olay_veri["yedek"] = yedek_adi
        from .. import gunluk
        _tar = {"K0": "HTTP (K0)", "K1": "Chromium başsız (K1)", "K2": "Brave (kazı)", "K3": "Brave (doğrulama)"}.get(
            str(katman), str(katman))
        if yedek_adi:
            _tar = f"{yedek_adi} (yedek)"
        gunluk.yaz("hata" if tur == "hata" else ("uyari" if tur in ("blok", "bos") or hata else "bilgi"), "tarama",
                   f"{urun_id}: {sayi} teklif" + (f", {site_elenen} elendi" if site_elenen else "")
                   + (f" — {hata}" if hata else ""),
                   olay=f"site_bitti:{tur}", site=site, tarayici=_tar, adet=sayi, sure_sn=sure,
                   veri={"urun_id": urun_id, "yontem": yontem, "tur": tur, "elenen_ornekleri": site_elenen_ornek})
        depo.olay_kaydet(
            hedef_v,
            "tarama_site_bitti",
            urun_id=urun_id,
            mesaj=f"{site}: {sayi} teklif ({sure}s, {katman})",
            veri=olay_veri,
            zaman=zaman,
        )
        if etkin_bildirim is not None:
            try:
                etkin_bildirim(olay_veri)
            except Exception:
                pass

        # Paralel işçi bağlantısını ve sekmelerini tarama boyunca tutar (her sitede yeniden bağlanmak saniyeler yiyordu);
        # havuz sonunda kaynak_kapat kapatır. Sıralı yolda eskisi gibi her siteden sonra bırakılır (başka süreç Brave'i alabilsin).
        if getattr(t_is, "brave_acik", False) and not getattr(t_is, "kilit_paylasimli", False):
            try:
                t_is.brave_birak()
            except Exception:
                pass

        return bulunan

    try:
        hazir: list[tuple] = []
        for urun in urunler:
            urun_id = urun.id or ""
            kurallar = _kurallari_cikar(urun)
            urun_siteleri = kurallar.get("siteler")
            uygun_siteler = list(hedef_siteler)
            if isinstance(urun_siteleri, list):
                uygun_siteler = [s for s in hedef_siteler if s in urun_siteleri]
            # 15.11: HTTP turunda başarısız olan ürün×site işleri Tarayıcı turunda da taranır
            uygun_siteler += sorted(s_ for u_, s_ in ek_ciftler if u_ == urun_id and s_ not in uygun_siteler)

            # Eşleştirme sırasında site adı kontrolü arama kaynağını kısıtlamasın
            # (uygun_siteler zaten süzüldü; toplayıcı kartlarında site/kaynak değişebilir).
            eslesme_kurallari = {k: val for k, val in kurallar.items() if k != "siteler"}

            sonuc.elenen_sayisi.setdefault(urun_id, 0)
            sonuc.elenen_ornekleri.setdefault(urun_id, [])
            sonuc.elenen_gerekceleri.setdefault(urun_id, [])

            def _ele_kaydet(site_adi: str, k_nesne: Kart, gerekce: str, urun_id: str = urun_id) -> None:
                with _kilit:
                    sonuc.elenen_sayisi[urun_id] = sonuc.elenen_sayisi.get(urun_id, 0) + 1
                    if len(sonuc.elenen_gerekceleri[urun_id]) < AZAMI_ELENEN_ORNEK:
                        sonuc.elenen_gerekceleri[urun_id].append(gerekce)
                    if len(sonuc.elenen_ornekleri[urun_id]) < AZAMI_ELENEN_ORNEK:
                        sonuc.elenen_ornekleri[urun_id].append(
                            {
                                "site": site_adi,
                                "baslik": k_nesne.baslik,
                                "fiyat": k_nesne.sepet_fiyati,
                                "gerekce": gerekce,
                            }
                        )

            hazir.append((urun, urun_id, kurallar, eslesme_kurallari, uygun_siteler, _ele_kaydet))

        bulunanlar: dict[str, list[Kart]] = {h[1]: [] for h in hazir}
        isler = [(site, h) for h in hazir for site in h[4]]
        if sekme_sayisi and sekme_sayisi > 1 and tarayici_fabrika is not None and len(isler) > 1:
            from . import havuz
            # İki havuz AYNI ANDA (15.11 hız): tarayıcı gerektirmeyen (K0/HTTP) siteler hafif iş parçacıklarında,
            # N sekmenin hepsi Brave sitelerinde. Bir site yine aynı anda tek işte (siteler havuzlar arasında ayrık).
            k0 = _k0_siteleri(hedef_v)
            http_isler = [x for x in isler if x[0] in k0]
            tarayici_isler = [x for x in isler if x[0] not in k0]
            def is_fn(t_is: Any, site: str, h: Any) -> list[Kart]:
                kartlar = _bir_site(t_is, h[0], h[1], site, h[2], h[3], h[5])
                if anlik_alarm_fn is not None and kartlar:
                    anlik_alarm_fn(hedef_v, h[1], kartlar)     # 15.11: alarm site biter bitmez, taramayı beklemeden
                return kartlar
            ozetler: list[Any] = []

            def _http_havuzu() -> None:
                ozetler.append(havuz.calistir(http_isler, HTTP_ISCI, kaynak_fabrika=tarayici_fabrika,
                                              is_fn=is_fn, kaynak_kapat=lambda t_is: t_is.kapat(),
                                          site_bitti=_site_penceresi_kapat))
            http_is = threading.Thread(target=_http_havuzu, name="kazi-http-havuzu", daemon=True)
            if http_isler:
                http_is.start()
            ozetler.append(havuz.calistir(tarayici_isler, sekme_sayisi, kaynak_fabrika=tarayici_fabrika,
                                          is_fn=is_fn, kaynak_kapat=lambda t_is: t_is.kapat(),
                                          site_bitti=_site_penceresi_kapat))
            if http_isler:
                http_is.join()
            for oz in ozetler:
                for r in oz.sonuclar:
                    if r.sira >= 0 and r.deger:
                        bulunanlar[r.yuk[1]].extend(r.deger)
        else:
            for site, h in isler:
                kartlar = _bir_site(t, h[0], h[1], site, h[2], h[3], h[5])
                if anlik_alarm_fn is not None and kartlar:
                    anlik_alarm_fn(hedef_v, h[1], kartlar)
                bulunanlar[h[1]].extend(kartlar)

        for urun, urun_id, *_ in hazir:
            hepsi = bulunanlar[urun_id]

            simdi_dt = datetime.now(TZ_ISTANBUL)
            for k in hepsi:
                if bayat_mi(k, simdi_dt):
                    k.durum = "bayat"
                if not k.zaman:
                    k.zaman = zaman

            tek_kartlar = tekillestir(hepsi)
            kaydedilen_teklifler: list[Teklif] = []
            for k in tek_kartlar:
                tk = k.teklife(urun_id)
                kaynaklar_str = getattr(k, "kaynaklar", None)
                tid = depo.teklif_ekle(
                    hedef_v,
                    tk,
                    kaynaklar=kaynaklar_str,
                    pesin_taksit=k.pesin_taksit,
                )
                tk.id = tid
                object.__setattr__(tk, "kaynaklar", kaynaklar_str)
                object.__setattr__(tk, "durum", k.durum)
                kaydedilen_teklifler.append(tk)

            sonuc.teklifler[urun_id] = kaydedilen_teklifler

        return sonuc
    finally:
        if t_kendi:
            try:
                t.kapat()
            except Exception:
                pass
        if v_kendi and hasattr(hedef_v, "kapat"):
            try:
                hedef_v.kapat()
            except Exception:
                pass


class _TaraModul(types.ModuleType):
    def __call__(self, *args: Any, **kwargs: Any) -> TaramaSonucu:
        return tara(*args, **kwargs)


sys.modules[__name__].__class__ = _TaraModul

