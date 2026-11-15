"""Enuygun kaynağı — Brave (SARTNAME §3).

Ayrıştırma mantığı eski `ucus/kaynaklar/enuygun.py` ile birebir aynıdır;
yalnız çıktı `kazi.seyahat.segment.Segment` nesneleridir.
"""
from __future__ import annotations

import re

from ...seyahat.segment import Segment
from ..model import Ucus, bacak_bilgi, saat_metni, ucuslar_segment
from . import _oturum
from .google import havalimanlari

KAYNAK = "enuygun"
SITE = "https://www.enuygun.com/ucak-bileti/"
AG_DESEN = r"enuygun\.com/.*/api/.*(search|init)"
# 15.11: tek yolcuda "Toplam" yazmaz (yalnız "16.735 TL Seç") → hazır kalıbı tutmuyor, her istek 60 sn bekliyordu
HAZIR_DESEN = r"Toplam\s|\d\s*TL\s*Seç|sonuç bulunamadı|Uygun uçuş bulunamadı"

SEHIR = {
    "IST": "istanbul", "SAW": "istanbul",
    "NRT": "tokyo", "HND": "tokyo", "TYO": "tokyo",
    "ICN": "seul", "GMP": "seul", "SEL": "seul",
    "ESB": "ankara", "ANK": "ankara", "ADB": "izmir", "IZM": "izmir",
}


def url_olustur(kalkis: str, varis: str, tarih: str, yolcu: int = 1) -> str:
    o, d = str(kalkis).upper(), str(varis).upper()
    t = tarih.split("-")
    gidis = f"{t[2]}.{t[1]}.{t[0]}"
    slug = f"{SEHIR.get(o, o.lower())}-{SEHIR.get(d, d.lower())}-{o.lower()}-{d.lower()}"
    return (f"https://www.enuygun.com/ucak-bileti/arama/{slug}/"
            f"?gidis={gidis}&yetiskin={int(yolcu)}&cocuk=0&bebek=0&sinif=ekonomi")


# ---------------------------------------------------------------- ayrıştırıcı
_BLOK = re.compile(r"(?=Kalkış \d{1,2} [A-ZÇĞİÖŞÜ][a-zçğıöşü]+ \d{4})")
_BASLIK = re.compile(
    r"Kalkış (?P<kt>\d{1,2} [A-ZÇĞİÖŞÜ][a-zçğıöşü]+ \d{4}) (?P<ks>\d{2}:\d{2}), "
    r"varış (?P<vt>\d{1,2} [A-ZÇĞİÖŞÜ][a-zçğıöşü]+ \d{4}) (?P<vs>\d{2}:\d{2})\.\s*"
    r"(?P<sure>[^,]+),\s*(?P<akt>[^.]+)\.")
_SEG = re.compile(r"\b([A-Z]{3}) (\d{2}:\d{2}) - ([A-Z]{3}) (\d{2}:\d{2})")
_KOD = re.compile(r"(?m)^([A-Z]{3})$")
_FIYAT = re.compile(
    r"(?P<kisi>[\d][\d.,]*)\s*(?:\xa0|\s)*TL\s*Toplam\s*(?P<toplam>[\d][\d.,]*)\s*(?:\xa0|\s)*TL")
# tek yolcu: "Toplam" satırı yok, fiyatın hemen ardından "Seç" düğmesi gelir
_FIYAT_TEK = re.compile(r"(?P<kisi>\d[\d.,]*)\s*(?:\xa0|\s)*TL\s*Seç\b")
_HAVA = re.compile(r"(?m)^([^\n]{2,60})\n(?=(?:[A-Z]{3}\n)+)")


def _sayi(s: str) -> float | None:
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except Exception:
        return None


def ayristir_metin(metin: str, bacak_id: str = "", tarih: str = "2026-12-27",
                   yolcu: int = 1) -> list[Segment]:
    if not metin:
        return []
    out: list[Ucus] = []
    for m in _BASLIK.finditer(metin):
        bas = m.start()
        sonraki = _BASLIK.search(metin, m.end())
        blok = metin[bas:sonraki.start() if sonraki else len(metin)]
        fm = _FIYAT.search(blok)
        if fm:
            toplam = _sayi(fm.group("toplam"))
            if not toplam or toplam <= 0:
                kisi = _sayi(fm.group("kisi"))
                toplam = (kisi * yolcu) if kisi else None
        else:
            ft = _FIYAT_TEK.search(blok)
            if not ft:
                continue
            kisi = _sayi(ft.group("kisi"))
            toplam = (kisi * yolcu) if kisi else None
        if not toplam:
            continue
        rota: list[str] = []
        for s in _SEG.finditer(blok):
            if not rota:
                rota.append(s.group(1))
            rota.append(s.group(3))
        if len(rota) < 2:
            rota = [k for k in _KOD.findall(blok)]
        if len(rota) < 2:
            continue
        aktarma = max(len(rota) - 2, 0)
        hm = _HAVA.search(blok)
        havayolu = hm.group(1).strip() if hm else "?"
        sure = None
        for satir in blok.split("\n"):
            satir = satir.strip()
            sm = re.fullmatch(r"(?:(\d+)g\s+)?(\d+)sa\s+(\d+)dk", satir)
            if sm:
                sure = ((int(sm.group(1) or 0) * 24 + int(sm.group(2))) * 60
                        + int(sm.group(3)))
                break
            gm = re.fullmatch(r"(\d+)g\s+(\d+)dk", satir)
            if gm:
                sure = int(gm.group(1)) * 24 * 60 + int(gm.group(2))
                break
        out.append(Ucus(
            kaynak=KAYNAK, bacak_id=bacak_id, tarih=tarih, fiyat_toplam=toplam,
            havayolu=havayolu, rota=rota, aktarma=aktarma, sure_dk=sure,
            kalkis_saat=saat_metni(m.group("ks")),
            varis_saat=saat_metni(m.group("vs")),
            link=url_olustur(rota[0], rota[-1], tarih, yolcu),
            para="TRY", yontem="brave", yolcu=yolcu,
        ))
    tekil: dict[tuple, Ucus] = {}
    for u in out:
        tekil.setdefault((u.fiyat_toplam, u.havayolu, u.kalkis_saat,
                          u.varis_saat, tuple(u.rota)), u)
    return ucuslar_segment(sorted(tekil.values(), key=lambda u: u.fiyat_toplam))


def ayristir_api(yanitlar: list[dict], bacak_id: str = "", tarih: str = "2026-12-27",
                 yolcu: int = 1) -> list[Segment]:
    """Enuygun sonuçları HTML'de gelir; yakalanan JSON'larda uçuş listesi yoksa []."""
    return []


# ---------------------------------------------------------------- site içi form
SEHIR_ADI = {
    "IST": "İstanbul", "SAW": "İstanbul", "NRT": "Tokyo", "HND": "Tokyo",
    "TYO": "Tokyo", "ICN": "Seul", "GMP": "Seul", "SEL": "Seul",
    # 15.11: plandaki Türkiye noktaları (Ankara bacağı eklenince form "kalkış seçilemedi: ESB" veriyordu)
    "ESB": "Ankara", "ANK": "Ankara", "ADB": "İzmir", "IZM": "İzmir",
}


def _girdi_sec(pg, tid: str, metin: str, iata: str) -> bool:
    """15.11: ortak `_oturum.oneri_sec`; öneriler `<tid'nin öneki>-option-item-N` (data-testid)."""
    girdi = pg.locator(f'[data-testid="{tid}"]').first
    oneki = tid.removesuffix("-input")
    return _oturum.oneri_sec(pg, girdi, [SEHIR_ADI.get(iata, iata), iata], iata,
                             f'[data-testid^="{oneki}-option-item"], [data-testid^="{oneki}"] [role="option"]')


def _yolcu_sec(pg, yolcu: int) -> bool:
    if yolcu <= 1:
        return True
    try:
        pg.locator('[data-testid="undefined-popover-button"]').first.click(timeout=5000)
        pg.wait_for_timeout(900)
    except Exception:
        return False
    artir = None
    for ad in (re.compile(r"yetişkin.*(artır|arttır|ekle|\+)", re.I),
               re.compile(r"(artır|arttır|ekle|\+)", re.I)):
        try:
            loc = pg.get_by_role("button", name=ad)
            if loc.count():
                artir = loc.first
                break
        except Exception:
            continue
    if artir is None:
        return False
    for _ in range(yolcu - 1):
        try:
            artir.click(timeout=3000)
            pg.wait_for_timeout(350)
        except Exception:
            return False
    try:
        uygula = pg.get_by_role("button", name=re.compile(r"uygula|tamam|bitti", re.I))
        if uygula.count():
            uygula.first.click(timeout=3000)
    except Exception:
        pass
    pg.wait_for_timeout(700)
    return True


def _takvim_gun_sec(pg, tarih: str) -> bool:
    """15.11 canlı: takvim günleri `data-testid="datepicker-active-day"` düğmeleri, ay başlığı
    `...-month-name-and-year` ("Aralık 2026"); aria-label yok → genel takvim_sec bulamıyordu. Ay görünmüyorsa ileri."""
    y, a, g = (int(x) for x in tarih.split("-"))
    etiket = f"{_oturum.AYLAR[a]} {y}"
    for _ in range(13):
        aylar = pg.locator('[data-testid$="datepicker-calendar-month"]')
        for i in range(aylar.count()):
            ay = aylar.nth(i)
            try:
                if etiket not in (ay.inner_text() or ""):
                    continue
                gunler = ay.locator('[data-testid="datepicker-active-day"]')
                for j in range(gunler.count()):
                    d = gunler.nth(j)
                    if (d.inner_text() or "").strip() == str(g) and d.is_visible():
                        d.click(timeout=4000)
                        pg.wait_for_timeout(700)
                        return True
                return False            # ay görünüyor ama gün seçilemez (geçmiş/kapalı)
            except Exception:
                continue
        ileri = pg.locator('[data-testid*="next"], button[aria-label*="Sonraki"], button[aria-label*="sonraki"]')
        try:
            if not ileri.count():
                return False
            ileri.first.click(timeout=3000)
            pg.wait_for_timeout(500)
        except Exception:
            return False
    return False


def form_doldur(pg, kalkis: str, varis: str, tarih: str, yolcu: int = 1) -> bool:
    """Ana sayfadaki uçuş formunu doldurup ara (canlı DOM seçicileri)."""
    try:
        tek = pg.locator('[data-testid="search-one-way-label"]')
        if tek.count():
            tek.first.click(timeout=4000)
            pg.wait_for_timeout(600)
    except Exception:
        pass
    if not _girdi_sec(pg, "endesign-flight-origin-autosuggestion-input",
                      SEHIR_ADI.get(kalkis, kalkis), kalkis):
        raise RuntimeError(f"enuygun kalkış seçilemedi: {kalkis}")
    if not _girdi_sec(pg, "endesign-flight-destination-autosuggestion-input",
                      SEHIR_ADI.get(varis, varis), varis):
        raise RuntimeError(f"enuygun varış seçilemedi: {varis}")

    try:
        pg.locator('[data-testid="enuygun-homepage-flight-departureDate-'
                   'datepicker-input"]').first.click(timeout=6000)
        pg.wait_for_timeout(1400)
    except Exception:
        raise RuntimeError("enuygun tarih alanı yok")
    if not (_takvim_gun_sec(pg, tarih) or _oturum.takvim_sec(pg, tarih)):
        raise RuntimeError(f"enuygun tarih seçilemedi: {tarih}")
    _yolcu_sec(pg, yolcu)

    ara = pg.locator('[data-testid="enuygun-homepage-flight-submitButton"]')
    if not ara.count():
        raise RuntimeError("enuygun Ara düğmesi yok")
    return _oturum.aramayi_baslat(pg, ara.first)


# ---------------------------------------------------------------- giriş
def ara(t, bacak, tarih: str) -> list[Segment]:
    b = bacak_bilgi(bacak)
    bacak_id = b["id"]
    yolcu = b["yolcu"]
    segmentler: list[Segment] = []
    gorulen: set[tuple] = set()
    kalkislar = havalimanlari(b.get("kalkis"))
    varislar = havalimanlari(b.get("varis"))
    ilk_url = (url_olustur(kalkislar[0], varislar[0], tarih, yolcu)
               if kalkislar and varislar else None)
    if kalkislar and varislar:
        _oturum.oturum_ac(
            t, "enuygun.com", SITE,
            lambda pg: form_doldur(pg, kalkislar[0], varislar[0], tarih, yolcu),
            ilk_url)
    for o in kalkislar:
        for d in varislar:
            url = url_olustur(o, d, tarih, yolcu)
            try:
                t.hiz_sinirla("enuygun.com")
            except Exception:
                pass
            yanitlar, metin = t.brave_ag_ve_govde(
                url, AG_DESEN, site="enuygun.com",
                azami_bekle=60.0, hazir_desen=HAZIR_DESEN,
                sayfa=t._sekme("enuygun.com"))
            sonuc = ayristir_metin(metin, bacak_id, tarih, yolcu)
            if not sonuc:
                sonuc = ayristir_api(yanitlar, bacak_id, tarih, yolcu)
            for s in sonuc:
                anahtar = (
                    s.toplam,
                    s.kalkis_nokta,
                    s.varis_nokta,
                    tuple(a.nokta for a in s.aktarmalar),
                    s.kalkis,
                    s.varis,
                    s.firma,
                )
                if anahtar in gorulen:
                    continue
                gorulen.add(anahtar)
                segmentler.append(s)
    segmentler.sort(key=lambda s: s.kisi_basi)
    return segmentler
