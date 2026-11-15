"""Tarayıcı izleme HTTP uç noktaları (`gorevler/TARAYICI_IZLEME_SOZLESME.md`).

Ağır işler (CDP çağrıları, dosya okuma) `asyncio.to_thread` ile iş parçacığına taşınır; çekirdek döngüsü kilitlenmez.
`api.py` bu router'ı include eder (orkestrasyon: Claude).
"""
import asyncio

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response

from .tarayici import dogrulama, izleme

router = APIRouter()


def _motor(ad: str) -> dict | None:
    return next((m for m in izleme.MOTORLAR if m["ad"] == ad), None)


@router.get("/tarayicilar")
async def tarayicilar() -> dict:
    """Motor kartları: kurulu/durum/renk/sekmeler/iş özeti/bot bilgisi."""
    return await asyncio.to_thread(izleme.durum)


@router.get("/tarayicilar/{ad}/goruntu/{sekme_id}")
async def tarayici_goruntu(ad: str, sekme_id: str, genislik: int = Query(480, ge=1)) -> Response:
    """Sekmenin JPEG ekran görüntüsü. `genislik` arayüz içindir; sayfayı bozmamak için Emulation KULLANILMAZ."""
    motor = _motor(ad)
    if motor is None:
        raise HTTPException(status_code=404, detail="motor bulunamadı")
    if not motor.get("cdp_port"):
        raise HTTPException(status_code=409, detail="bu motorun CDP ucu yok")
    try:
        veri = await asyncio.to_thread(izleme.ekran_goruntusu, motor["cdp_port"], sekme_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc) or "sekme bulunamadı") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="ekran görüntüsü zaman aşımı") from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=409, detail="CDP kapalı") from exc
    return Response(content=veri, media_type="image/jpeg")


@router.post("/tarayicilar/dogrulama")
async def dogrulama_iste(g: dict = Body(...)) -> dict:
    """Kullanıcı elle doğrulama isteyecek: süre yazılır, sekme bulunursa pencere öne getirilir (CAPTCHA çözülmez)."""
    site = str((g or {}).get("site") or "").strip()
    if not site:
        raise HTTPException(status_code=422, detail="site boş")
    try:
        sure = float(g.get("sure_sn") or dogrulama.VARSAYILAN_SURE_SN)
    except (TypeError, ValueError):
        sure = dogrulama.VARSAYILAN_SURE_SN
    bitis = await asyncio.to_thread(dogrulama.istek_yaz, site, sure)

    istenen_ad = g.get("ad")
    istenen_sekme = g.get("sekme_id") or None
    bulunan_sekme = None
    gosterildi = False
    for motor in izleme.MOTORLAR:
        if not motor.get("cdp_port") or (istenen_ad and motor["ad"] != istenen_ad):
            continue
        sekmeler = await asyncio.to_thread(izleme.cdp_sekmeleri, motor["cdp_port"])
        if not sekmeler:
            continue
        hedef = next((s for s in sekmeler if istenen_sekme and s.get("id") == istenen_sekme), None)
        if hedef is None:
            hedef = next((s for s in sekmeler
                          if izleme._site_eslesir(s.get("site"), site)), None)
        if hedef is None:
            continue
        bulunan_sekme = hedef.get("id")
        gosterildi = await asyncio.to_thread(izleme.pencereyi_goster, motor["cdp_port"], bulunan_sekme)
        break
    return {"tamam": True, "bitis": bitis, "sekme_id": bulunan_sekme, "gosterildi": gosterildi}


@router.delete("/tarayicilar/dogrulama/{site}")
async def dogrulama_sil(site: str) -> dict:
    """Kullanıcı "✓ Geçtim" dedi ya da vazgeçti: bekleme isteği silinir."""
    silindi = await asyncio.to_thread(dogrulama.istek_sil, site)
    return {"tamam": True, "silindi": bool(silindi)}
