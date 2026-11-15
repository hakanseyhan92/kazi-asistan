"""Skyscanner kaynağı — Brave (SARTNAME §3, §2.7 kuralı).

Ayrıştırma mantığı eski `ucus/kaynaklar/skyscanner.py` ile birebir aynıdır;
yalnız çıktı `kazi.seyahat.segment.Segment` nesneleridir.
"""
from __future__ import annotations

import re
from datetime import datetime

from ...seyahat.segment import Segment
from ..model import Ucus, bacak_bilgi, saat_metni, tl_sayi, ucuslar_segment
from .google import havalimanlari

KAYNAK = "skyscanner"
SITE_ANA = "https://www.skyscanner.com.tr/"
AG_DESEN = r"web-unified-search|radar|itinerar"
HAZIR_DESEN = r"\d+\s+sonuç|sonuç bulunamadı|uygun uçuş|uçuş bulunamadı"

AYLAR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
}


def url_olustur(kalkis: str, varis: str, tarih: str, yolcu: int = 1) -> str:
    """Skyscanner sonuç URL'si (kodlar küçük harf, tarih YYMMDD)."""
    g = datetime.strptime(tarih, "%Y-%m-%d").strftime("%y%m%d")
    return ("https://www.skyscanner.com.tr/transport/flights/"
            f"{kalkis.lower()}/{varis.lower()}/{g}/"
            f"?adultsv2={int(yolcu)}&cabinclass=economy&rtn=0"
            "&preferdirects=false&currency=TRY")


# ---------------------------------------------------------------- API ayrıştırıcı
def _sonuc_listeleri(yanitlar: list[dict]) -> list[list[dict]]:
    """Yakalanan yanıtlardan `itineraries.results` listelerini çıkar."""
    listeler: list[list[dict]] = []
    for y in yanitlar or []:
        j = y.get("json") if isinstance(y, dict) else None
        if not isinstance(j, dict):
            continue
        it = j.get("itineraries")
        if isinstance(it, dict) and isinstance(it.get("results"), list):
            listeler.append(it["results"])
    return listeler


def _fiyat(sonuc: dict) -> float | None:
    p = sonuc.get("price")
    if isinstance(p, dict):
        for k in ("raw", "amount"):
            if p.get(k) is not None:
                try:
                    return float(p[k])
                except Exception:
                    pass
    po = sonuc.get("pricingOptions")
    if isinstance(po, list) and po:
        p2 = (po[0] or {}).get("price") or {}
        if p2.get("amount") is not None:
            try:
                return float(p2["amount"])
            except Exception:
                pass
    return None


def _bacak_ozet(legs: list[dict]) -> dict:
    rota: list[str] = []
    havayollari: list[str] = []
    sure = 0
    aktarma = 0
    for lg in legs:
        segs = lg.get("segments") or []
        for sg in segs:
            o = ((sg.get("origin") or {}).get("displayCode")
                 or (sg.get("origin") or {}).get("flightPlaceId"))
            d = ((sg.get("destination") or {}).get("displayCode")
                 or (sg.get("destination") or {}).get("flightPlaceId"))
            if not rota and o:
                rota.append(str(o).upper())
            if d:
                rota.append(str(d).upper())
            mc = sg.get("marketingCarrier") or {}
            ad = mc.get("name") or mc.get("displayCode")
            if ad and ad not in havayollari:
                havayollari.append(ad)
        try:
            sure += int(lg.get("durationInMinutes") or 0)
        except Exception:
            pass
        try:
            aktarma += int(lg.get("stopCount") or 0)
        except Exception:
            pass
        if not havayollari:
            for c in ((lg.get("carriers") or {}).get("marketing") or []):
                ad = c.get("name") or c.get("displayCode")
                if ad and ad not in havayollari:
                    havayollari.append(ad)
    kalkis = None
    varis = None
    if legs:
        kalkis = saat_metni((legs[0] or {}).get("departure"))
        varis = saat_metni((legs[-1] or {}).get("arrival"))
    return {"rota": rota, "havayolu": ", ".join(havayollari),
            "sure_dk": sure or None, "aktarma": aktarma,
            "kalkis_saat": kalkis, "varis_saat": varis}


def ayristir_api(yanitlar: list[dict], bacak_id: str = "", tarih: str = "2026-12-27",
                 yolcu: int = 1) -> list[Segment]:
    """Yakalanan `web-unified-search` JSON'larını `Segment` listesine çevir."""
    gorulen: set[str] = set()
    ucuslar: list[Ucus] = []
    for results in _sonuc_listeleri(yanitlar):
        for r in results:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id") or "")
            if rid and rid in gorulen:
                continue
            if rid:
                gorulen.add(rid)
            fiyat = _fiyat(r)
            if fiyat is None or fiyat <= 0:
                continue
            legs = r.get("legs") or []
            if not legs:
                continue
            oz = _bacak_ozet(legs)
            if len(oz["rota"]) < 2:
                continue
            dep = (legs[0] or {}).get("departure") or ""
            gercek_tarih = dep[:10] if len(dep) >= 10 else tarih
            ucuslar.append(Ucus(
                kaynak=KAYNAK, bacak_id=bacak_id, tarih=gercek_tarih,
                fiyat_toplam=fiyat, havayolu=oz["havayolu"] or "?",
                rota=oz["rota"], aktarma=oz["aktarma"], sure_dk=oz["sure_dk"],
                kalkis_saat=oz["kalkis_saat"], varis_saat=oz["varis_saat"],
                ayri_bilet=bool(r.get("isSelfTransfer")),
                link=url_olustur(oz["rota"][0], oz["rota"][-1], gercek_tarih, yolcu),
                para="TRY", yontem="brave", yolcu=yolcu,
            ))
    return ucuslar_segment(ucuslar)


# ---------------------------------------------------------------- metin ayrıştırıcı
_BLOK_RX = re.compile(
    r"(?P<havayolu>[A-ZÇĞİÖŞÜ][^\n]{1,60})\n+"
    r"(?:\d+\.\s*uçuş seçeneği:\s*)?Yolcu başına (?P<kisi>[\d.,]+)\s*TL\.?\s*"
    r"Toplam(?: fiyat)? (?P<toplam>[\d.,]+)\s*TL\.?"
    r"(?P<ara>.{0,220}?)"
    r"Kalkış:\s*(?P<kalkis_ad>[^,\n]+),\s*(?P<ks>\d{1,2}:\d{2})\s+"
    r"Varış:\s*(?P<varis_ad>[^,\n]+),\s*(?P<vs>\d{1,2}:\d{2})"
    r"(?P<govde>.{0,600}?)"
    r"(?=\d+\.\s*uçuş seçeneği:|Teklifleri gör|\Z)",
    re.DOTALL,
)
_KOD_RX = re.compile(r"^([A-Z]{3})$", re.MULTILINE)
_KOD_DISI = {"TL", "TRY", "CO2", "IATA", "KOD", "VAR"}
_AKTARMA_RX = re.compile(r"(aktarmasız|(\d+)\s*aktarma|bir aktarma)", re.IGNORECASE)


def ayristir_metin(metin: str, bacak_id: str = "", tarih: str = "2026-12-27",
                   yolcu: int = 1) -> list[Segment]:
    """`page.inner_text("body")` metninden uçuşları `Segment` listesi olarak çıkar (API yedeği)."""
    if not metin:
        return []
    out: list[Ucus] = []
    for m in _BLOK_RX.finditer(metin):
        toplam = tl_sayi(m.group("toplam"))
        kisi = tl_sayi(m.group("kisi"))
        if not toplam or toplam <= 0:
            if kisi:
                toplam = kisi * yolcu
            else:
                continue
        govde = (m.group("ara") or "") + (m.group("govde") or "")
        kodlar = [k for k in _KOD_RX.findall(govde) if k not in _KOD_DISI]
        rota = kodlar if len(kodlar) >= 2 else []
        aktarma = max(len(rota) - 2, 0)
        am = _AKTARMA_RX.search(govde)
        if am and not rota:
            s = am.group(1).lower()
            aktarma = 0 if s == "aktarmasız" else 1 if s == "bir aktarma" else int(am.group(2) or 1)
        if len(rota) < 2:
            rota = ["IST", "NRT"]
        sure = None
        sm = re.search(r"(\d+)\s*sa(?:at)?\.?\s*(\d+)?\s*dk", govde)
        if sm:
            sure = int(sm.group(1)) * 60 + int(sm.group(2) or 0)
        out.append(Ucus(
            kaynak=KAYNAK, bacak_id=bacak_id, tarih=tarih, fiyat_toplam=toplam,
            havayolu=m.group("havayolu").strip(), rota=rota,
            aktarma=aktarma, sure_dk=sure,
            kalkis_saat=saat_metni(m.group("ks")),
            varis_saat=saat_metni(m.group("vs")),
            link=url_olustur(rota[0], rota[-1], tarih, yolcu),
            para="TRY", yontem="brave", yolcu=yolcu,
        ))
    tekil: dict[tuple, Ucus] = {}
    for u in out:
        tekil.setdefault((u.havayolu, u.fiyat_toplam, u.kalkis_saat, u.varis_saat), u)
    return ucuslar_segment(sorted(tekil.values(), key=lambda u: u.fiyat_toplam))


# ---------------------------------------------------------------- ana sayfa araması
def _li_sec(pg, secici: str, metin: str, iata: str) -> bool:
    kutu = pg.locator(secici).first
    kutu.wait_for(state="visible", timeout=15000)
    kutu.click()
    pg.wait_for_timeout(800)
    try:
        kutu.fill("")
    except Exception:
        pass
    pg.wait_for_timeout(300)
    pg.keyboard.type(metin, delay=110)
    adaylar = pg.locator('li[role="option"]')
    if not adaylar.count():
        adaylar = pg.locator("li")
    try:
        adaylar.first.wait_for(state="visible", timeout=15000)
    except Exception:
        return False
    for i in range(adaylar.count()):
        try:
            tx = adaylar.nth(i).inner_text()
        except Exception:
            continue
        if iata in tx:
            adaylar.nth(i).click()
            pg.wait_for_timeout(1200)
            return True
    return False


def _tarih_sec(pg, tarih: str) -> bool:
    t = datetime.strptime(tarih, "%Y-%m-%d")
    etiket = f"{t.day} {AYLAR[t.month]} {t.year}"
    for _ in range(24):
        loc = pg.locator(f'button[aria-label*="{etiket}"]')
        if loc.count():
            try:
                loc.first.click()
                pg.wait_for_timeout(1200)
                return True
            except Exception:
                pass
        nxt = pg.locator('button[aria-label*="Sonraki ay"]')
        if not nxt.count():
            break
        try:
            nxt.first.click()
            pg.wait_for_timeout(900)
        except Exception:
            break
    return False


def _yolcu_sec(pg, yolcu: int) -> bool:
    if yolcu <= 1:
        return True
    try:
        pg.locator('[data-testid="traveller-button"]').first.click()
        pg.wait_for_timeout(1200)
        artir = pg.locator('button[aria-label*="Yetişkin sayısını artırın"]')
        if not artir.count():
            return False
        for _ in range(yolcu - 1):
            artir.first.click()
            pg.wait_for_timeout(400)
        uygula = pg.locator('[data-testid="traveller-selector-apply-button"]')
        if uygula.count():
            uygula.first.click()
        pg.wait_for_timeout(1000)
        return True
    except Exception:
        return False


def formdan_ara(t, kalkis: str, varis: str, tarih: str, yolcu: int = 1) -> bool:
    """Ana sayfa + site içi form ile gerçek bir arama yap (§2.7)."""
    site = "skyscanner.com.tr"
    t.brave_getir(SITE_ANA, site)
    pg = t._sekme(site)
    pg.wait_for_timeout(3000)

    try:
        pg.locator('button[aria-label*="Yolculuk türünü seçin"]').first.click()
        pg.wait_for_timeout(900)
        li = pg.locator("li")
        for i in range(li.count()):
            try:
                if li.nth(i).inner_text().strip() == "Tek yön":
                    li.nth(i).click()
                    pg.wait_for_timeout(1000)
                    break
            except Exception:
                continue
    except Exception:
        pass

    adlar = {"IST": "Istanbul", "NRT": "Tokyo Narita", "HND": "Tokyo Haneda",
             "ICN": "Seoul Incheon", "GMP": "Seoul Gimpo"}
    if not _li_sec(pg, "#originInput-input", adlar.get(kalkis, kalkis),
                   f"({kalkis})"):
        raise RuntimeError(f"skyscanner kalkış seçilemedi: {kalkis}")
    if not _li_sec(pg, "#destinationInput-input", adlar.get(varis, varis),
                   f"({varis})"):
        raise RuntimeError(f"skyscanner varış seçilemedi: {varis}")
    if not _tarih_sec(pg, tarih):
        raise RuntimeError(f"skyscanner tarih seçilemedi: {tarih}")
    _yolcu_sec(pg, yolcu)

    ara = pg.locator('[data-testid="desktop-cta"]')
    if not ara.count():
        ara = pg.get_by_role("button", name="Ara", exact=True)
    if not ara.count():
        raise RuntimeError("skyscanner Ara düğmesi yok")
    ara.first.click()
    try:
        pg.wait_for_url(re.compile(r"/transport/flights/"), timeout=25000)
    except Exception:
        pass
    return "/transport/flights/" in pg.url


# ---------------------------------------------------------------- giriş
def ara(t, bacak, tarih: str) -> list[Segment]:
    b = bacak_bilgi(bacak)
    bacak_id = b["id"]
    yolcu = b["yolcu"]
    siteler = havalimanlari(b.get("kalkis"))
    hedefler = havalimanlari(b.get("varis"))
    segmentler: list[Segment] = []
    gorulen: set[tuple] = set()

    if not getattr(t, "_ss_form_arandi", False):
        ilk_o = siteler[0] if siteler else str(b.get("kalkis")).upper()
        ilk_d = hedefler[0] if hedefler else str(b.get("varis")).upper()
        formdan_ara(t, ilk_o, ilk_d, tarih, yolcu)
        try:
            t._ss_form_arandi = True
        except Exception:
            pass

    for o in siteler:
        for d in hedefler:
            url = url_olustur(o, d, tarih, yolcu)
            try:
                t.hiz_sinirla("skyscanner.com.tr")
            except Exception:
                pass
            yanitlar, metin = t.brave_ag_ve_govde(
                url, AG_DESEN, site="skyscanner.com.tr",
                azami_bekle=60.0, hazir_desen=HAZIR_DESEN)
            sonuc = ayristir_api(yanitlar, bacak_id, tarih, yolcu)
            if not sonuc:
                sonuc = ayristir_metin(metin, bacak_id, tarih, yolcu)
                for s in sonuc:
                    if s.kalkis_nokta and s.varis_nokta:
                        s.link = url_olustur(s.kalkis_nokta, s.varis_nokta, s.kalkis[:10], yolcu)
                    else:
                        s.link = url
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
