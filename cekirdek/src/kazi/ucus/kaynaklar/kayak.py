"""Kayak kaynağı — Brave (SARTNAME §3).

Ayrıştırma mantığı eski `ucus/kaynaklar/kayak.py` ile birebir aynıdır;
yalnız çıktı `kazi.seyahat.segment.Segment` nesneleridir.
"""
from __future__ import annotations

import re

from ...seyahat.segment import Segment
from ..model import Ucus, bacak_bilgi, saat_metni, ucuslar_segment
from . import _oturum
from .google import havalimanlari

KAYNAK = "kayak"
SITE = "https://www.kayak.com.tr/"
AG_DESEN = r"/i/api/search/dynamic/flights/poll"
HAZIR_DESEN = r"\d+\s+sonuç|sonuç bulunamadı|Sonuç yok"


def _kodlar(x) -> str:
    """'HND' ya da ['HND','NRT'] → 'HND,NRT' (Kayak tek adreste çoklu havalimanı kabul eder)."""
    if isinstance(x, (list, tuple)):
        return ",".join(str(k).upper() for k in x if k)
    return str(x).upper()


def url_olustur(kalkis, varis, tarih: str, yolcu: int = 1) -> str:
    return (f"https://www.kayak.com.tr/flights/{_kodlar(kalkis)}-"
            f"{_kodlar(varis)}/{tarih}/{int(yolcu)}adults?sort=price_a")


def arama_bitti_mi(yanitlar: list[dict]) -> bool:
    """Kayak arama API'si (poll) "complete" dediyse sonuçlar tamam. 15.11 canlı: "N sonuç" metni artık sayfada yok →
    her sayfa 70 sn zaman aşımına kadar bekliyordu; poll yanıtı `status: complete` geliyor."""
    for y in reversed(yanitlar or []):
        j = y.get("json") if isinstance(y, dict) else None
        if isinstance(j, dict) and str(j.get("status", "")).lower() == "complete":
            return True
    return False


# ---------------------------------------------------------------- API ayrıştırıcı
def _fiyat(sonuc: dict) -> float | None:
    """Kişi başı ve toplam fiyat; toplam varsa onu kullan (model toplam ister)."""
    for bo in sonuc.get("bookingOptions") or []:
        fees = (bo or {}).get("fees") or {}
        tp = (fees.get("totalPrice") or {}).get("price")
        if tp:
            try:
                return float(tp)
            except Exception:
                pass
        dp = (bo or {}).get("displayPrice") or {}
        if dp.get("price"):
            try:
                return float(dp["price"])
            except Exception:
                pass
    return None


def ayristir_api(yanitlar: list[dict], bacak_id: str = "", tarih: str = "2026-12-27",
                 yolcu: int = 1) -> list[Segment]:
    sonuclar: list[dict] = []
    for y in yanitlar or []:
        j = (y or {}).get("json")
        if isinstance(j, dict) and isinstance(j.get("results"), list):
            sonuclar.extend(j["results"])
    out: list[Ucus] = []
    gorulen: set[tuple] = set()
    for r in sonuclar:
        if not isinstance(r, dict) or r.get("type") != "core":
            continue
        fiyat = _fiyat(r)
        if not fiyat or fiyat <= 0:
            continue
        lf = (r.get("legFarings") or [{}])[0]
        ks = saat_metni(lf.get("approxDepartureTime"))
        vs = saat_metni(lf.get("approxArrivalTime"))
        rota: list[str] = []
        hava: list[str] = []
        for bo in r.get("bookingOptions") or []:
            for attr in (bo or {}).get("detailedFareAttributes") or []:
                for rd in attr.get("routeData") or []:
                    o, d = rd.get("origin"), rd.get("destination")
                    if o and not rota:
                        rota.append(str(o).upper())
                    if d and (not rota or rota[-1] != str(d).upper()):
                        rota.append(str(d).upper())
        for lg in r.get("legs") or []:
            for sg in lg.get("segments") or []:
                m = re.match(r"\d{13}([A-Z0-9]{2})\d+", str(sg.get("id") or ""))
                if m and m.group(1) not in hava:
                    hava.append(m.group(1))
        seg_say = sum(len(lg.get("segments") or []) for lg in (r.get("legs") or []))
        if len(rota) < 2:
            continue
        anahtar = (fiyat, ks, vs, r.get("resultId"))
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        out.append(Ucus(
            kaynak=KAYNAK, bacak_id=bacak_id, tarih=tarih, fiyat_toplam=fiyat,
            havayolu=", ".join(hava) or "?", rota=rota,
            aktarma=max(seg_say - 1, 0),
            kalkis_saat=ks, varis_saat=vs,
            link=url_olustur(rota[0], rota[-1], tarih, yolcu),
            para="TRY", yontem="brave", yolcu=yolcu,
        ))
    return ucuslar_segment(out)


# ---------------------------------------------------------------- metin ayrıştırıcı
_SAAT_SATIR = re.compile(r"(?m)^(?P<ks>\d{2}:\d{2}) – (?P<vs>\d{2}:\d{2})(?P<gd>\+\d+)?$")
_KOD_SATIR = re.compile(r"^([A-Z]{3})(?=[A-ZÇĞİÖŞÜ]|$)")
_FIYAT_BLOK = re.compile(
    r"(?:(?P<sure>\d+sa\.(?:\s*\d+dk\.)?)\n)?"
    r"(?P<hava>[^\n]+)\n(?:\d+\n)+[₺$](?P<kisi>[\d.,]+)\n"
    r"[₺$](?P<toplam>[\d.,]+) toplam")
_SURE = re.compile(r"^(\d+)sa\.(?:\s*(\d+)dk\.)?$")


def _sayi(s: str) -> float | None:
    try:
        return float(str(s).replace(".", "").replace(",", "."))
    except Exception:
        return None


def ayristir_metin(metin: str, bacak_id: str = "", tarih: str = "2026-12-27",
                   yolcu: int = 1) -> list[Segment]:
    if not metin:
        return []
    esles = list(_SAAT_SATIR.finditer(metin))
    out: list[Ucus] = []
    for i, m in enumerate(esles):
        bas = m.end()
        son = esles[i + 1].start() if i + 1 < len(esles) else len(metin)
        blok = metin[bas:son]
        fb = _FIYAT_BLOK.search(blok)
        if not fb:
            continue
        toplam = _sayi(fb.group("toplam"))
        if not toplam or toplam <= 0:
            kisi = _sayi(fb.group("kisi"))
            toplam = (kisi * yolcu) if kisi else None
        if not toplam:
            continue
        satirlar = [s.strip() for s in blok.split("\n")]
        rota = []
        for s in satirlar:
            km = _KOD_SATIR.match(s)
            if km and km.group(1) not in rota:
                rota.append(km.group(1))
        if len(rota) < 2:
            continue
        if len(rota) > 2:
            rota = [rota[0], *rota[2:], rota[1]]
        sure = None
        if fb.group("sure"):
            sm = _SURE.match(fb.group("sure"))
            if sm:
                sure = int(sm.group(1)) * 60 + int(sm.group(2) or 0)
        out.append(Ucus(
            kaynak=KAYNAK, bacak_id=bacak_id, tarih=tarih, fiyat_toplam=toplam,
            havayolu=fb.group("hava").strip(), rota=rota,
            aktarma=max(len(rota) - 2, 0), sure_dk=sure,
            kalkis_saat=saat_metni(m.group("ks")),
            varis_saat=saat_metni(m.group("vs")),
            ayri_bilet="Kendi kendine aktarma" in blok,
            link=url_olustur(rota[0], rota[-1], tarih, yolcu),
            para="TRY", yontem="brave", yolcu=yolcu,
        ))
    tekil: dict[tuple, Ucus] = {}
    for u in out:
        tekil.setdefault((u.fiyat_toplam, u.havayolu, u.kalkis_saat,
                          u.varis_saat, tuple(u.rota)), u)
    return ucuslar_segment(sorted(tekil.values(), key=lambda u: u.fiyat_toplam))


# ---------------------------------------------------------------- site içi form
SEHIR_ADI = {
    "IST": "Istanbul", "SAW": "Istanbul", "NRT": "Tokyo", "HND": "Tokyo",
    "TYO": "Tokyo", "ICN": "Seoul", "GMP": "Seoul", "SEL": "Seoul",
    # 15.11: plandaki Türkiye noktaları (Ankara bacağı eklenince form "kalkış seçilemedi: ESB" veriyordu)
    "ESB": "Ankara", "ANK": "Ankara", "ADB": "Izmir", "IZM": "Izmir",
}


def _konum_sec(pg, etiket: str, iata: str) -> bool:
    """15.11: ortak `_oturum.oneri_sec` (önceki oturumdan kalan çip temizlenir, yalnız öneri listesi aranır)."""
    try:
        girdi = pg.get_by_role("combobox", name=etiket).first
    except Exception:
        return False
    return _oturum.oneri_sec(pg, girdi, [iata, SEHIR_ADI.get(iata, iata)], iata,
                             'ul[role="listbox"] li[role="option"], [id*="smarty-input-list"] [role=option]')


def _yolcu_sec(pg, yolcu: int) -> bool:
    if yolcu <= 1:
        return True
    try:
        dugme = pg.get_by_role("button").filter(
            has_text=re.compile(r"yetişkin", re.I)).first
        dugme.click(timeout=5000)
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
            pg.wait_for_timeout(400)
        except Exception:
            return False
    try:
        uygula = pg.get_by_role("button", name=re.compile(r"bitti|uygula|tamam", re.I))
        if uygula.count():
            uygula.first.click(timeout=3000)
    except Exception:
        pass
    pg.wait_for_timeout(700)
    return True


def form_doldur(pg, kalkis: str, varis: str, tarih: str, yolcu: int = 1) -> bool:
    """Ana sayfadaki uçuş formunu doldurup ara (canlı DOM seçicileri)."""
    # 15.11 canlı: Kayak ana sayfası yapay zekâlı aramaya geçti — "Hedef konum" kutusu yok ("Dilediğinizi sorun").
    # Form doldurulamaz → hemen False: oturum_ac aynı sekmeden sonuç adresine geçer (kural §2.7), 6+ sn boşa gitmez.
    if not pg.get_by_role("combobox", name="Hedef konum").count():
        return False
    try:
        grp = pg.get_by_role("radiogroup", name="Seyahat türü")
        if grp.count():
            grp.first.get_by_text("Tek yön", exact=True).click(timeout=4000)
            pg.wait_for_timeout(800)
    except Exception:
        pass
    if not _konum_sec(pg, "Başlangıç konumu", kalkis):
        raise RuntimeError(f"kayak kalkış seçilemedi: {kalkis}")
    if not _konum_sec(pg, "Hedef konum", varis):
        raise RuntimeError(f"kayak varış seçilemedi: {varis}")

    try:
        pg.locator('[aria-label="Gidiş tarihi"]').first.click(timeout=6000)
        pg.wait_for_timeout(1400)
    except Exception:
        raise RuntimeError("kayak tarih düğmesi yok")
    if not _oturum.takvim_sec(pg, tarih):
        raise RuntimeError(f"kayak tarih seçilemedi: {tarih}")
    _yolcu_sec(pg, yolcu)

    ara = pg.get_by_role("button", name="Ara", exact=True)
    if not ara.count():
        raise RuntimeError("kayak Ara düğmesi yok")
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
            t, "kayak.com.tr", SITE,
            lambda pg: form_doldur(pg, kalkislar[0], varislar[0], tarih, yolcu),
            ilk_url)
    # 15.11: bütün havalimanı çiftleri TEK sayfada (HND,NRT-ICN,GMP): canlı ölçüm 4 sayfa × 73 sn → 1 sayfa, aynı uçuşlar
    if kalkislar and varislar:
        url = url_olustur(kalkislar, varislar, tarih, yolcu)
        try:
            t.hiz_sinirla("kayak.com.tr")
        except Exception:
            pass
        yanitlar, metin = t.brave_ag_ve_govde(
            url, AG_DESEN, site="kayak.com.tr",
            azami_bekle=70.0, hazir_desen=HAZIR_DESEN, hazir_fn=arama_bitti_mi,
            sayfa=t._sekme("kayak.com.tr"))
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
