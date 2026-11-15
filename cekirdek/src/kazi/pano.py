"""Arayüz ekranları için hesaplanmış görünümler (api/SOZLESME.md: /pano, /urunler/{id}/detay, /siteler)."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .db import VeriTabani
from .site_adi import okunur_ad, site_anahtari
from .kurallar.piyasa import UrunAyar, hedef_altinda, piyasa_ozeti
from .modeller import Teklif

TR = timezone(timedelta(hours=3))


def _simdi() -> datetime:
    return datetime.now(TR)


def _hedef(v: VeriTabani, urun_id: str) -> float | None:
    """Alarm fiyatı: kullanıcının arayüzden girdiği (alarm tablosu) öncelikli; yoksa eski sistemin hedef fiyatı."""
    a = v.alarmlar().get(urun_id)
    if a is not None:
        return a
    r = v.baglanti.execute("SELECT hedef_fiyat FROM izleme WHERE urun_id=? AND etkin=1 ORDER BY id DESC LIMIT 1",
                           (urun_id,)).fetchone()
    return None if r is None else r[0]


def _gecerli(t: Teklif) -> bool:
    return t.sepet_fiyati is not None and t.stok is not False and str(t.teyit_durumu) != "urun_farkli"


def _toplayici_ozeti(t: Teklif) -> bool:
    """Akakçe/Cimri/Epey'in kendi model sayfası fiyatı (mağaza değil). Eşit fiyatta gerçek mağaza önce gelir."""
    return site_anahtari(t.site) in ("akakce", "cimri", "epey") and not t.satici


def _taze(t: Teklif, simdi: datetime, saat: float) -> bool:
    return datetime.fromisoformat(t.gozlem_zamani) >= simdi - timedelta(hours=saat)


def _teklif_json(v: VeriTabani, t: Teklif, ozet: dict, hedef: float | None, ayar: UrunAyar) -> dict:
    taksitler = v.taksitler(t.id) if t.id else []
    pesin = max((x.taksit_sayisi for x in taksitler if (x.vade_farki_yuzde or 0) == 0), default=None)
    j = {"teklif_id": t.id, "site": okunur_ad(t.site), "site_anahtari": site_anahtari(t.site), "satici": t.satici, "baslik": t.baslik, "url": t.url,
            "sepet_fiyati": t.sepet_fiyati, "liste_fiyati": t.liste_fiyati, "stok": t.stok,
            "taksit": {"pesin_fiyatina": pesin,
                       "tablo": [x.model_dump(exclude={"id", "teklif_id"}) for x in taksitler if x.banka]},
            "kaynak_katmani": None if t.kaynak_katmani is None else str(t.kaynak_katmani),
            "gozlem_zamani": t.gozlem_zamani, "teyit_durumu": str(t.teyit_durumu), "teyit_zamani": t.teyit_zamani,
            "kanit_goruntusu": t.kanit_goruntusu, "hedef_altinda": hedef_altinda(t, hedef),
            "alarm_altinda": hedef is not None and t.sepet_fiyati is not None and t.sepet_fiyati <= hedef}
    return j


ILAN_GUNCELLIK_SAAT = 3   # son taramada (± 3 sa) görülmeyen ilan bayattır: sitede artık yok ya da fiyatı değişmiştir


def _taze_teklifler(v: VeriTabani, urun_id: str, simdi: datetime, ayar: UrunAyar) -> list[Teklif]:
    """Pencere içi (24 sa) VE ürünün en son gözleminden en fazla ILAN_GUNCELLIK_SAAT önce görülmüş ilanlar.
    14.11: ilan bazlı gruplamadan sonra sabahki bayat 109.999 TL ilanı 'en iyi' çıkıyordu."""
    tum = [t for t in v.teklifler(urun_id) if _taze(t, simdi, ayar.pencere_saat)]
    if not tum:
        return tum
    son = max(datetime.fromisoformat(t.gozlem_zamani) for t in tum)
    return [t for t in tum if datetime.fromisoformat(t.gozlem_zamani) >= son - timedelta(hours=ILAN_GUNCELLIK_SAAT)]


def pano(v: VeriTabani, ayar: UrunAyar | None = None) -> dict:
    ayar = ayar or UrunAyar()
    simdi = _simdi()
    urunler, toplam, eksik, secili_idler = [], 0.0, [], []
    secimler = v.toplam_secimleri()
    disarida = v.takip_disi()
    for u in v.urunler_listele(aktif=True):
        if u.id in disarida:
            continue
        k = u.eslesme_kurallari or {}
        hedef = _hedef(v, u.id)
        tum = _taze_teklifler(v, u.id, simdi, ayar)
        ozet = piyasa_ozeti(tum, simdi, ayar)
        gecerli = sorted((t for t in tum if _gecerli(t)), key=lambda t: (t.sepet_fiyati, _toplayici_ozeti(t)))
        en_iyi = _teklif_json(v, gecerli[0], ozet, hedef, ayar) if gecerli else None
        son = max((t.gozlem_zamani for t in tum), default=None)
        # toplama dahil mi: kullanıcının kutucuk seçimi; hiç seçmediyse eski "sistemde" işareti
        toplamda = secimler.get(u.id, bool(k.get("sistemde")))
        if toplamda:
            secili_idler.append(u.id)
            if en_iyi:
                toplam += en_iyi["sepet_fiyati"]
            else:
                eksik.append(u.kanonik_ad)
        urunler.append({"id": u.id, "ad": u.kanonik_ad, "kategori": k.get("kategori"), "sistemde": toplamda,
                        "toplamda": toplamda, "alarm_fiyat": hedef,
                        "hedef_fiyat": hedef, "ozet": ozet, "en_iyi": en_iyi,
                        "son_tarama": son})
    return {"guncelleme": simdi.isoformat(timespec="seconds"), "urunler": urunler,
            "sistem_toplami": {"tutar": round(toplam, 2), "eksik": eksik, "adet": len(secili_idler), "secili": secili_idler},
            "takip_disi": [{"id": u.id, "ad": u.kanonik_ad, "zaman": disarida[u.id]}
                           for u in v.urunler_listele(aktif=True) if u.id in disarida]}


def detay(v: VeriTabani, urun_id: str, ayar: UrunAyar | None = None) -> dict | None:
    ayar = ayar or UrunAyar()
    u = v.urun_getir(urun_id)
    if not u:
        return None
    simdi = _simdi()
    hedef = _hedef(v, urun_id)
    tum = _taze_teklifler(v, urun_id, simdi, ayar)
    ozet = piyasa_ozeti(tum, simdi, ayar)
    teklifler = sorted(tum, key=lambda t: (t.sepet_fiyati is None, t.sepet_fiyati or 0))
    gunluk: dict[str, list[float]] = defaultdict(list)
    for t in v.teklifler(urun_id, yalniz_son=False):
        if _gecerli(t):
            gunluk[t.gozlem_zamani[:10]].append(t.sepet_fiyati)
    k = u.eslesme_kurallari or {}
    return {"urun": {"id": u.id, "ad": u.kanonik_ad, "kategori": k.get("kategori"), "hedef_fiyat": hedef, "alarm_fiyat": hedef,
                     "kurallar": {"dahil": k.get("dahil") or [], "haric": k.get("haric") or []}},
            "ozet": ozet, "teklifler": [_teklif_json(v, t, ozet, hedef, ayar) for t in teklifler],
            "gecmis": [{"gun": g, "en_dusuk": min(f), "ortalama": round(sum(f) / len(f), 2)} for g, f in sorted(gunluk.items())]}


def site_24s_yeni(v: VeriTabani, simdi: datetime | None = None) -> dict[str, dict]:
    """Son 24 saatin site özeti YENİ taramalardan (olay: tarama_site_bitti → {site, adet, hata?}).
    Eski evren-asistan fiyat.db kapandığı için oradaki sayılar 24 saatte sıfırlanır (15.11)."""
    sinir = ((simdi or _simdi()) - timedelta(hours=24)).isoformat(timespec="seconds")
    d: dict[str, dict] = {}
    for (veri,) in v.baglanti.execute(
            "SELECT veri FROM olay WHERE tur = 'tarama_site_bitti' AND zaman >= ?", (sinir,)):
        try:
            o = json.loads(veri or "{}")
        except ValueError:
            continue
        site = o.get("site")
        if not site:
            continue
        x = d.setdefault(site, {"basarili": 0, "hatali": 0, "teklif": 0})
        hata = str(o.get("hata") or "")
        if o.get("tur") in ("desteklenmiyor", "blok_atlandi") or hata.startswith(("desteklenmiyor", "atlandı")):
            # 15.11: desteklenmeyen kaynak (THY/Qatar/Türkmenistan) ve aynı taramada zaten engellenip atlanan deneme
            # "hatalı" sayılmasın — site sağlığı 36/36 hata gösteriyordu
            x["atlanan"] = x.get("atlanan", 0) + 1
            continue
        if o.get("hata"):
            x["hatali"] += 1
        else:
            x["basarili"] += 1
        x["teklif"] += int(o.get("adet") or 0)
    return d


def siteler(v: VeriTabani, son_24s: dict[str, dict] | None = None) -> list[dict]:
    son_24s = {**(son_24s or {}), **site_24s_yeni(v)}
    from .siteler import SITELER
    # dogrudan: sitenin kendi bağdaştırıcısı var mı (yoksa yalnız Akakçe/Cimri üzerinden görülen mağaza)
    return [{**s.model_dump(mode="json"), "son_24s": son_24s.get(s.ad, {"basarili": 0, "hatali": 0, "teklif": 0}),
             "dogrudan": s.ad in SITELER or s.tur == "ucus"}
            for s in v.siteler_listele()]
