"""Arka plan tarama döngüsü ve bekçi (`python -m kazi.tarama dongu`).

Zamanlama:
- Her 30 dk (:05 ve :35): `"rtx"` — RTX 5080 tüm siteler + `kovala`.
- Her 3 saatte (:20): `"hizli"` — tüm ürünler yalnız K0/HTTP siteleri.
- Her 3 saatte (:50, 03:50 hariç): `"ucus"` — tüm bacaklar × uçuş kaynakları (ana/rotasyon tarihi).
- Her gün 02:50: `"ucus_tam"` — tüm bacaklar × tüm esnek tarihler.
- Her gün 04:15: `"tam"` — tüm ürünler tüm siteler + `kovala`.

Kurallar:
- İkinci kopya engeli: `~/.local/share/kazi/tarama.pid` üzerinde `fcntl.flock`.
- Bekçi: her tur ayrı alt süreçte (`multiprocessing` fork) süre sınırıyla çalışır;
  süre aşılırsa süreç sonlandırılır, Brave kilidi düşer ve sızan sekmeler temizlenir.
- `--bir-kez` tek tur (`["rtx"]`), `--kuru` bildirim göndermeden çalışır.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import fcntl
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable
import urllib.request

from ..modeller import TZ_ISTANBUL
from ..tarayici import CDP, Tarayici, brave_kilidi
from . import depo
from .tara import tara
from .teyit import kovala

VERI_DIZINI = Path.home() / ".local" / "share" / "kazi"
PID_DOSYA = VERI_DIZINI / "tarama.pid"
BEKCI_DURUM = VERI_DIZINI / "bekci_bildirim.json"
BOZULMA_DOSYA = VERI_DIZINI / "bozulma_bildirim.json"
RTX_ID = "gpu_rtx5080"

IS_SURE_SN: dict[str, int] = {
    "rtx": 20 * 60,
    "hizli": 45 * 60,
    "tam": 150 * 60,
    "ucus": 60 * 60,
    "ucus_tam": 120 * 60,
}


class DonguZatenCalisiyor(RuntimeError):
    """Aynı anda ikinci tarama döngüsü kopyası çalışıyor."""


def log(kaynak: str, mesaj: str) -> None:
    zaman = datetime.now(TZ_ISTANBUL).strftime("%Y-%m-%d %H:%M:%S")
    try:
        sys.stderr.write(f"[{zaman}] [{kaynak}] {mesaj}\n")
    except Exception:
        pass


def _json_oku(yol: Path, varsayilan: Any = None) -> Any:
    try:
        return json.loads(Path(yol).read_text(encoding="utf-8"))
    except Exception:
        return varsayilan


def _json_yaz(yol: Path, veri: Any) -> None:
    p = Path(yol)
    p.parent.mkdir(parents=True, exist_ok=True)
    gecici = Path(str(p) + ".tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
    gecici.replace(p)


# ------------------------------------------------------------------ zamanlama (saf fonksiyonlar)
def planla(simdi: datetime) -> list[str]:
    """`simdi` dakikasında çalışması gereken işler (saf fonksiyon).

    - :05 / :35 → `"rtx"` (RTX tüm siteler + kovala)
    - saat 3'ün katı ve :20 → `"hizli"` (tüm ürünler, yalnız HTTP siteler)
    - saat 3'ün katı ve :50 (03:50 hariç) → `"ucus"` (tüm bacaklar × uçuş kaynakları)
    - 02:50 → `"ucus_tam"` (tüm bacaklar × esnek tarihler)
    - 04:15 → `"tam"` (tüm ürünler, tüm siteler + kovala)
    """
    isler: list[str] = []
    if simdi.minute in (5, 35):
        isler.append("rtx")
    if simdi.hour % 3 == 0 and simdi.minute == 20:
        isler.append("hizli")
    if simdi.hour % 3 == 0 and simdi.minute == 50 and simdi.hour != 3:
        isler.append("ucus")
    if simdi.hour == 2 and simdi.minute == 50:
        isler.append("ucus_tam")
    if simdi.hour == 4 and simdi.minute == 15:
        isler.append("tam")
    return isler


def planla_aralik(baslangic: datetime, bitis: datetime) -> list[str]:
    """`(baslangic, bitis]` aralığındaki tüm planlanmış işler; uzun turda
    kaçırılan dakikalar bir sonraki kontrolde telafi edilir.
    """
    isler: list[str] = []
    t = baslangic.replace(second=0, microsecond=0) + timedelta(minutes=1)
    bitis_dk = bitis.replace(second=0, microsecond=0)
    while t <= bitis_dk:
        isler.extend(planla(t))
        t += timedelta(minutes=1)
    return isler


# ------------------------------------------------------------------ ikinci kopya engeli
@contextmanager
def tek_kopya(yol: Path | str | None = None):
    """`~/.local/share/kazi/tarama.pid` üzerinde fcntl kilidi; ikinci kopyayı reddeder."""
    kilit = Path(yol).expanduser() if yol else PID_DOSYA
    kilit.parent.mkdir(parents=True, exist_ok=True)
    dosya = open(kilit, "a+", encoding="utf-8")
    try:
        fcntl.flock(dosya.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        dosya.close()
        raise DonguZatenCalisiyor(f"tarama döngüsü zaten çalışıyor: {kilit}") from e
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


# ------------------------------------------------------------------ tek tur
def _yok(_olay: Any = None) -> None:
    return None


def bir_tur(
    isler: list[str] | None = None,
    kuru: bool = False,
    bildirim_fn: Callable[[Any], Any] | None = None,
    gonder_fn: Callable[[Any], Any] | None = None,
    simdi: datetime | None = None,
    v: Any = None,
    con: Any = None,
    tarayici: Any = None,
    **_: Any,
) -> dict[str, Any]:
    """İşleri sırayla çalıştırır; kapsam/süre/hata özeti döndürür. Hata turu öldürmez."""
    simdi = simdi or datetime.now(TZ_ISTANBUL)
    isler = list(isler or ["rtx"])
    geri_cagirim = bildirim_fn or gonder_fn
    etkin_bildirim = _yok if kuru else (geri_cagirim or _yok)

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
            bildirim_fn=etkin_bildirim,
        )
    )

    t0 = time.monotonic()
    kapsam: list[str] = []
    hatalar: list[str] = []

    try:
        for is_ in isler:
            try:
                if is_ == "rtx":
                    sonuc = tara(
                        hedef_v,
                        t,
                        urun_idler=[RTX_ID],
                        bildirim_fn=etkin_bildirim,
                    )
                    kapsam.append(f"rtx {sonuc.kapsam_ozeti()}")
                    log("tarama_dongu", f"rtx turu: {sonuc.kapsam_ozeti()}")
                    teyit_sonuclari = kovala(
                        hedef_v,
                        t,
                        RTX_ID,
                        bildirim_fn=etkin_bildirim,
                    )
                    sayim: dict[str, int] = {}
                    for tk in teyit_sonuclari:
                        d_str = str(tk.teyit_durumu)
                        sayim[d_str] = sayim.get(d_str, 0) + 1
                    kapsam.append(f"teyit {len(teyit_sonuclari)} aday {sayim}")
                    log("tarama_dongu", f"rtx teyit: {len(teyit_sonuclari)} aday {sayim}")
                    if hasattr(t, "brave_birak"):
                        try:
                            t.brave_birak()
                        except Exception:
                            pass

                elif is_ == "hizli":
                    sonuc = tara(
                        hedef_v,
                        t,
                        hizli=True,
                        bildirim_fn=etkin_bildirim,
                    )
                    kapsam.append(f"hizli {sonuc.kapsam_ozeti()}")
                    log("tarama_dongu", f"hızlı tur: {sonuc.kapsam_ozeti()}")

                elif is_ in ("ucus", "ucus_tam"):
                    from ..ucus import tara as ucus_tara_mod

                    _s = datetime.now(TZ_ISTANBUL)
                    usonuc = ucus_tara_mod.tara(
                        hedef_v,
                        t,
                        azami_tarih=None if is_ == "ucus_tam" else 1,
                        rotasyon=_s.timetuple().tm_yday * 8 + _s.hour // 3,
                    )
                    kapsam.append(f"ucus {usonuc.kapsam_ozeti()}")
                    log("tarama_dongu", f"uçuş turu: {usonuc.kapsam_ozeti()}")

                elif is_ == "tam":
                    sonuc = tara(hedef_v, t, bildirim_fn=etkin_bildirim)
                    kapsam.append(f"tam {sonuc.kapsam_ozeti()}")
                    log("tarama_dongu", f"tam tur: {sonuc.kapsam_ozeti()}")
                    for urun in depo.urunler_listele(hedef_v, aktif=True):
                        if not urun.id:
                            continue
                        teyitler = kovala(
                            hedef_v,
                            t,
                            urun.id,
                            bildirim_fn=etkin_bildirim,
                        )
                        if teyitler:
                            sayim_u: dict[str, int] = {}
                            for tk in teyitler:
                                d_str = str(tk.teyit_durumu)
                                sayim_u[d_str] = sayim_u.get(d_str, 0) + 1
                            kapsam.append(f"teyit {urun.id} {len(teyitler)} aday {sayim_u}")
                else:
                    hatalar.append(f"bilinmeyen iş: {is_}")
            except Exception as e:
                hatalar.append(f"{is_}: {type(e).__name__}: {e}")
                log("tarama_dongu", f"iş {is_} hata {type(e).__name__}: {e}")
    finally:
        if t_kendi and hasattr(t, "kapat"):
            try:
                t.kapat()
            except Exception:
                pass
        if v_kendi and hasattr(hedef_v, "kapat"):
            try:
                hedef_v.kapat()
            except Exception:
                pass

    sure = round(time.monotonic() - t0, 1)
    ozet = {"isler": isler, "sure": sure, "kapsam": kapsam, "hatalar": hatalar}
    log(
        "tarama_dongu",
        f"TUR {isler} {sure:.1f}s — {'; '.join(kapsam) or 'kapsam yok'}"
        + (f" — HATA {hatalar}" if hatalar else ""),
    )
    return ozet


# ------------------------------------------------------------------ bekçi (alt süreç + süre sınırı)
def tur_suresi(isler: list[str]) -> float:
    return float(sum(IS_SURE_SN.get(i, 30 * 60) for i in isler))


def _alt_tur(isler: list[str], kuru: bool, bildirim_fn: Any, simdi: datetime | None) -> None:
    try:
        bir_tur(isler=isler, kuru=kuru, bildirim_fn=bildirim_fn, simdi=simdi)
    except Exception as e:
        log("tarama_dongu", f"alt tur çöktü {type(e).__name__}: {e}")


def sekmeleri_temizle(cdp: str = CDP) -> int:
    """Öldürülen turun sızdırdığı sekmeleri kapat (yalnız Brave kilidi boştayken; 1 boş sekme kalır)."""
    try:
        with brave_kilidi(zaman_asimi=5):
            with urllib.request.urlopen(cdp + "/json/list", timeout=10) as r:
                sayfalar = [x for x in json.load(r) if x.get("type") == "page"]
            if not sayfalar:
                return 0
            bos = [x for x in sayfalar if str(x.get("url", "")).startswith("about:blank")]
            if not bos:
                urllib.request.urlopen(
                    urllib.request.Request(cdp + "/json/new?about:blank", method="PUT"),
                    timeout=10,
                ).read()
            kapanan = 0
            for x in sayfalar:
                if x in bos[:1]:
                    continue
                urllib.request.urlopen(cdp + "/json/close/" + str(x["id"]), timeout=10).read()
                kapanan += 1
            return kapanan
    except Exception as e:
        log("tarama_dongu", f"sekme temizliği yapılamadı {type(e).__name__}: {e}")
        return 0


def bekcili_tur(
    isler: list[str],
    kuru: bool = False,
    bildirim_fn: Callable[[Any], Any] | None = None,
    gonder_fn: Callable[[Any], Any] | None = None,
    simdi: datetime | None = None,
    sure_sn: float | None = None,
    hedef: Callable[..., Any] | None = None,
    temizle: Callable[[], int] | None = sekmeleri_temizle,
) -> bool:
    """Turu alt süreçte çalıştırır; süre aşılırsa öldürür. `True` = zamanında bitti."""
    geri_cagirim = bildirim_fn or gonder_fn
    ctx = mp.get_context("fork")
    etkin_sure = tur_suresi(isler) if sure_sn is None else float(sure_sn)
    pr = ctx.Process(
        target=hedef or _alt_tur,
        args=(isler, kuru, geri_cagirim, simdi),
        daemon=True,
    )
    pr.start()
    pr.join(etkin_sure)
    if not pr.is_alive():
        return True

    pr.terminate()
    pr.join(2)
    if pr.is_alive():
        pr.kill()
        pr.join(2)

    kapanan = temizle() if temizle else 0
    mesaj = (
        f"⏱ tarama turu {isler} {int(etkin_sure // 60)} dk sınırını aştı → durduruldu "
        f"(Brave kilidi bırakıldı, {kapanan} sekme kapatıldı). Sonraki turda yeniden denenecek."
    )
    log("tarama_dongu", mesaj)
    try:
        bugun = datetime.now(TZ_ISTANBUL).strftime("%Y-%m-%d")
        d = _json_oku(BEKCI_DURUM, {}) or {}
        if d.get("gun") != bugun and not kuru and geri_cagirim is not None:
            geri_cagirim({"tur": "bekci_zaman_asimi", "isler": isler, "mesaj": "🛠 " + mesaj})
            _json_yaz(BEKCI_DURUM, {"gun": bugun})
    except Exception as e:
        log("tarama_dongu", f"bekçi bildirimi gönderilemedi {e}")
    return False


# ------------------------------------------------------------------ ana döngü ve CLI
def dongu(
    bekle: float = 15.0,
    kuru: bool = False,
    bildirim_fn: Callable[[Any], Any] | None = None,
    gonder_fn: Callable[[Any], Any] | None = None,
    simdi_fn: Callable[[], datetime] = lambda: datetime.now(TZ_ISTANBUL),
    uyu: Callable[[float], Any] = time.sleep,
) -> None:
    """Sonsuz zamanlama döngüsü; uzun turda kaçan slotlar telafi edilir."""
    son = simdi_fn().replace(second=0, microsecond=0)
    while True:
        simdi = simdi_fn()
        dakika = simdi.replace(second=0, microsecond=0)
        isler = planla_aralik(son, dakika)
        son = dakika
        if isler:
            try:
                bekcili_tur(
                    isler=isler,
                    kuru=kuru,
                    bildirim_fn=bildirim_fn,
                    gonder_fn=gonder_fn,
                    simdi=simdi,
                )
            except Exception as e:
                log("tarama_dongu", f"tur çöktü {type(e).__name__}: {e}")
        uyu(max(1.0, float(bekle)))


def calistir(
    bir_kez: bool = False,
    kuru: bool = False,
    *,
    tek_kez: bool = False,
    pid_yolu: Path | str | None = None,
    bildirim_fn: Callable[[Any], Any] | None = None,
) -> int:
    """CLI girişi: tek kopya kilidi altında tek tur (`--bir-kez`) ya da sonsuz döngü."""
    tek = bool(bir_kez or tek_kez)
    try:
        with tek_kopya(pid_yolu):
            if tek:
                ozet = bir_tur(isler=["rtx"], kuru=kuru, bildirim_fn=bildirim_fn)
                print(f"tur: {ozet['isler']} · {ozet['sure']:.1f}s")
                for satir in ozet["kapsam"]:
                    print(f"  {satir}")
                if ozet["hatalar"]:
                    print(f"  hatalar: {ozet['hatalar']}")
                return 0
            log("tarama_dongu", "döngü başladı")
            dongu(kuru=kuru, bildirim_fn=bildirim_fn)
    except DonguZatenCalisiyor as e:
        log("tarama_dongu", str(e))
        print(str(e))
        return 3
    except KeyboardInterrupt:
        log("tarama_dongu", "döngü kapatıldı (Ctrl-C)")
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    args_liste = list(sys.argv[1:] if argv is None else argv)
    if args_liste and args_liste[0] == "dongu":
        args_liste = args_liste[1:]

    parser = argparse.ArgumentParser(prog="python -m kazi.tarama dongu")
    parser.add_argument("--bir-kez", action="store_true", help="Tek tur (RTX) çalıştır ve çık")
    parser.add_argument("--kuru", action="store_true", help="Bildirim göndermeden çalıştır")
    parser.add_argument("--pid", type=Path, default=None, help="Özel PID kilit dosyası yolu")
    ns = parser.parse_args(args_liste)
    return calistir(bir_kez=ns.bir_kez, kuru=ns.kuru, pid_yolu=ns.pid)


if __name__ == "__main__":
    raise SystemExit(main())
