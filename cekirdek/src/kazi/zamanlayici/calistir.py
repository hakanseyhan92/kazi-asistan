"""Kategori bazlı tarama zamanlayıcısı yürütücüsü (`python -m kazi.zamanlayici`).

Özellikler:
- 15 saniyede bir uyanır; elle istekleri (10 dk boşluk kuralını atlayarak) ve zamanı gelen
  kategorileri tek kopya kilidi (`~/.local/share/kazi/zamanlayici.pid`) altında sırayla çalıştırır.
- Her kategori taraması alt süreçte (`multiprocessing` fork) süre sınırıyla çalışır:
  ürün kategorisi 25 dk, uçuş 60 dk. Süre aşılırsa süreç sonlandırılır ve `sekmeleri_temizle`
  ile Brave sekmeleri temizlenir.
- `--kuru` gerçekte ağa çıkmadan ve DB durumunu değiştirmeden planı / sıradaki adımı yazar.
- SIGTERM / SIGINT sinyallerinde etkin alt süreci kapatıp temiz çıkış yapar.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import multiprocessing as mp
import os
from pathlib import Path
import signal
import sys
import threading
import time
from typing import Any, Callable

from ..ayar import ayar_yukle, varsayilan_veri_dizini
from ..bildirim.telegram import alarm_denetle
from ..db import VeriTabani
from ..modeller import TZ_ISTANBUL, Olay
from ..seyahat import plan_depo
from ..tarama.dongu import DonguZatenCalisiyor, sekmeleri_temizle
from ..tarama.tara import tara as urun_tara
from ..tarama.teyit import kovala as teyit_kovala
from ..tarayici import Tarayici
from ..ucus import tara as ucus_tara_mod
from . import plan as plan_mod
from .plan import Kategori

VERI_DIZINI = Path.home() / ".local" / "share" / "kazi"
PID_DOSYA = VERI_DIZINI / "zamanlayici.pid"
_VARSAYILAN_PID = PID_DOSYA

URUN_SURE_SN = 25 * 60
UCUS_SURE_SN = 60 * 60
UYKU_SN = 15.0


class ZamanlayiciZatenCalisiyor(DonguZatenCalisiyor):
    """Aynı anda ikinci zamanlayıcı kopyası çalışıyor."""


def log(mesaj: str, seviye: str = "bilgi") -> None:
    zaman = datetime.now(TZ_ISTANBUL).strftime("%Y-%m-%d %H:%M:%S")
    try:
        sys.stderr.write(f"[{zaman}] [zamanlayici] {mesaj}\n")
    except Exception:
        pass
    from .. import gunluk
    if seviye == "bilgi" and any(k in mesaj.lower() for k in ("hata", "başarısız", "zaman aşımı", "öldürül", "durduruldu")):
        seviye = "uyari"
    gunluk.yaz(seviye, "zamanlayici", mesaj, olay="zamanlayici")


def kategori_sure_sn(kat: Kategori) -> float:
    return float(UCUS_SURE_SN if kat.tur == "ucus" else URUN_SURE_SN)


@contextmanager
def tek_kopya(yol: Path | str | None = None):
    """`~/.local/share/kazi/zamanlayici.pid` üzerinde fcntl kilidi; ikinci kopyayı reddeder."""
    if yol is not None:
        kilit = Path(yol).expanduser()
    elif PID_DOSYA != _VARSAYILAN_PID:
        kilit = PID_DOSYA
    elif os.environ.get("XDG_DATA_HOME") or os.environ.get("KAZI_VERI"):
        kilit = (
            Path(os.environ["KAZI_VERI"]).expanduser()
            if os.environ.get("KAZI_VERI")
            else varsayilan_veri_dizini()
        ) / "zamanlayici.pid"
    else:
        kilit = PID_DOSYA
    kilit.parent.mkdir(parents=True, exist_ok=True)
    dosya = open(kilit, "a+", encoding="utf-8")
    try:
        fcntl.flock(dosya.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        dosya.close()
        raise ZamanlayiciZatenCalisiyor(f"zamanlayıcı zaten çalışıyor: {kilit}") from e
    try:
        dosya.seek(0)
        dosya.truncate()
        dosya.write(str(os.getpid()))
        dosya.flush()
        yield kilit
    finally:
        try:
            fcntl.flock(dosya.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        dosya.close()


def _kayitli_ucus_plani() -> tuple[Any, list[str] | None]:
    try:
        d = plan_depo.yukle()
        p = plan_depo.plan_nesnesi(d)
        tum = p.bacaklar()
        if any("ucak" not in (b.araclar or ["ucak"]) for b in tum):
            ucak_ids = [b.id for b in tum if "ucak" in (b.araclar or ["ucak"]) and b.id]
            yan_ids = [
                bid
                for yt in p.yan_turlar
                for bid in (yt.gidis_bacak_id, yt.donus_bacak_id)
                if bid
            ]
            return p, [*ucak_ids, *yan_ids]
        return p, None
    except Exception:
        return None, None


def _paralel_urun_tara(v: VeriTabani, t: Any, urun_idler: list[str]) -> Any:
    """Ayarlar → 'Aynı anda sekme' (2–10): ürün×site işleri N sekmeye dağıtılır; bir site aynı anda tek sekmede.
    Brave kilidini bu iş parçacığı tarama boyunca tutar, işçiler paylaşır (başka süreç araya giremez)."""
    from ..tarayici.cekirdek import sekme_fabrikasi
    from ..tarayici.gorunur_chromium import GorunurChromium
    from ..tarayici.yerel_profil import YerelBrave, yerel_profil_var
    from ..bildirim.telegram import anlik_alarm
    from ..tarayici.kilit import brave_kilidi
    from ..tarayici.paralel_ayar import paralel_ayarlar
    from ..tarayici.cekirdek import artik_pencereleri_kapat
    sekme = paralel_ayarlar()["sekme_sayisi"]
    log(f"paralel tarama: {sekme} sekme, {len(urun_idler)} ürün")
    with brave_kilidi(900.0):
        n = artik_pencereleri_kapat()
        if n:
            log(f"önceki taramalardan kalan {n} boş pencere kapatıldı")
        from . import hizli
        # 15.11 Tarayıcı turu: yalnız tarayıcı turuna atanmış siteler + son HTTP turunda başarısız ürün×site işleri
        siteler, ek = hizli.tarayici_turu_urun(v, urun_idler)
        if siteler is not None:
            log(f"Tarayıcı turu: {len(siteler)} site" + (f" + HTTP'den kalan {len(ek)} iş" if ek else ""))
            if not siteler and not ek:
                from ..tarama.tara import TaramaSonucu
                log("Tarayıcı turunda bu kategori için site yok (hepsi HTTP turunda)")
                return TaramaSonucu(zaman=plan_mod.iso_bicim(datetime.now(TZ_ISTANBUL)),
                                    urun_idler=list(urun_idler or []), siteler=[])
        return urun_tara(v, t, urun_idler=urun_idler, sekme_sayisi=sekme,
                         siteler=siteler, ek_ciftler=ek,
                         tarayici_fabrika=sekme_fabrikasi(v, getattr(t, "bildirim_fn", None)),
                         yedek_fabrika=lambda: GorunurChromium(veritabani=v),
                         yerel_fabrika=(lambda: YerelBrave(veritabani=v)) if yerel_profil_var() else None,
                         anlik_alarm_fn=anlik_alarm)


def _paralel_ucus_tara(v: VeriTabani, t: Any, ucus_fn: Callable[..., Any], kw: dict[str, Any]) -> Any:
    """Uçuş taraması: Ayarlar → 'Aynı anda sekme' > 1 ise bacak×tarih×kaynak işleri N sekmeye dağıtılır (bir kaynak aynı
    anda tek pencerede); Brave kilidi tarama boyunca tutulur. Sekme sayısı 1 ise eski sıralı yol (kilidi `t` kendisi alır)."""
    from ..tarayici.cekirdek import artik_pencereleri_kapat, sekme_fabrikasi
    from ..tarayici.kilit import brave_kilidi
    from ..tarayici.paralel_ayar import paralel_ayarlar
    sekme = int(paralel_ayarlar()["sekme_sayisi"])
    if sekme <= 1:
        return ucus_fn(v, t, **kw)
    log(f"paralel uçuş taraması: {sekme} sekme")
    if getattr(t, "brave_acik", False) and not getattr(t, "kilit_paylasimli", False):
        t.brave_birak()                 # ana tarayıcı kilidi tutuyorsa havuz alamaz
    with brave_kilidi(900.0):
        n = artik_pencereleri_kapat()
        if n:
            log(f"önceki taramalardan kalan {n} boş pencere kapatıldı")
        return ucus_fn(v, t, sekme_sayisi=sekme,
                       tarayici_fabrika=sekme_fabrikasi(v, getattr(t, "bildirim_fn", None)), **kw)


def kategori_tara_calistir(
    v: VeriTabani,
    kat: Kategori,
    *,
    tarayici: Any = None,
    tara_fn: Callable[..., Any] | None = None,
    kovala_fn: Callable[..., Any] | None = None,
    ucus_tara_fn: Callable[..., Any] | None = None,
    alarm_fn: Callable[..., Any] | None = None,
    simdi_fn: Callable[[], datetime] = lambda: datetime.now(TZ_ISTANBUL),
) -> dict[str, Any]:
    """Tek bir kategoriyi tara, DB/alarm/olay güncellemelerini yap ve özet döndür."""
    plan_mod.kur(v)
    t0 = time.monotonic()
    teklif_sayisi = 0
    hata_sayisi = 0

    t_kendi = tarayici is None
    t = tarayici if tarayici is not None else Tarayici(veritabani=v)
    etkin_tara = tara_fn or urun_tara
    etkin_kovala = kovala_fn or teyit_kovala
    etkin_ucus = ucus_tara_fn or ucus_tara_mod.tara
    etkin_alarm = alarm_fn or alarm_denetle

    try:
        if kat.tur == "ucus":
            kayitli_plan, bacak_idler = _kayitli_ucus_plani()
            ref = simdi_fn()
            rotasyon = ref.timetuple().tm_yday * 8 + ref.hour // 3
            kw: dict[str, Any] = {
                "plan": kayitli_plan,
                "azami_tarih": 1,
                "rotasyon": rotasyon,
            }
            if bacak_idler is not None:
                kw["bacak_idler"] = bacak_idler
            if ucus_tara_fn is None:       # gerçek tarama: yerel profil son çaresi (testlerin sahte fonksiyonuna verilmez)
                from ..tarayici.yerel_profil import YerelBrave, yerel_profil_var
                if yerel_profil_var():
                    kw["yerel_fabrika"] = lambda: YerelBrave(veritabani=v)
            if ucus_tara_fn is None:
                from . import hizli
                kaynaklar_t = hizli.tarayici_turu_ucus(v)       # None → HTTP turu kapalı, hepsi
                if kaynaklar_t is not None:
                    kw["kaynaklar"] = kaynaklar_t
                if kaynaklar_t == []:
                    from types import SimpleNamespace
                    log("Tarayıcı turunda uçuş kaynağı yok (hepsi HTTP turunda)")
                    usonuc = SimpleNamespace(segmentler={}, kapsam={}, hatalar=[])
                else:
                    usonuc = _paralel_ucus_tara(v, t, etkin_ucus, kw)
            else:
                usonuc = etkin_ucus(v, t, **kw)
            teklif_sayisi = sum(
                len(segs) for segs in (getattr(usonuc, "segmentler", None) or {}).values()
            )
            hata_sayisi = len(getattr(usonuc, "hatalar", None) or []) + sum(
                1
                for k in (getattr(usonuc, "kapsam", None) or {}).values()
                if isinstance(k, dict) and k.get("hata")
            )
        else:
            if kat.urunler:
                if tara_fn is None:
                    sonuc = _paralel_urun_tara(v, t, list(kat.urunler))
                else:
                    sonuc = etkin_tara(v, t, urun_idler=list(kat.urunler))
                teklif_sayisi = sum(
                    len(lst) for lst in (getattr(sonuc, "teklifler", None) or {}).values()
                )
                hata_sayisi = sum(
                    1
                    for k in (getattr(sonuc, "kapsam", None) or {}).values()
                    if isinstance(k, dict) and k.get("hata")
                )
                for uid in kat.urunler:
                    try:
                        etkin_kovala(v, t, uid, azami=3)
                    except Exception as exc:
                        hata_sayisi += 1
                        log(f"{kat.id} teyit hatası ({uid}): {type(exc).__name__}: {exc}")
                if hasattr(t, "brave_birak"):
                    try:
                        t.brave_birak()
                    except Exception:
                        pass
    except Exception as exc:
        hata_sayisi += 1
        log(f"{kat.id} tarama hatası: {type(exc).__name__}: {exc}")
    finally:
        if t_kendi and hasattr(t, "kapat"):
            try:
                t.kapat()
            except Exception:
                pass
        if t_kendi and tara_fn is None:   # boşta boş Brave penceresi kalmasın (15.11, kullanıcı); yalnız gerçek (sahte olmayan) taramada
            from ..tarayici.cekirdek import brave_kapat
            if brave_kapat():
                log("tarama bitti: kazı Brave'i kapatıldı")

    sure_sn = max(0, round(time.monotonic() - t0))
    bitis_dt = simdi_fn()
    bitis_iso = plan_mod.iso_bicim(bitis_dt) or datetime.now(TZ_ISTANBUL).isoformat(timespec="seconds")
    ozet = {"teklif": int(teklif_sayisi), "hata": int(hata_sayisi), "sure_sn": int(sure_sn)}

    plan_mod.tarama_bitti(v, kat.id, bitis_iso, ozet)

    try:
        etkin_alarm(v)
    except Exception as exc:
        log(f"{kat.id} alarm denetimi hatası: {type(exc).__name__}: {exc}")

    v.olay_ekle(
        Olay(
            tur="tarama_bitti",
            zaman=bitis_iso,
            mesaj=f"{kat.ad}: {teklif_sayisi} teklif, {hata_sayisi} hata ({sure_sn}s)",
            veri={
                "kategori": kat.id,
                "teklif": int(teklif_sayisi),
                "hata": int(hata_sayisi),
                "sure_sn": int(sure_sn),
            },
        )
    )
    log(f"bitti: {kat.id} — {ozet}")
    return ozet


def kategori_grubu_tara_calistir(
    v: VeriTabani,
    kats: list[Kategori],
    *,
    tarayici: Any = None,
    tara_fn: Callable[..., Any] | None = None,
    kovala_fn: Callable[..., Any] | None = None,
    alarm_fn: Callable[..., Any] | None = None,
    simdi_fn: Callable[[], datetime] = lambda: datetime.now(TZ_ISTANBUL),
) -> dict[str, dict[str, Any]]:
    """Birden çok ÜRÜN kategorisini TEK havuz taramasıyla birlikte tara ("aynı anda kategori").

    Ürün listeleri birleştirilir (aynı site aynı anda yine tek sekmede); sonuç her kategoriye kendi
    ürünlerine göre bölünür ve her kategori için ayrı `tarama_bitti` + olay yazılır."""
    plan_mod.kur(v)
    t0 = time.monotonic()
    t_kendi = tarayici is None
    t = tarayici if tarayici is not None else Tarayici(veritabani=v)
    etkin_tara = tara_fn or urun_tara
    etkin_kovala = kovala_fn or teyit_kovala
    etkin_alarm = alarm_fn or alarm_denetle

    teklif: dict[str, int] = {k.id: 0 for k in kats}
    hata: dict[str, int] = {k.id: 0 for k in kats}
    tum_urunler: list[str] = []
    for k in kats:
        for uid in k.urunler:
            if uid not in tum_urunler:
                tum_urunler.append(uid)

    try:
        try:
            if tara_fn is None:
                sonuc = _paralel_urun_tara(v, t, list(tum_urunler))
            else:
                sonuc = etkin_tara(v, t, urun_idler=list(tum_urunler))
            teklifler = getattr(sonuc, "teklifler", None) or {}
            kapsam = getattr(sonuc, "kapsam", None) or {}
            for k in kats:
                uidler = set(k.urunler)
                teklif[k.id] = sum(len(lst) for uid, lst in teklifler.items() if uid in uidler)
                hata[k.id] = sum(
                    1
                    for anahtar, kp in kapsam.items()
                    if isinstance(kp, dict) and kp.get("hata")
                    and isinstance(anahtar, tuple) and anahtar and anahtar[0] in uidler
                )
        except Exception as exc:
            for k in kats:
                hata[k.id] += 1
            log(f"{'+'.join(k.id for k in kats)} grup tarama hatası: {type(exc).__name__}: {exc}")
        for k in kats:
            for uid in k.urunler:
                try:
                    etkin_kovala(v, t, uid, azami=3)
                except Exception as exc:
                    hata[k.id] += 1
                    log(f"{k.id} teyit hatası ({uid}): {type(exc).__name__}: {exc}")
        if hasattr(t, "brave_birak"):
            try:
                t.brave_birak()
            except Exception:
                pass
    finally:
        if t_kendi and hasattr(t, "kapat"):
            try:
                t.kapat()
            except Exception:
                pass
        if t_kendi and tara_fn is None:   # boşta boş Brave penceresi kalmasın (15.11, kullanıcı); yalnız gerçek (sahte olmayan) taramada
            from ..tarayici.cekirdek import brave_kapat
            if brave_kapat():
                log("tarama bitti: kazı Brave'i kapatıldı")

    sure_sn = max(0, round(time.monotonic() - t0))
    bitis_dt = simdi_fn()
    bitis_iso = plan_mod.iso_bicim(bitis_dt) or datetime.now(TZ_ISTANBUL).isoformat(timespec="seconds")
    ozetler: dict[str, dict[str, Any]] = {}
    for k in kats:
        ozet = {"teklif": int(teklif[k.id]), "hata": int(hata[k.id]), "sure_sn": int(sure_sn)}
        ozetler[k.id] = ozet
        plan_mod.tarama_bitti(v, k.id, bitis_iso, ozet)
    try:
        etkin_alarm(v)
    except Exception as exc:
        log(f"grup alarm denetimi hatası: {type(exc).__name__}: {exc}")
    for k in kats:
        ozet = ozetler[k.id]
        v.olay_ekle(
            Olay(
                tur="tarama_bitti",
                zaman=bitis_iso,
                mesaj=f"{k.ad}: {ozet['teklif']} teklif, {ozet['hata']} hata ({sure_sn}s)",
                veri={"kategori": k.id, **ozet},
            )
        )
        log(f"bitti: {k.id} — {ozet} (grup)")
    return ozetler


def _alt_kategori_tara(db_yolu: Path, kat: Kategori | list[Kategori]) -> None:
    from .. import gunluk
    gunluk.kategori_ayarla(",".join(k.id for k in kat) if isinstance(kat, list) else kat.id)
    gunluk.python_logu_bagla()
    v = VeriTabani(db_yolu)
    try:
        if isinstance(kat, list):
            kategori_grubu_tara_calistir(v, kat)
        else:
            kategori_tara_calistir(v, kat)
    finally:
        v.kapat()


def bekcili_kategori_tara(
    db_yolu: Path,
    kat: Kategori | list[Kategori],
    *,
    sure_sn: float | None = None,
    hedef: Callable[..., Any] | None = None,
    temizle: Callable[[], int] | None = sekmeleri_temizle,
    dur_event: threading.Event | None = None,
) -> bool:
    """Kategori taramasını alt süreçte süre sınırıyla çalıştır. `True` = zamanında bitti."""
    kat_id = "+".join(k.id for k in kat) if isinstance(kat, list) else kat.id
    etkin_sure = (
        max(kategori_sure_sn(k) for k in kat) if isinstance(kat, list) else kategori_sure_sn(kat)
    ) if sure_sn is None else float(sure_sn)
    ctx = mp.get_context("fork")
    pr = ctx.Process(
        target=hedef or _alt_kategori_tara,
        args=(Path(db_yolu), kat),
        daemon=True,
    )
    pr.start()

    bitis_mono = time.monotonic() + max(0.0, etkin_sure)
    zaman_asimi = False
    while pr.is_alive():
        if dur_event is not None and dur_event.is_set():
            break
        kalan = bitis_mono - time.monotonic()
        if kalan <= 0:
            zaman_asimi = True
            break
        pr.join(min(0.5, kalan))

    if not pr.is_alive():
        return pr.exitcode == 0

    pr.terminate()
    pr.join(2)
    if pr.is_alive():
        pr.kill()
        pr.join(2)

    kapanan = 0
    if temizle is not None:
        try:
            kapanan = int(temizle())
        except Exception as exc:
            log(f"sekme temizliği başarısız: {type(exc).__name__}: {exc}")
    if temizle is sekmeleri_temizle:
        # 15.11 canlı: flatpak Brave servis grubunun dışında çalışıyor; alt süreç öldürülünce kazı Brave'i açık kalıyordu
        try:
            from ..tarayici.cekirdek import brave_kapat
            if brave_kapat():
                log("durdurulan taramanın kazı Brave'i kapatıldı")
        except Exception as exc:                    # noqa: BLE001
            log(f"kazı Brave'i kapatılamadı: {type(exc).__name__}: {exc}")

    neden = "durdurma sinyali" if (dur_event is not None and dur_event.is_set() and not zaman_asimi) else f"{int(etkin_sure // 60)} dk süre sınırı aşıldı"
    log(f"⏱ {kat_id} alt süreci durduruldu ({neden}, {kapanan} sekme kapatıldı)")
    return False


def _sonlandirma_kaydet(
    v: VeriTabani,
    kat: Kategori,
    baslangic_dt: datetime,
    simdi: datetime,
    sure_sn: int,
) -> None:
    """Alt süreç zaman aşımı/çökme durumunda `tarama_kategori` ve `olay` kaydını tamamla."""
    durum = plan_mod.durum_oku(v).get(kat.id) or {}
    son_bit = plan_mod.iso_coz(durum.get("son_bitis"))
    if son_bit is not None and son_bit >= plan_mod._tz_li(baslangic_dt):
        return
    bitis_iso = plan_mod.iso_bicim(simdi) or datetime.now(TZ_ISTANBUL).isoformat(timespec="seconds")
    ozet = {"teklif": 0, "hata": 1, "sure_sn": int(sure_sn)}
    plan_mod.tarama_bitti(v, kat.id, bitis_iso, ozet)
    v.olay_ekle(
        Olay(
            tur="tarama_bitti",
            zaman=bitis_iso,
            mesaj=f"{kat.ad}: tarama tamamlanamadı ({sure_sn}s)",
            veri={"kategori": kat.id, **ozet},
        )
    )


def yetim_taramalari_kapat(db_yolu: Path, simdi: datetime | None = None) -> list[str]:
    """Zamanlayıcı açılırken (tek kopya kilidi alınmışken) yarım kalmış tarama kayıtlarını kapat.

    15.11 canlı: servis tarama ortasında yeniden başlatılınca bitiş hiç yazılmıyor, kategori 90 dk boyunca
    "taranıyor" görünüyor ve sırası gelmiyordu. Bu anda başka tarama süreci olamaz (tek kopya), o yüzden güvenli."""
    simdi = simdi or datetime.now(TZ_ISTANBUL)
    v = VeriTabani(db_yolu)
    kapatilan: list[str] = []
    try:
        plan_mod.kur(v)
        bitis_iso = plan_mod.iso_bicim(simdi) or simdi.isoformat(timespec="seconds")
        for kid, d in plan_mod.durum_oku(v).items():
            bas = plan_mod.iso_coz(d.get("son_baslangic"))
            bit = plan_mod.iso_coz(d.get("son_bitis"))
            if bas is None or (bit is not None and bit >= bas):
                continue
            sure = max(0, int((simdi - bas).total_seconds()))
            ozet = {"teklif": 0, "hata": 1, "sure_sn": sure, "kesildi": True}
            plan_mod.tarama_bitti(v, kid, bitis_iso, ozet)
            v.olay_ekle(Olay(tur="tarama_bitti", zaman=bitis_iso,
                             mesaj=f"{plan_mod.kategori_adi(kid)}: tarama yarıda kesildi (zamanlayıcı yeniden başladı)",
                             veri={"kategori": kid, **ozet}))
            kapatilan.append(kid)
    finally:
        v.kapat()
    return kapatilan


def bir_adim(
    db_yolu: Path,
    *,
    kuru: bool = False,
    alt_surec: bool = True,
    simdi_fn: Callable[[], datetime] = lambda: datetime.now(TZ_ISTANBUL),
    dur_event: threading.Event | None = None,
    tara_fn: Callable[..., Any] | None = None,
    kovala_fn: Callable[..., Any] | None = None,
    ucus_tara_fn: Callable[..., Any] | None = None,
    alarm_fn: Callable[..., Any] | None = None,
    tarayici: Any = None,
    sure_sn: float | None = None,
    temizle: Callable[[], int] | None = sekmeleri_temizle,
    kategori_esz: int | None = None,
) -> dict[str, Any] | None:
    """Tek kontrol adımı: sıradaki kategori(ler) varsa çalıştır (veya `--kuru` ile raporla).

    `kategori_esz` (1–3; verilmezse ayardan) > 1 ise vadesi dolmuş ürün kategorileri BİRLİKTE taranır."""
    if kategori_esz is None:
        from ..tarayici.paralel_ayar import paralel_ayarlar
        kategori_esz = paralel_ayarlar()["kategori_esz"]
    v = VeriTabani(db_yolu)
    try:
        kats = plan_mod.kategoriler(v)
        durum = plan_mod.durum_oku(v)
        ayar = plan_mod.kategori_aralik_oku()
        simdi = simdi_fn()
        _, kuyruk = plan_mod.aktif_durum_coz(durum, simdi=simdi)
        hedef_idler = plan_mod.siradakiler(
            kats, durum, ayar, simdi, calisan=None, kuyruk=kuyruk, azami=kategori_esz
        )
        plan_ozet = plan_mod.plan_hesapla(
            kats, durum, ayar, simdi, calisan=None, kuyruk=kuyruk, azami=kategori_esz
        )
        from ..tarayici.paralel_ayar import paralel_ayarlar as _pa
        if not _pa().get("tarayici_otomatik", True):
            # 15.11: otomatik tarayıcı (Brave) taramaları kapalı — yalnız hızlı HTTP turu döner; elle "Şimdi tara" çalışır
            hedef_idler = [h for h in hedef_idler if h in kuyruk]
        if not hedef_idler:
            if kuru:
                return {"kategori": None, "kuru": True, "plan": plan_ozet}
            return None
        hedefler = [next(k for k in kats if k.id == h) for h in hedef_idler]
        kat = hedefler[0]

        if kuru:
            rapor = {
                "kategori": kat.id,
                "ad": kat.ad,
                "tur": kat.tur,
                "urunler": list(kat.urunler),
                "kuru": True,
                "plan": plan_ozet,
            }
            if len(hedefler) > 1:
                rapor["kategoriler"] = [k.id for k in hedefler]
            return rapor

        baslangic_iso = plan_mod.iso_bicim(simdi) or datetime.now(TZ_ISTANBUL).isoformat(timespec="seconds")
        for k in hedefler:
            plan_mod.tarama_basladi(v, k.id, baslangic_iso)
    finally:
        v.kapat()

    if len(hedefler) > 1:
        return _grup_adimi(
            db_yolu, hedefler, simdi, alt_surec=alt_surec, simdi_fn=simdi_fn, dur_event=dur_event,
            tara_fn=tara_fn, kovala_fn=kovala_fn, alarm_fn=alarm_fn, tarayici=tarayici,
            sure_sn=sure_sn, temizle=temizle,
        )

    log(f"başladı: {kat.id} ({kat.ad}, tur={kat.tur}, {len(kat.urunler)} ürün)")
    t0 = time.monotonic()

    if alt_surec and tara_fn is None and kovala_fn is None and ucus_tara_fn is None and tarayici is None:
        ok = bekcili_kategori_tara(
            db_yolu,
            kat,
            sure_sn=sure_sn,
            temizle=temizle,
            dur_event=dur_event,
        )
        v2 = VeriTabani(db_yolu)
        try:
            if not ok:
                _sonlandirma_kaydet(v2, kat, simdi, simdi_fn(), max(0, round(time.monotonic() - t0)))
            son_durum = plan_mod.durum_oku(v2).get(kat.id) or {}
        finally:
            v2.kapat()
        return {"kategori": kat.id, "ok": ok, "sonuc": son_durum.get("son_sonuc")}

    v2 = VeriTabani(db_yolu)
    try:
        ozet = kategori_tara_calistir(
            v2,
            kat,
            tarayici=tarayici,
            tara_fn=tara_fn,
            kovala_fn=kovala_fn,
            ucus_tara_fn=ucus_tara_fn,
            alarm_fn=alarm_fn,
            simdi_fn=simdi_fn,
        )
        return {"kategori": kat.id, "ok": True, "sonuc": ozet}
    finally:
        v2.kapat()


def _grup_adimi(
    db_yolu: Path,
    hedefler: list[Kategori],
    simdi: datetime,
    *,
    alt_surec: bool,
    simdi_fn: Callable[[], datetime],
    dur_event: threading.Event | None,
    tara_fn: Callable[..., Any] | None,
    kovala_fn: Callable[..., Any] | None,
    alarm_fn: Callable[..., Any] | None,
    tarayici: Any,
    sure_sn: float | None,
    temizle: Callable[[], int] | None,
) -> dict[str, Any]:
    """Birlikte başlayan ürün kategorilerini tek taramada çalıştır; sonuçları kategori başına döndür."""
    idler = [k.id for k in hedefler]
    log(f"başladı (grup): {'+'.join(idler)} ({sum(len(k.urunler) for k in hedefler)} ürün)")
    t0 = time.monotonic()

    if alt_surec and tara_fn is None and kovala_fn is None and tarayici is None:
        ok = bekcili_kategori_tara(
            db_yolu, hedefler, sure_sn=sure_sn, temizle=temizle, dur_event=dur_event
        )
        v2 = VeriTabani(db_yolu)
        try:
            if not ok:
                sure = max(0, round(time.monotonic() - t0))
                for k in hedefler:
                    _sonlandirma_kaydet(v2, k, simdi, simdi_fn(), sure)
            durum = plan_mod.durum_oku(v2)
        finally:
            v2.kapat()
        sonuclar = {k.id: (durum.get(k.id) or {}).get("son_sonuc") for k in hedefler}
        return {"kategori": idler[0], "kategoriler": idler, "ok": ok,
                "sonuc": sonuclar[idler[0]], "sonuclar": sonuclar}

    v2 = VeriTabani(db_yolu)
    try:
        ozetler = kategori_grubu_tara_calistir(
            v2, hedefler, tarayici=tarayici, tara_fn=tara_fn, kovala_fn=kovala_fn,
            alarm_fn=alarm_fn, simdi_fn=simdi_fn,
        )
    finally:
        v2.kapat()
    return {"kategori": idler[0], "kategoriler": idler, "ok": True,
            "sonuc": ozetler[idler[0]], "sonuclar": ozetler}


def _kuru_yazdir(rapor: dict[str, Any]) -> None:
    plan_ozet = rapor.get("plan") or {}
    if rapor.get("kategoriler"):
        print(f"[KURU] Birlikte taranacak kategoriler: {', '.join(rapor['kategoriler'])}")
    elif rapor.get("kategori"):
        if rapor.get("tur") == "ucus":
            print(f"[KURU] Sıradaki tarama: {rapor['kategori']} ({rapor['ad']}) — uçuş planı")
        else:
            print(
                f"[KURU] Sıradaki tarama: {rapor['kategori']} ({rapor['ad']}) — "
                f"{len(rapor.get('urunler') or [])} ürün: {rapor.get('urunler')}"
            )
    else:
        print("[KURU] Şu an başlatılacak kategori yok (bekleme / 10 dk boşluk sürüyor).")
    for k in plan_ozet.get("kategoriler") or []:
        print(
            f"  - {k['id']} ({k['ad']}): aralık={k['aralik_dk']}dk, durum={k['durum']}, "
            f"sonraki={k['sonraki']}, kalan={k['kalan_sn']}sn"
        )


def _rapor_adimi(db_yolu: Path) -> None:
    """Telegram düzenli özeti (zamanı geldiyse). Hata döngüyü durdurmaz."""
    try:
        from ..bildirim.rapor import rapor_gonder_gerekirse
        from ..db import VeriTabani
        v = VeriTabani(db_yolu)
        try:
            if rapor_gonder_gerekirse(v):
                log("Telegram raporu gönderildi")
        finally:
            v.kapat()
    except Exception as exc:
        log(f"rapor hatası: {type(exc).__name__}: {exc}")


def dongu(
    db_yolu: Path,
    *,
    bekle: float = UYKU_SN,
    kuru: bool = False,
    dur_event: threading.Event | None = None,
    simdi_fn: Callable[[], datetime] = lambda: datetime.now(TZ_ISTANBUL),
    uyu: Callable[[float], Any] | None = None,
    bir_adim_fn: Callable[..., Any] | None = None,
) -> None:
    """15 sn'de bir uyanan ana zamanlayıcı döngüsü; `dur_event` ile temiz kapanır."""
    etkin_dur = dur_event or threading.Event()
    adim_fn = bir_adim_fn or bir_adim
    while not etkin_dur.is_set():
        try:
            sonuc = adim_fn(db_yolu, kuru=kuru, simdi_fn=simdi_fn, dur_event=etkin_dur)
            if kuru and sonuc is not None:
                _kuru_yazdir(sonuc)
        except Exception as exc:
            log(f"döngü adımı hatası: {type(exc).__name__}: {exc}")
        if not kuru:
            _rapor_adimi(db_yolu)

        if etkin_dur.is_set():
            break
        bekleme = max(0.05, float(bekle))
        if uyu is not None:
            uyu(bekleme)
        else:
            etkin_dur.wait(timeout=bekleme)


def calistir(
    *,
    bir_kez: bool = False,
    kuru: bool = False,
    pid_yolu: Path | str | None = None,
    db_yolu: Path | str | None = None,
    bekle: float = UYKU_SN,
) -> int:
    """CLI girişi: tek kopya kilidi + SIGTERM yakalama + tek tur veya sonsuz döngü."""
    hedef_db = Path(db_yolu) if db_yolu else ayar_yukle().db_yolu
    dur_event = threading.Event()

    def _sinyal_yakala(_signum: int, _frame: Any) -> None:
        dur_event.set()

    onceki_sigterm = None
    onceki_sigint = None
    if threading.current_thread() is threading.main_thread():
        onceki_sigterm = signal.signal(signal.SIGTERM, _sinyal_yakala)
        onceki_sigint = signal.signal(signal.SIGINT, _sinyal_yakala)

    try:
        with tek_kopya(pid_yolu):
            if bir_kez:
                sonuc = bir_adim(hedef_db, kuru=kuru, dur_event=dur_event)
                if kuru and sonuc is not None:
                    _kuru_yazdir(sonuc)
                elif sonuc is not None:
                    print(f"tamamlandı: {sonuc['kategori']} · {sonuc.get('sonuc')}")
                else:
                    print("şu an zamanı gelen kategori yok")
                return 0

            try:
                kapatilan = yetim_taramalari_kapat(hedef_db)
                if kapatilan:
                    log(f"yarım kalmış tarama kaydı kapatıldı: {', '.join(kapatilan)}")
            except Exception as exc:                       # noqa: BLE001
                log(f"yarım tarama denetimi hatası: {type(exc).__name__}: {exc}")
            log("zamanlayıcı döngüsü başladı")
            if not kuru:          # 15.11: hızlı HTTP turu ayrı iş parçacığında (Brave taramalarını beklemez/bekletmez)
                from . import hizli
                threading.Thread(target=hizli.dongu, args=(hedef_db, dur_event), kwargs={"log": log},
                                 name="kazi-hizli-http", daemon=True).start()
            dongu(hedef_db, bekle=bekle, kuru=kuru, dur_event=dur_event)
            log("zamanlayıcı temiz kapatıldı")
            return 0
    except DonguZatenCalisiyor as e:
        log(str(e))
        print(str(e))
        return 3
    except KeyboardInterrupt:
        log("zamanlayıcı kapatıldı (Ctrl-C)")
        return 0
    finally:
        if threading.current_thread() is threading.main_thread():
            if onceki_sigterm is not None:
                signal.signal(signal.SIGTERM, onceki_sigterm)
            if onceki_sigint is not None:
                signal.signal(signal.SIGINT, onceki_sigint)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kazi.zamanlayici")
    parser.add_argument("--bir-kez", action="store_true", help="Tek kontrol/tarama adımı çalıştır ve çık")
    parser.add_argument("--kuru", action="store_true", help="Ağa çıkmadan ve tarama yapmadan planı yaz")
    parser.add_argument("--pid", type=Path, default=None, help="Özel PID kilit dosyası yolu")
    parser.add_argument("--db", type=Path, default=None, help="Özel SQLite veritabanı yolu")
    ns = parser.parse_args(argv)
    from .. import gunluk
    gunluk.python_logu_bagla()
    silinen = gunluk.temizle()
    if silinen:
        log(f"kayıt defteri: {gunluk.SAKLAMA_GUN} günden eski {silinen} kayıt silindi")
    return calistir(bir_kez=ns.bir_kez, kuru=ns.kuru, pid_yolu=ns.pid, db_yolu=ns.db)


if __name__ == "__main__":
    raise SystemExit(main())
