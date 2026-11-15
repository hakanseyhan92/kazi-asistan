"""Kategori bazlı tarama zamanlayıcısı HTTP uç noktaları (`api/SOZLESME.md`).

Not: bu dosyada `from __future__ import annotations` KULLANILMAZ; FastAPI bağımlılık
ipuçlarını çalışma anında çözer.
"""
from datetime import datetime, timedelta
import math

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from .db import VeriTabani
from .modeller import TZ_ISTANBUL
from .zamanlayici.plan import (
    MANUEL_BEKLEME_DK,
    aktif_durumlar_coz,
    durum_oku,
    iso_bicim,
    iso_coz,
    kategori_aralik_oku,
    kategori_aralik_yaz,
    kategoriler,
    kur,
    manuel_istek_kaydet,
    plan_hesapla,
    sayac_sifirla,
)

router = APIRouter()


def _db(request: Request) -> VeriTabani:
    return request.app.state.db


def _simdi(request: Request) -> datetime:
    fn = getattr(request.app.state, "simdi_fn", None)
    if callable(fn):
        dt = fn()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ_ISTANBUL)
        return dt.astimezone(TZ_ISTANBUL)
    return datetime.now(TZ_ISTANBUL)


def _guncel_plan(db: VeriTabani, simdi: datetime) -> dict:
    kur(db)
    kats = kategoriler(db)
    durum = durum_oku(db)
    ayar = kategori_aralik_oku()
    calisanlar, kuyruk = aktif_durumlar_coz(durum, simdi=simdi)
    from .tarayici.paralel_ayar import paralel_ayarlar
    azami = paralel_ayarlar()["kategori_esz"]
    return plan_hesapla(kats, durum, ayar, simdi, calisan=calisanlar, kuyruk=kuyruk, azami=azami)


@router.get("/tarama/plan")
async def tarama_plani(request: Request) -> dict:
    return _guncel_plan(_db(request), _simdi(request))


@router.put("/tarama/plan")
async def tarama_plani_guncelle(request: Request, d: dict = Body(...)):
    if not isinstance(d, dict) or not isinstance(d.get("kategoriler"), dict):
        return JSONResponse(
            status_code=422,
            content={"hata": 'kategoriler: {"gpu": 30, ...} biçiminde sözlük olmalı', "ayrinti": None},
        )
    try:
        kategori_aralik_yaz(d["kategoriler"])
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"hata": str(exc), "ayrinti": None})
    return _guncel_plan(_db(request), _simdi(request))


@router.post("/tarama/kategori/{id}/simdi")
async def kategori_simdi(id: str, request: Request):
    db = _db(request)
    kur(db)
    simdi = _simdi(request)
    kats = kategoriler(db)
    if id not in {k.id for k in kats}:
        raise HTTPException(status_code=404, detail=f"bilinmeyen kategori: {id}")

    durum = durum_oku(db)
    kat_durum = durum.get(id) or {}
    manuel_son = iso_coz(kat_durum.get("manuel_son"))
    if manuel_son is not None:
        kilit_bitis = manuel_son + timedelta(minutes=MANUEL_BEKLEME_DK)
        if simdi < kilit_bitis:
            kalan_sn = max(1, math.ceil((kilit_bitis - simdi).total_seconds()))
            return JSONResponse(
                status_code=429,
                content={"hata": f"Elle tarama için {kalan_sn} sn beklenmeli", "kalan_sn": kalan_sn},
            )

    calisanlar, _ = aktif_durumlar_coz(durum, simdi=simdi)
    zaman_iso = iso_bicim(simdi) or simdi.isoformat(timespec="seconds")
    manuel_istek_kaydet(db, id, zaman_iso, kuyruga_ekle=(id not in calisanlar))
    return JSONResponse(status_code=202, content={"durum": "kuyrukta"})


@router.post("/tarama/sifirla")
async def tarama_sifirla(request: Request, d: dict = Body(default={})):
    """Geri sayımı şimdiden, seçili aralıkla yeniden başlat (15.11). `{"kategoriler": ["gpu", …]}`; boş/eksik → hepsi.
    Şu an taranan kategoriye dokunulmaz (`atlanan`). Yanıt: güncel plan + `sifirlanan`, `atlanan`."""
    db = _db(request)
    kur(db)
    simdi = _simdi(request)
    bilinen = [k.id for k in kategoriler(db)]
    istenen = d.get("kategoriler") if isinstance(d, dict) else None
    if istenen is None or istenen == []:
        istenen = bilinen
    if not isinstance(istenen, list) or not all(isinstance(x, str) for x in istenen):
        return JSONResponse(status_code=422, content={"hata": 'kategoriler: ["gpu", ...] listesi olmalı', "ayrinti": None})
    bilinmeyen = [x for x in istenen if x not in bilinen]
    if bilinmeyen:
        raise HTTPException(status_code=404, detail=f"bilinmeyen kategori: {', '.join(bilinmeyen)}")
    calisanlar, _ = aktif_durumlar_coz(durum_oku(db), simdi=simdi)
    zaman_iso = iso_bicim(simdi) or simdi.isoformat(timespec="seconds")
    sifirlanan, atlanan = [], []
    for kid in istenen:
        if kid in calisanlar:
            atlanan.append(kid)
        else:
            sayac_sifirla(db, kid, zaman_iso)
            sifirlanan.append(kid)
    plan = _guncel_plan(db, simdi)
    plan["sifirlanan"] = sifirlanan
    plan["atlanan"] = atlanan
    return plan


# ---------------------------------------------------------------- hızlı HTTP turu (15.11)
@router.get("/tarama/http")
async def http_turu(request: Request) -> dict:
    """Tarayıcısız siteler (akakce, vatan…; uçuşta Google) için ayrı sıklık: ayarlar + son/sonraki tur + site listesi."""
    from .zamanlayici import hizli
    return hizli.plan_ozeti(_db(request), _simdi(request))


@router.put("/tarama/http")
async def http_turu_ayar(request: Request, d: dict = Body(...)) -> dict:
    """{"etkin"?, "aralik_dk"? (5,10,15,20,30,45,60), "ucus"?, "tarayicida_atla"?}"""
    from .tarayici.gorunurluk import ayar_yaz
    from .tarayici.paralel_ayar import dogrula
    from .zamanlayici import hizli
    eslem = {"etkin": "http_etkin", "aralik_dk": "http_aralik_dk", "ucus": "http_ucus",
             "tarayicida_atla": "http_tarayicida_atla", "tarayici_otomatik": "tarayici_otomatik"}
    bilinmeyen = set(d) - set(eslem)
    degisen = {eslem[k]: v for k, v in d.items() if k in eslem}
    hatalar = [h for h in (dogrula(k, v) for k, v in degisen.items()) if h]
    if bilinmeyen or not degisen or hatalar:
        raise HTTPException(422, "; ".join(hatalar) or f"alanlar: {', '.join(eslem)}")
    ayar_yaz(**degisen)
    return hizli.plan_ozeti(_db(request), _simdi(request))


@router.post("/tarama/http/simdi")
async def http_turu_simdi(request: Request) -> JSONResponse:
    """Hızlı turu sıraya al (zamanlayıcı 15 sn içinde başlatır). Zaten çalışıyorsa 429."""
    from .zamanlayici import hizli
    from .zamanlayici.plan import iso_bicim
    db = _db(request)
    if hizli.calisiyor_mu(hizli.durum_oku(db)):
        return JSONResponse(status_code=429, content={"hata": "hızlı tur zaten çalışıyor", "ayrinti": None})
    hizli.manuel_iste(db, iso_bicim(_simdi(request)))
    return JSONResponse(status_code=202, content={"durum": "kuyrukta"})


@router.post("/tarama/http/sifirla")
async def http_turu_sifirla(request: Request) -> dict:
    """Geri sayımı şimdiden yeniden başlat (aralık değişince)."""
    from .zamanlayici import hizli
    from .zamanlayici.plan import iso_bicim
    db = _db(request)
    hizli.sayac_sifirla(db, iso_bicim(_simdi(request)))
    return hizli.plan_ozeti(db, _simdi(request))


@router.put("/tarama/site/{ad}/tur")
async def site_tur_degistir(request: Request, ad: str, d: dict = Body(...)) -> dict:
    """{"tur": "http" | "tarayici"} — siteyi Hızlı HTTP turuna ya da Tarayıcı turuna taşı (hata sayacı sıfırlanır)."""
    from .zamanlayici import hizli, tur_atama
    from .zamanlayici.plan import iso_bicim
    db = _db(request)
    tur = d.get("tur")
    try:
        tur_atama.ata(db, ad, tur, "elle (arayüz)", iso_bicim(_simdi(request)))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return hizli.plan_ozeti(db, _simdi(request))
