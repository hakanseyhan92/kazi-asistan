"""Trip.com kaynağı — Brave (SARTNAME §3).

Ayrıştırma mantığı eski `ucus/kaynaklar/tripcom.py` ile birebir aynıdır;
yalnız çıktı `kazi.seyahat.segment.Segment` nesneleridir.
"""
from __future__ import annotations

import re

from ...seyahat.segment import Segment
from ..model import Ucus, bacak_bilgi, saat_metni, ucuslar_segment
from . import _oturum

KAYNAK = "tripcom"
SITE = "https://tr.trip.com/flights/"
AG_DESEN = r"FlightListSearchSSE|FlightMiddleSearch|flightList|searchresult"
# 15.11: "41 uçuş bulundu" filtre başlığında kartlardan ÖNCE çıkıyordu → sayfa kartsız okunup 0 sonuç (IST↔TYO). Başarı
# artık uçuş listesi akışının gelmesiyle (arama_bitti_mi); kalıp yalnız kartlar ya da "sonuç yok" için.
HAZIR_DESEN = r"Ayrıntıları görüntüle|uçuş bulunamadı|sonuç bulunamadı"

SEHIR_KODU = {
    "Doha": "DOH", "Pekin": "PEK", "Şanghay": "PVG", "Guangzhou": "CAN",
    "Almatı": "ALA", "Astana": "NQZ", "Dubai": "DXB", "Frankfurt": "FRA",
    "Seul": "ICN", "İstanbul": "IST", "Tokyo": "NRT", "Hong Kong": "HKG", "Ankara": "ESB", "İzmir": "ADB",
    "Bangkok": "BKK", "Singapur": "SIN", "Kuala Lumpur": "KUL",
}


def url_olustur(kalkis: str, varis: str, tarih: str, yolcu: int = 1) -> str:
    return ("https://tr.trip.com/flights/showfarefirst"
            f"?dcity={str(kalkis).lower()}&acity={str(varis).lower()}"
            f"&ddate={tarih}&triptype=ow&class=y&quantity={int(yolcu)}"
            "&locale=tr-TR&curr=TRY")


def _saat(deger) -> str | None:
    m = re.search(r"(\d{2}):(\d{2})", str(deger or ""))
    return f"{m.group(1)}:{m.group(2)}" if m else None


# ---------------------------------------------------------------- API ayrıştırıcı
def _yolculuk(jr: dict) -> dict | None:
    segs = jr.get("transportList") or []
    if not segs:
        return None
    rota: list[str] = []
    hava: list[str] = []
    for sg in segs:
        dp = (sg.get("departPoint") or {}).get("airPort") or {}
        ap = (sg.get("arrivePoint") or {}).get("airPort") or {}
        o = str(dp.get("airportCode") or "").upper()
        d = str(ap.get("airportCode") or "").upper()
        if o and not rota:
            rota.append(o)
        if d:
            rota.append(d)
        ad = ((sg.get("flight") or {}).get("airlineInfo") or {}).get("name")
        if ad and ad not in hava:
            hava.append(ad)
    if len(rota) < 2:
        return None
    ilk = (segs[0].get("dateInfo") or {})
    son = (segs[-1].get("dateInfo") or {})
    return {
        "rota": rota, "havayolu": ", ".join(hava) or "?",
        "sure_dk": int(jr.get("duration") or 0) or None,
        "kalkis_saat": _saat(ilk.get("departDate")),
        "varis_saat": _saat(son.get("arriveDate")),
        "aktarma": max(len(segs) - 1, 0),
        "tarih": str(ilk.get("departDate") or "")[:10] or None,
    }


def arama_bitti_mi(yanitlar: list[dict]) -> bool:
    """Uçuş listesi akışı (itineraryList) geldiyse sayfa hazırdır."""
    return any(isinstance((y or {}).get("json"), dict) and "itineraryList" in y["json"] for y in yanitlar or [])


_AYRI_BILET_BAYRAKLARI = {"TRANSFER_SPLICING_MULTIPLE_TICKETS", "NON_INTERLINE_FLIGHT"}


def _sse_ucuslari(j: dict, bacak_id: str, tarih: str, yolcu: int) -> list[Ucus]:
    """15.11 biçimi (FlightListSearchSSE): itineraryList[].journeyList[0].transSectionList[] + policies[].price."""
    adlar = {a.get("code"): a.get("name") for a in j.get("airlineList") or [] if a.get("code")}
    out: list[Ucus] = []
    for it in j.get("itineraryList") or []:
        yol = (it.get("journeyList") or [{}])[0]
        bolumler = yol.get("transSectionList") or []
        if not bolumler:
            continue
        rota: list[str] = []
        hava: list[str] = []
        for b in bolumler:
            o = str(((b.get("departPoint") or {}).get("airportCode")) or "").upper()
            d = str(((b.get("arrivePoint") or {}).get("airportCode")) or "").upper()
            if o and not rota:
                rota.append(o)
            if d:
                rota.append(d)
            kod = (b.get("flightInfo") or {}).get("airlineCode")
            ad = adlar.get(kod) or kod
            if ad and ad not in hava:
                hava.append(ad)
        if len(rota) < 2:
            continue
        for pol in (it.get("policies") or [])[:1]:          # ilk politika = listede gösterilen fiyat
            pr = pol.get("price") or {}
            try:
                toplam = float(pr.get("totalPrice") or 0) or float(pr.get("averagePrice") or 0) * yolcu
            except (TypeError, ValueError):
                continue
            if toplam <= 0:
                continue
            ilk, son = bolumler[0], bolumler[-1]
            out.append(Ucus(
                kaynak=KAYNAK, bacak_id=bacak_id,
                tarih=str(ilk.get("departDateTime") or "")[:10] or tarih, fiyat_toplam=toplam,
                havayolu=", ".join(hava) or "?", rota=rota, aktarma=max(len(bolumler) - 1, 0),
                sure_dk=int(yol.get("duration") or 0) or None,
                kalkis_saat=_saat(ilk.get("departDateTime")), varis_saat=_saat(son.get("arriveDateTime")),
                ayri_bilet=bool(_AYRI_BILET_BAYRAKLARI & set(pol.get("policyFlags") or [])),
                link=url_olustur(rota[0], rota[-1], tarih, yolcu),
                para="TRY", yontem="brave", yolcu=yolcu,
            ))
    return out


def ayristir_api(yanitlar: list[dict], bacak_id: str = "", tarih: str = "2026-12-27",
                 yolcu: int = 1) -> list[Segment]:
    out: list[Ucus] = []
    for y in yanitlar or []:
        j = (y or {}).get("json")
        if not isinstance(j, dict):
            continue
        if "itineraryList" in j:
            out.extend(_sse_ucuslari(j, bacak_id, tarih, yolcu))
            continue
        yollar = {}
        for jr in j.get("journeyList") or []:
            oz = _yolculuk(jr)
            if oz:
                yollar[jr.get("journeyNo")] = oz
        for p in j.get("policyList") or []:
            gi = p.get("gradeInfoList") or []
            if not gi:
                continue
            oz = yollar.get(gi[0].get("journeyNo"))
            if not oz:
                continue
            pr = p.get("price") or {}
            toplam = pr.get("totalPrice")
            try:
                toplam = float(toplam) if toplam else None
            except Exception:
                toplam = None
            if not toplam:
                ort = pr.get("averagePrice") or (pr.get("adult") or {}).get("price")
                toplam = (float(ort) * yolcu) if ort else None
            if not toplam or toplam <= 0:
                continue
            ayri = any(g.get("selfTransfer") for g in gi)
            out.append(Ucus(
                kaynak=KAYNAK, bacak_id=bacak_id,
                tarih=oz["tarih"] or tarih, fiyat_toplam=toplam,
                havayolu=oz["havayolu"], rota=oz["rota"],
                aktarma=oz["aktarma"], sure_dk=oz["sure_dk"],
                kalkis_saat=oz["kalkis_saat"], varis_saat=oz["varis_saat"],
                ayri_bilet=ayri,
                link=url_olustur(oz["rota"][0], oz["rota"][-1], tarih, yolcu),
                para="TRY", yontem="brave", yolcu=yolcu,
            ))
    tekil: dict[tuple, Ucus] = {}
    for u in out:
        tekil.setdefault((u.fiyat_toplam, u.havayolu, u.kalkis_saat,
                          u.varis_saat, tuple(u.rota)), u)
    return ucuslar_segment(sorted(tekil.values(), key=lambda u: u.fiyat_toplam))


# ---------------------------------------------------------------- metin ayrıştırıcı
_KART = re.compile(
    r"(?m)^(?P<hava>[A-ZÇĞİÖŞÜ][^\n]{1,40})\n"
    r"(?P<ks>\d{2}:\d{2})\n(?P<o>[A-Z]{3})\n"
    r"(?P<sure>\d+\s*sa\s*\d+\s*dk)\n"
    r"(?P<akt>[^\n]+)\n"
    r"(?P<vs>\d{2}:\d{2})\n(?P<d>[A-Z]{3})\n"
    r"(?:T\d\n)?(?:\s*\+\d+\n)?"
    r"(?P<kisi>[\d.]+)\s*TL\nToplam\s*(?P<toplam>[\d.]+)\s*TL")


def ayristir_metin(metin: str, bacak_id: str = "", tarih: str = "2026-12-27",
                   yolcu: int = 1) -> list[Segment]:
    if not metin:
        return []
    out: list[Ucus] = []
    for m in _KART.finditer(metin):
        try:
            toplam = float(m.group("toplam").replace(".", ""))
        except Exception:
            continue
        rota = [m.group("o"), m.group("d")]
        akt = m.group("akt")
        if "aktarmasız" not in akt.lower():
            for sehir, kod in SEHIR_KODU.items():
                if sehir.lower() in akt.lower():
                    rota.insert(-1, kod)
                    break
        sm = re.search(r"(\d+)\s*sa\s*(\d+)\s*dk", m.group("sure"))
        out.append(Ucus(
            kaynak=KAYNAK, bacak_id=bacak_id, tarih=tarih, fiyat_toplam=toplam,
            havayolu=m.group("hava").strip(), rota=rota,
            aktarma=max(len(rota) - 2, 0),
            sure_dk=(int(sm.group(1)) * 60 + int(sm.group(2))) if sm else None,
            kalkis_saat=saat_metni(m.group("ks")),
            varis_saat=saat_metni(m.group("vs")),
            ayri_bilet="self" in akt.lower(),
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
    "IST": "İstanbul", "SAW": "İstanbul", "NRT": "Tokyo", "HND": "Tokyo",
    "TYO": "Tokyo", "ICN": "Seul", "GMP": "Seul", "SEL": "Seul",
    # 15.11: plandaki Türkiye noktaları (Ankara bacağı eklenince form "kalkış seçilemedi: ESB" veriyordu)
    "ESB": "Ankara", "ANK": "Ankara", "ADB": "İzmir", "IZM": "İzmir",
}


def _sehir_sec(pg, tid: str, iata: str) -> bool:
    """15.11: ortak `_oturum.oneri_sec`; önce havalimanı satırı (kod eşleşir), yoksa 'Şehir · Tüm havalimanları'."""
    girdi = pg.locator(f'[data-testid="{tid}"]').first
    return _oturum.oneri_sec(pg, girdi, [SEHIR_ADI.get(iata, iata), iata], iata,
                             f'[data-testid="{tid}_poi_wrapper"] li.poi-result__airport, '
                             f'[data-testid="{tid}_poi_wrapper"] .poi-result__title, '
                             '[data-testid="search_result_box"] li.poi-result__airport, '
                             '[data-testid="search_result_box"] .poi-result__title')


def _tarih_sec(pg, tarih: str) -> bool:
    try:
        inp = pg.locator('[data-testid="search_date_depart0"]').first
        inp.click(timeout=6000)
        pg.wait_for_timeout(1200)
    except Exception:
        return False
    if _oturum.takvim_sec(pg, tarih):
        return True
    t = tarih.split("-")
    for metin in (f"{t[2]}.{t[1]}.{t[0]}", f"{t[2]}/{t[1]}/{t[0]}"):
        try:
            inp.fill(metin)
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(900)
            deger = inp.input_value()
            if str(int(t[2])) in deger:
                return True
        except Exception:
            continue
    return False


def _yolcu_sec(pg, yolcu: int) -> bool:
    if yolcu <= 1:
        return True
    try:
        pg.locator(".nh_psgAndCabin").first.click(timeout=5000)
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
        uygula = pg.get_by_role("button", name=re.compile(r"uygula|tamam|bitti|onayla", re.I))
        if uygula.count():
            uygula.first.click(timeout=3000)
    except Exception:
        pass
    pg.wait_for_timeout(700)
    return True


def form_doldur(pg, kalkis: str, varis: str, tarih: str, yolcu: int = 1) -> bool:
    """Ana sayfadaki uçuş formunu doldurup ara (canlı DOM seçicileri)."""
    try:
        tek = pg.locator('[data-testid="flightType_OW"]')
        if tek.count():
            tek.first.click(timeout=4000)
            pg.wait_for_timeout(600)
    except Exception:
        pass
    if not _sehir_sec(pg, "search_city_from0", kalkis):
        raise RuntimeError(f"tripcom kalkış seçilemedi: {kalkis}")
    if not _sehir_sec(pg, "search_city_to0", varis):
        raise RuntimeError(f"tripcom varış seçilemedi: {varis}")
    if not _tarih_sec(pg, tarih):
        raise RuntimeError(f"tripcom tarih seçilemedi: {tarih}")
    _yolcu_sec(pg, yolcu)

    ara = pg.locator('[data-testid="search_btn"]')
    if not ara.count():
        raise RuntimeError("tripcom Ara düğmesi yok")
    return _oturum.aramayi_baslat(pg, ara.first)


# ---------------------------------------------------------------- giriş
def ara(t, bacak, tarih: str) -> list[Segment]:
    b = bacak_bilgi(bacak)
    bacak_id = b["id"]
    yolcu = b["yolcu"]
    segmentler: list[Segment] = []
    gorulen: set[tuple] = set()
    kalkislar = (str(b.get("kalkis") or "").upper(),)
    varislar = (str(b.get("varis") or "").upper(),)
    if kalkislar[0] and varislar[0]:
        _oturum.oturum_ac(
            t, "tr.trip.com", SITE,
            lambda pg: form_doldur(pg, kalkislar[0], varislar[0], tarih, yolcu),
            url_olustur(kalkislar[0], varislar[0], tarih, yolcu))
    for o in kalkislar:
        for d in varislar:
            url = url_olustur(o, d, tarih, yolcu)
            try:
                t.hiz_sinirla("tr.trip.com")
            except Exception:
                pass
            yanitlar, metin = t.brave_ag_ve_govde(
                url, AG_DESEN, site="tr.trip.com",
                azami_bekle=60.0, hazir_desen=HAZIR_DESEN, hazir_fn=arama_bitti_mi,
                sayfa=t._sekme("tr.trip.com"))
            sonuc = ayristir_api(yanitlar, bacak_id, tarih, yolcu)
            if not sonuc:
                sonuc = ayristir_metin(metin, bacak_id, tarih, yolcu)
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
