"""Geçiş dönemi köprüsü: evren-asistan'ın topladığı veriyi (fiyat.db, ucus.db) yeni çekirdeğe taşır.

- `aktar()`  : urunler.json + son N saatin teklifleri → kazi.db (ürün kimliği, izleme/hedef, teklif, taksit, site).
               Eşleştirme eski sistemin `fiyat.eslestir.uygun_mu` kuralıyla yapılır (hazır sistem, aksesuar vb. elenir).
- `seyahat()`: ucus.db'deki son uçuşlar → bacak seçenekleri (fiyata göre) + transit denetimi + yan tur çiftleri.
A2/A7 aşamalarında kazıma yeni çekirdeğe geçince bu modül kaldırılacak.
"""
from __future__ import annotations

import os

import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .db import VeriTabani
from .modeller import Katman, Site, Taksit, Teklif, TeyitDurumu, UrunKimligi

ESKI_KOK = Path(os.environ.get("KAZI_ESKI_KOK") or Path.home() / "evren-asistan")   # isteğe bağlı eski veri kaynağı
FIYAT_DB = ESKI_KOK / "fiyat/veri/fiyat.db"
UCUS_DB = ESKI_KOK / "ucus/veri/ucus.db"
TR = timezone(timedelta(hours=3))
TEYIT = {"dogrulandi": TeyitDurumu.teyitli, "urun_farkli": TeyitDurumu.urun_farkli, "gecerli": TeyitDurumu.bekliyor,
         "acilamadi": TeyitDurumu.bekliyor, "supheli": TeyitDurumu.supheli}


def _tz(z: str | None) -> str | None:
    """Eski sistemin saat dilimsiz yerel zamanı → +03:00."""
    if not z:
        return None
    d = datetime.fromisoformat(z)
    return (d if d.tzinfo else d.replace(tzinfo=TR)).isoformat(timespec="seconds")


def _eslestir():
    if str(ESKI_KOK) not in sys.path:
        sys.path.insert(0, str(ESKI_KOK))
    from fiyat import eslestir  # type: ignore
    from fiyat.model import Teklif as EskiTeklif  # type: ignore
    return eslestir, EskiTeklif


# ------------------------------------------------------------------ ürünler
def aktar(v: VeriTabani, saat: int = 48, fiyat_db: Path = FIYAT_DB) -> dict:
    """Ürünleri ve son `saat` saatin tekliflerini yeniden aktarır (önceki aktarımı siler). Özet döndürür.
    Tamamı TEK işlemdir: hata olursa önceki veri yerinde kalır, API yarım veri görmez."""
    with v.toplu():
        return _aktar(v, saat, fiyat_db)


def _aktar(v: VeriTabani, saat: int, fiyat_db: Path) -> dict:
    eslestir, EskiTeklif = _eslestir()
    con = v.baglanti
    # kart/banka taksit tabloları (A5, ürün sayfasından kazınır) aktarımda kaybolmasın: linke göre sakla, sonra geri bağla
    sakli: dict[str, list[tuple]] = {}
    for url, *satir in con.execute(
            "SELECT t.url, x.banka, x.taksit_sayisi, x.aylik, x.toplam, x.vade_farki_yuzde "
            "FROM taksit x JOIN teklif t ON t.id = x.teklif_id WHERE x.banka IS NOT NULL AND t.url IS NOT NULL"):
        sakli.setdefault(url, []).append(tuple(satir))
    for tablo in ("taksit", "teklif", "izleme", "urun_kimligi", "site"):
        con.execute(f"DELETE FROM {tablo}")

    urunler = eslestir.urunler_yukle()
    for u in urunler:
        kurallar = {k: u.get(k) for k in ("kategori", "sistemde", "dahil", "haric", "min_fiyat", "max_fiyat", "qvl", "not")}
        v.urun_ekle(UrunKimligi(id=u["id"], kanonik_ad=u["ad"], eslesme_kurallari=kurallar))
        con.execute("INSERT INTO izleme (urun_id, hedef_fiyat) VALUES (?, ?)", (u["id"], u.get("hedef_fiyat")))

    eski = sqlite3.connect(fiyat_db)
    eski.row_factory = sqlite3.Row
    sinir = (datetime.now() - timedelta(hours=saat)).isoformat(timespec="seconds")
    say = {"okunan": 0, "aktarilan": 0, "elenen": 0}
    uids = {u["id"]: u for u in urunler}
    for r in eski.execute("SELECT * FROM teklif WHERE zaman >= ? ORDER BY id", (sinir,)):
        say["okunan"] += 1
        u = uids.get(r["urun_id"])
        if not u:
            continue
        et = EskiTeklif(site=r["site"], ad=r["ad"] or "", fiyat=r["fiyat"], url=r["url"] or "", liste_fiyat=r["liste_fiyat"],
                        satici=r["satici"], taksit=r["taksit"], stok=None if r["stok"] is None else bool(r["stok"]))
        if r["durum"] != "urun_farkli" and not eslestir.uygun_mu(u, et):
            say["elenen"] += 1
            continue
        site = r["kaynak"] or r["site"]
        tid = v.teklif_ekle(Teklif(
            urun_id=u["id"], site=site, satici=r["satici"] or (f"{r['site']} üzerinden" if r["kaynak"] else None), baslik=r["ad"],
            url=r["teyit_url"] or r["url"], liste_fiyati=r["liste_fiyat"], sepet_fiyati=r["fiyat"],
            stok=None if r["stok"] is None else bool(r["stok"]),
            pesin_fiyatina_taksit=bool(r["taksit"]) if r["taksit"] is not None else None,
            gozlem_zamani=_tz(r["zaman"]), kaynak_katmani=Katman.K0 if r["yontem"] == "http" else Katman.K2,
            teyit_durumu=TEYIT.get(r["durum"], TeyitDurumu.bekliyor), teyit_zamani=_tz(r["teyit_zamani"])))
        if r["taksit"]:
            v.taksit_ekle(Taksit(teklif_id=tid, taksit_sayisi=int(r["taksit"]), vade_farki_yuzde=0.0))
        for banka, sayi, aylik, toplam, vade in sakli.get(r["teyit_url"] or r["url"], []):
            v.taksit_ekle(Taksit(teklif_id=tid, banka=banka, taksit_sayisi=sayi, aylik=aylik, toplam=toplam, vade_farki_yuzde=vade))
        say["aktarilan"] += 1

    # siteler: son 24 saatin tarama kayıtları
    sinir24 = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    for r in eski.execute("""SELECT site, MAX(CASE WHEN hata IS NULL OR hata='' THEN zaman END) basari,
                                    MAX(CASE WHEN hata IS NOT NULL AND hata!='' THEN zaman END) hata_z,
                                    MAX(yontem) yontem FROM tarama WHERE zaman >= ? GROUP BY site""", (sinir24,)):
        son_hata = eski.execute("SELECT hata FROM tarama WHERE site=? AND hata IS NOT NULL AND hata!='' ORDER BY id DESC LIMIT 1",
                                (r["site"],)).fetchone()
        v.site_kaydet(Site(ad=r["site"], tur="urun", katman_tercihi=Katman.K0 if r["yontem"] == "http" else Katman.K2,
                           son_basari=_tz(r["basari"]), son_hata=_tz(r["hata_z"]),
                           hata_turu=(son_hata[0][:120] if son_hata and r["hata_z"] and (not r["basari"] or r["hata_z"] > r["basari"]) else None)))
    eski.close()
    _ucus_siteleri_aktar(v, sinir24)
    return say


def _ucus_siteleri_aktar(v: VeriTabani, sinir24: str, ucus_db: Path | None = None) -> None:
    """Uçuş kaynaklarının (google, kayak, skyscanner…) son 24 saatlik tarama durumu → site tablosu (tur=ucus)."""
    ucus_db = ucus_db or UCUS_DB
    if not Path(ucus_db).exists():
        return
    u = sqlite3.connect(ucus_db)
    u.row_factory = sqlite3.Row
    for r in u.execute("""SELECT kaynak, MAX(CASE WHEN hata IS NULL OR hata='' THEN zaman END) basari,
                                 MAX(CASE WHEN hata IS NOT NULL AND hata!='' THEN zaman END) hata_z,
                                 MAX(yontem) yontem FROM tarama WHERE zaman >= ? GROUP BY kaynak""", (sinir24,)):
        son_hata = u.execute("SELECT hata FROM tarama WHERE kaynak=? AND hata IS NOT NULL AND hata!='' ORDER BY id DESC LIMIT 1",
                             (r["kaynak"],)).fetchone()
        katman = {"http": Katman.K0, "chromium": Katman.K1, "brave": Katman.K2}.get(r["yontem"] or "", Katman.K3)
        v.site_kaydet(Site(ad=r["kaynak"], tur="ucus", katman_tercihi=katman,
                           son_basari=_tz(r["basari"]), son_hata=_tz(r["hata_z"]),
                           hata_turu=(son_hata[0][:120] if son_hata and r["hata_z"] and (not r["basari"] or r["hata_z"] > r["basari"]) else None)))
    u.close()


def site_24s(fiyat_db: Path = FIYAT_DB) -> dict[str, dict]:
    eski = sqlite3.connect(fiyat_db)
    sinir = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    d = {}
    for site, ok, hata, teklif in eski.execute(
            """SELECT site, SUM(CASE WHEN hata IS NULL OR hata='' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN hata IS NOT NULL AND hata!='' THEN 1 ELSE 0 END), SUM(COALESCE(sonuc_sayisi,0))
               FROM tarama WHERE zaman >= ? GROUP BY site""", (sinir,)):
        d[site] = {"basarili": ok or 0, "hatali": hata or 0, "teklif": teklif or 0}
    eski.close()
    if Path(UCUS_DB).exists():
        u = sqlite3.connect(UCUS_DB)
        for kaynak, ok, hata, adet in u.execute(
                """SELECT kaynak, SUM(CASE WHEN hata IS NULL OR hata='' THEN 1 ELSE 0 END),
                          SUM(CASE WHEN hata IS NOT NULL AND hata!='' THEN 1 ELSE 0 END), SUM(COALESCE(sonuc,0))
                   FROM tarama WHERE zaman >= ? GROUP BY kaynak""", (sinir,)):
            d[kaynak] = {"basarili": ok or 0, "hatali": hata or 0, "teklif": adet or 0}
        u.close()
    return d


# ------------------------------------------------------------------ seyahat
_UTC_OFSET = {"JP": 9, "KR": 9, "TR": 3, "CN": 8, "HK": 8, "TW": 8, "QA": 3, "AE": 4, "KZ": 5, "UZ": 5, "AZ": 4,
              "GE": 4, "IR": 3.5, "TH": 7, "VN": 7, "SG": 8, "PH": 8, "MN": 8, "IN": 5.5, "SA": 3, "EG": 2}


def _ofset(kod: str) -> timezone | None:
    """Uç noktanın UTC farkı (bu ülkelerde Aralık–Ocak'ta yaz saati yok). Bilinmiyorsa None → segment atlanır (C1 #2)."""
    from .seyahat.segment import nokta_ulke
    s = _UTC_OFSET.get(nokta_ulke(kod) or "")
    return None if s is None else timezone(timedelta(hours=s))


def _segment_satiri(r: sqlite3.Row):
    from .seyahat.segment import Aktarma, Segment, ayri_bilet_tahmini
    rota = [x for x in (r["rota"] or "").split(">") if x]
    if len(rota) < 2:
        return None
    k_tz, v_tz = _ofset(rota[0]), _ofset(rota[-1])
    if k_tz is None or v_tz is None:
        return None
    gun = date.fromisoformat(r["tarih"])
    saat_yok = not r["kalkis_saat"]
    kh, km = (int(x) for x in (r["kalkis_saat"] or "12:00").split(":")[:2])
    kalkis = datetime(gun.year, gun.month, gun.day, kh, km, tzinfo=k_tz)
    if r["sure_dk"]:
        varis = (kalkis + timedelta(minutes=int(r["sure_dk"]))).astimezone(v_tz)
    elif r["varis_saat"]:
        vh, vm = (int(x) for x in r["varis_saat"].split(":")[:2])
        varis = datetime(gun.year, gun.month, gun.day, vh, vm, tzinfo=v_tz)
        if varis <= kalkis:
            varis += timedelta(days=1)
    else:
        varis = kalkis + timedelta(hours=3)
        saat_yok = True
    akt = int(r["aktarma"] or 0)
    ab = bool(r["ayri_bilet"]) or bool(ayri_bilet_tahmini(r["havayolu"], akt))
    yolcu = max(int(r["yolcu"] or 1), 1)
    from .ucus.model import arac_tahmini
    s = Segment(id=f"u{r['id']}", arac=arac_tahmini(r["havayolu"]), kalkis_nokta=rota[0], varis_nokta=rota[-1], kalkis=kalkis.isoformat(),
                varis=varis.isoformat(), kisi_basi=r["fiyat_toplam"] / yolcu, toplam=r["fiyat_toplam"],
                aktarmalar=[Aktarma(x, ayri_bilet=ab) for x in rota[1:-1]], aktarma_sayisi=akt, firma=r["havayolu"],
                link=r["link"], kaynak=r["kaynak"], gozlem_zamani=_tz(r["zaman"]))
    return s, saat_yok


def _firma_adi(firma: str | None) -> str:
    """Gösterim: IATA kodu → tam havayolu adı (kayıtta kod kalır; transit kuralları koda bakar)."""
    from .ucus.havayollari import havayolu_adi
    return havayolu_adi(firma)


def _secenek_json(s, uyarilar: list[str]) -> dict:
    return {"id": s.id, "tarih": s.kalkis[:10], "kalkis": s.kalkis, "varis": s.varis, "kalkis_nokta": s.kalkis_nokta,
            "varis_nokta": s.varis_nokta, "aktarmalar": [a.nokta for a in s.aktarmalar], "aktarma_sayisi": s.aktarma_sayisi,
            "sure_dk": s.sure_dk, "firma": _firma_adi(s.firma), "arac": s.arac, "kisi_basi": round(s.kisi_basi, 2), "toplam": s.toplam,
            "kaynak": s.kaynak, "link": s.link, "gozlem_zamani": s.gozlem_zamani,
            "uyarilar": uyarilar, "ayri_bilet": s.ayri_bilet,
            "saat_bilinmiyor": "⚠ kalkış/varış saati kaynakta yok" in uyarilar,
            "aktarma_bekleme_dk": [a.sure_dk for a in s.aktarmalar if a.sure_dk is not None] or None}


def _ucus_anahtari(s) -> tuple:
    """Aynı uçuş (iki kaynakta da): firma + kalkış/varış anı + rota."""
    return (s.firma, s.kalkis_dt.isoformat(), s.varis_dt.isoformat(), s.kalkis_nokta,
            tuple(a.nokta for a in s.aktarmalar), s.varis_nokta)


def _bacak_secenekleri(eski: sqlite3.Connection, bacak_id: str, yolcular, en_erken, en_gec, saat: int, azami: int,
                       v: VeriTabani | None = None):
    """Eski ucus.db + (varsa) kazi'nin kendi uçuş deposu (zamanlayıcı yazar) birleşik; aynı uçuşun EN YENİ gözlemi kalır."""
    from .seyahat.transit import denetle
    sinir = (datetime.now() - timedelta(hours=saat)).isoformat(timespec="seconds")
    tekil: dict[tuple, tuple] = {}
    elenen, elenen_ornek = 0, []
    adaylar = []
    for r in eski.execute("SELECT * FROM ucus WHERE bacak_id=? AND zaman >= ? AND para='TRY' ORDER BY id DESC",
                          (bacak_id, sinir)):
        sonuc = _segment_satiri(r)
        if sonuc:
            adaylar.append((sonuc[0], sonuc[1], r["rota"]))
    if v is not None:
        try:
            from .ucus import depo as ucus_depo
            for s in ucus_depo.son(v, bacak_id, saat=saat):
                rota = ">".join([s.kalkis_nokta, *(a.nokta for a in s.aktarmalar), s.varis_nokta])
                # kimlik "kaynak:rota:tarih:saat" — saat boşsa kalkış 12:00 varsayılmıştır (15.11: kayak IST>NRT "12:00→15:00")
                adaylar.append((s, str(s.id).endswith(":"), rota))
        except Exception as e:                      # depo yoksa/boşsa eski veriyle devam
            print("kazi uçuş deposu okunamadı:", type(e).__name__, e)
    adaylar.sort(key=lambda x: x[0].gozlem_zamani or "", reverse=True)     # en yeni önce
    for s, saat_yok, rota in adaylar:
        g = s.kalkis_dt.date()
        if en_erken and not (en_erken <= g <= en_gec):
            continue
        t = denetle(s, yolcular)
        if not t.gecer:
            elenen += 1
            if len(elenen_ornek) < 15 and all(e["gerekce"] != t.gerekceler[0] for e in elenen_ornek):
                elenen_ornek.append({"firma": _firma_adi(s.firma), "rota": rota, "gerekce": t.gerekceler[0]})
            continue
        uy = t.uyarilar + (["⚠ kalkış/varış saati kaynakta yok"] if saat_yok else [])
        anahtar = _ucus_anahtari(s)
        if anahtar not in tekil:
            tekil[anahtar] = (s, uy)  # adaylar en yeniden eskiye: aynı uçuşun EN YENİ fiyatı kalır
    secenekler = sorted(tekil.values(), key=lambda x: x[0].kisi_basi)
    return secenekler[:azami], elenen, elenen_ornek


def seyahat(saat: int = 36, azami: int = 150, ucus_db: Path = UCUS_DB, plan_sozlugu: dict | None = None,
            v: VeriTabani | None = None) -> dict:
    """Kayıtlı plana (arayüzden düzenlenen; yoksa eski plandan) göre bacak seçenekleri + yan turlar."""
    from .seyahat import plan_depo
    from .seyahat.plan import bacaklar
    from .seyahat.yan_tur import yan_tur_ciftleri
    plan = plan_depo.plan_nesnesi(plan_sozlugu or plan_depo.yukle())
    eski = sqlite3.connect(ucus_db)
    eski.row_factory = sqlite3.Row
    yol = {y.id: y for y in plan.yolcular}

    def jsonla(secenekler):                    # fiyata göre artan (kişi başı)
        return [_secenek_json(s, u) for s, u in secenekler]

    sonuc = {"guncelleme": datetime.now(TR).isoformat(timespec="seconds"),
             "yolcular": [{"id": y.id, "ad": y.ad, "uyruklar": y.uyruklar, "izinler": y.izinler} for y in plan.yolcular],
             "bacaklar": [], "yan_turlar": []}
    ana_secenek: dict[str, list] = {}
    for b in bacaklar(plan):
        sec, elenen, ornek = _bacak_secenekleri(eski, b.id, b.yolcular, b.en_erken, b.en_gec, saat, azami, v)
        ana_secenek[b.id] = sec
        sonuc["bacaklar"].append({"id": b.id, "baslik": f"{b.kalkis_sehir} → {b.varis_sehir}",
                                  "en_erken": str(b.en_erken), "en_gec": str(b.en_gec), "yolcular": [y.id for y in b.yolcular],
                                  "secenekler": jsonla(sec),
                                  "elenen_sayisi": elenen, "elenen_ornekleri": ornek})
    for yt in plan.yan_turlar:
        yolcular = [yol[i] for i in yt.yolcu_idler]
        g, ge, go = _bacak_secenekleri(eski, yt.gidis_bacak_id, yolcular, yt.en_erken_gidis, yt.en_gec_donus, saat, 2000, v)
        d, de, do = _bacak_secenekleri(eski, yt.donus_bacak_id, yolcular, yt.en_erken_gidis, yt.en_gec_donus, saat, 2000, v)
        ciftler = yan_tur_ciftleri([x for x in g if "⚠ kalkış/varış saati kaynakta yok" not in x[1]],
                                   [x for x in d if "⚠ kalkış/varış saati kaynakta yok" not in x[1]], yt, k=30)
        yg = {x["id"]: x for x in jsonla(g)}
        yd = {x["id"]: x for x in jsonla(d)}
        sonuc["yan_turlar"].append({
            "baslik": f"{yt.ana_sehir} ↔ {yt.hedef_sehir} (yalnız {', '.join(y.ad for y in yolcular)}, {yt.kalis_gece} gece)",
            "pencere": [str(yt.en_erken_gidis), str(yt.en_gec_donus)], "yolcular": yt.yolcu_idler,
            "gidis_bacak": {"id": yt.gidis_bacak_id, "secenek_sayisi": len(g), "elenen_sayisi": ge},
            "donus_bacak": {"id": yt.donus_bacak_id, "secenek_sayisi": len(d), "elenen_sayisi": de},
            "ciftler": [{"gidis": yg[c.gidis.id], "donus": yd[c.donus.id], "gece": c.gece,
                         "kisi_basi_toplam": round(c.kisi_basi_toplam, 2), "toplam_sure_dk": c.toplam_sure_dk,
                         "uyarilar": c.uyarilar} for c in ciftler]})
    eski.close()
    return sonuc


def json_yaz(veri, yol: Path) -> None:
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.write_text(json.dumps(veri, ensure_ascii=False, indent=1), encoding="utf-8")
