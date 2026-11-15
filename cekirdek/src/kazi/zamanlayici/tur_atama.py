"""Site → tur ataması (15.11 kullanıcı isteği): her site ya "Hızlı HTTP turu"nda ya "Tarayıcı turu"nda taranır, ikisinde
birden değil.

- İlk kurulumda atama son başarılı katmandan türetilir (K0 + HTTP ile aranabilen + Brave'e zorlanmamış → http).
  Sonrasında atama sabittir; yalnız kullanıcı (arayüz düğmesi) ya da hata eşiği değiştirir.
- HTTP turunda bir sitenin hata sayacı (ürün×site işi başına: engel/hata) HATA_ESIGI'ni AŞINCA site kendiliğinden
  Tarayıcı turuna geçer. Bir turu hiç hatasız geçen sitenin sayacı sıfırlanır (seyrek 429'lar birikip siteyi taşımasın).
- Tarayıcı turu ayrıca son HTTP turunda başarısız olan ürün×site işlerini de tarar (hizli.basarisiz_ciftler).
- Tablo `site_tur` (site PK, tur, tip, neden, zaman, http_hata).
"""
from __future__ import annotations

from contextlib import nullcontext
from typing import Any

HTTP = "http"
TARAYICI = "tarayici"
TURLER = (HTTP, TARAYICI)
HATA_ESIGI = 100

SEMA = """CREATE TABLE IF NOT EXISTS site_tur (
    site TEXT PRIMARY KEY,
    tur TEXT NOT NULL,
    tip TEXT NOT NULL,
    neden TEXT,
    zaman TEXT,
    http_hata INTEGER NOT NULL DEFAULT 0
)"""

UCUS_HTTP_VARSAYILAN = ("google",)


def _con(v: Any):
    if hasattr(v, "baglanti"):
        return v.baglanti, getattr(v, "_kilit", None) or nullcontext()
    return v, nullcontext()


def urun_siteleri_hepsi() -> list[str]:
    from ..siteler import SITELER
    return sorted(SITELER)


def ucus_kaynaklari_hepsi() -> list[str]:
    from ..ucus.kaynaklar import KAYNAKLAR
    return sorted(KAYNAKLAR)


def http_ile_aranabilir(site: str) -> bool:
    """Site modülü aramayı yalnız tarayıcıdaki kutudan yapıyorsa False (HTTP turuna taşınırsa hata verir)."""
    from ..siteler import SITELER
    mod = SITELER.get(site)
    return bool(getattr(mod, "HTTP_ARAMA", True)) if mod is not None else True


def _varsayilan_http(v: Any) -> set[str]:
    """İlk kurulum: son tercihi K0 olan, HTTP ile aranabilen, Brave'e zorlanmamış siteler (+ uçuşta google)."""
    from . import hizli
    k0 = hizli.k0_siteleri(v)
    urun = {s for s in urun_siteleri_hepsi() if s in k0 and http_ile_aranabilir(s)}
    ucus = {k for k in UCUS_HTTP_VARSAYILAN if k in ucus_kaynaklari_hepsi()}
    return (urun | ucus) - hizli.brave_zorunlu_siteler(urun | ucus)


def kur(v: Any, zaman_iso: str | None = None) -> None:
    con, kilit = _con(v)
    with kilit:
        con.execute(SEMA)
        mevcut = {r[0] for r in con.execute("SELECT site FROM site_tur").fetchall()}
        con.commit()
    eksik_urun = [s for s in urun_siteleri_hepsi() if s not in mevcut]
    eksik_ucus = [k for k in ucus_kaynaklari_hepsi() if k not in mevcut]
    if not (eksik_urun or eksik_ucus):
        return
    http = _varsayilan_http(v)
    with kilit:
        for tip, adlar in (("urun", eksik_urun), ("ucus", eksik_ucus)):
            for s in adlar:
                con.execute("INSERT OR IGNORE INTO site_tur (site, tur, tip, neden, zaman) VALUES (?,?,?,?,?)",
                            (s, HTTP if s in http else TARAYICI, tip, "başlangıç: son çalışan yönteme göre", zaman_iso))
        con.commit()


def atamalar(v: Any) -> dict[str, dict]:
    kur(v)
    con, kilit = _con(v)
    with kilit:
        satirlar = con.execute("SELECT site, tur, tip, neden, zaman, http_hata FROM site_tur ORDER BY tip DESC, site").fetchall()
    return {r[0]: {"site": r[0], "tur": r[1], "tip": r[2], "neden": r[3], "zaman": r[4], "http_hata": int(r[5] or 0)}
            for r in satirlar}


def siteler(v: Any, tur: str, tip: str) -> list[str]:
    return sorted(s for s, a in atamalar(v).items() if a["tur"] == tur and a["tip"] == tip)


def ata(v: Any, site: str, tur: str, neden: str, zaman_iso: str | None) -> dict:
    """Siteyi tura ata (sayaç sıfırlanır). Bilinmeyen site/tur → ValueError."""
    if tur not in TURLER:
        raise ValueError(f"tur: {' | '.join(TURLER)}")
    a = atamalar(v)
    if site not in a:
        raise ValueError(f"bilinmeyen site: {site}")
    con, kilit = _con(v)
    with kilit:
        con.execute("UPDATE site_tur SET tur = ?, neden = ?, zaman = ?, http_hata = 0 WHERE site = ?",
                    (tur, neden, zaman_iso, site))
        con.commit()
    return atamalar(v)[site]


def http_hatalarini_isle(v: Any, hata: dict[str, int], denenen: set[str], zaman_iso: str | None,
                         esik: int = HATA_ESIGI) -> list[str]:
    """Bir HTTP turunun site başı hata sayılarını işle. Eşiği AŞAN http sitesi Tarayıcı turuna geçer; turu hatasız
    geçen sitenin sayacı sıfırlanır. Taşınan siteleri döndürür."""
    a = atamalar(v)
    tasinan: list[str] = []
    con, kilit = _con(v)
    with kilit:
        for site in denenen:
            if site not in a:
                continue
            n = int(hata.get(site, 0))
            yeni = a[site]["http_hata"] + n if n else 0
            if a[site]["tur"] == HTTP and yeni > esik:
                con.execute("UPDATE site_tur SET tur = ?, neden = ?, zaman = ?, http_hata = 0 WHERE site = ?",
                            (TARAYICI, f"otomatik: HTTP turunda {yeni} hata (eşik {esik})", zaman_iso, site))
                tasinan.append(site)
            else:
                con.execute("UPDATE site_tur SET http_hata = ? WHERE site = ?", (yeni, site))
        con.commit()
    return tasinan
