"""FastAPI uygulama fabrikası ve uç noktaları (yalnız 127.0.0.1).

Not: bu dosyada `from __future__ import annotations` KULLANILMAZ; FastAPI bağımlılık
ipuçlarını çalışma anında çözer.
"""
import asyncio
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .ayar import Ayar, hazirla
from .db import VeriTabani, surum_oku
from .modeller import (Olay, OnaylaIstek, TanimlaIstek, TanimlaYanit, TaramaIstek,
                       TaramaYanit, Teklif, UrunKimligi, FiyatBandi)
from .olaylar import YayinVeriyolu
from . import eski, pano as pano_mod
from .siteler import SITELER
from .seyahat import plan_depo
from .tanimlama import tanimla as tanimla_calistir, kurallar_uret

_TANIMLA_SURE_SN = 30 * 60        # onay için istek önbelleği ömrü


def _akakce_getir(url: str) -> str:
    """Katalog araması için K0 (HTTP). Akakçe bot korumasız; K2 gerekmez."""
    from .tarayici import Tarayici
    durum, html = Tarayici().http_getir(url, site="akakce")
    return html if durum == 200 else ""


def _db(r: Request) -> VeriTabani:
    return r.app.state.db


def _bus(r: Request) -> YayinVeriyolu:
    return r.app.state.bus


def uygulama_olustur(ayar: Ayar | None = None, *, seyahat_isit: bool = False) -> FastAPI:
    """`seyahat_isit`: açılışta /seyahat önbelleğini arkada doldur (servis; testlerde kapalı)."""
    ayar = hazirla(ayar)
    baslangic = time.time()

    seyahat_onbellek: dict = {}

    async def _seyahat_hesapla(app_: FastAPI) -> dict:
        plan = plan_depo.yukle(app_.state.plan_yolu)
        v = await asyncio.to_thread(eski.seyahat, plan_sozlugu=plan, v=app_.state.db)
        seyahat_onbellek.update(z=time.time(), v=v)
        return v

    @asynccontextmanager
    async def yasam(app: FastAPI):
        app.state.db = VeriTabani(ayar.db_yolu)
        app.state.bus = YayinVeriyolu(app.state.db)
        app.state.tanimlamalar = {}
        if not hasattr(app.state, "plan_yolu"):
            app.state.plan_yolu = plan_depo.plan_yolu()
        if not hasattr(app.state, "eski_plan_yolu"):
            app.state.eski_plan_yolu = plan_depo.ESKI_PLAN
        if not hasattr(app.state, "katalog_getir"):
            app.state.katalog_getir = _akakce_getir
        if seyahat_isit:                       # servis: Python uyarıları kayıt defterine
            from . import gunluk
            gunluk.python_logu_bagla()
        if seyahat_isit:                       # ilk /seyahat isteği soğuk hesabı beklemesin (15.11)
            seyahat_onbellek["is"] = asyncio.create_task(_seyahat_hesapla(app))
        try:
            yield
        finally:
            app.state.db.kapat()

    app = FastAPI(title="kazi-cekirdek", version=__version__, lifespan=yasam)
    from .api_tarama import router as tarama_router          # A6: kategori bazlı tarama zamanlayıcısı
    app.include_router(tarama_router)
    from .api_tarayici import router as tarayici_router      # 15.11: sağ panel tarayıcı izleme + elle doğrulama
    app.include_router(tarayici_router)

    @app.exception_handler(HTTPException)
    async def _http_hata(_r: Request, e: HTTPException):
        return JSONResponse(status_code=e.status_code, content={"hata": str(e.detail), "ayrinti": None})

    @app.exception_handler(RequestValidationError)
    async def _dogrulama_hata(_r: Request, e: RequestValidationError):
        return JSONResponse(status_code=422, content={"hata": "geçersiz istek", "ayrinti": e.errors()})

    @app.get("/saglik")
    async def saglik(r: Request) -> dict:
        return {"surum": surum_oku(_db(r).baglanti), "calisma_suresi_sn": int(time.time() - baslangic),
                "db_yolu": str(ayar.db_yolu), "cekirdek_surumu": __version__}

    @app.get("/siteler")
    async def siteler(r: Request) -> list[dict]:
        try:
            son = eski.site_24s()
        except Exception:  # eski sistem yoksa 24 saat özeti boş
            son = {}
        return pano_mod.siteler(_db(r), son)

    @app.put("/siteler/{ad}/etkin")
    async def site_etkin(ad: str, r: Request, d: dict = Body(...)) -> dict:
        """{"etkin": false} → site aramalardan çıkar (taranmaz, toplayıcı üzerinden gelen teklifleri elenir, kayıtlı
        teklifleri ekranlara/raporlara girmez); true → geri gelir. Yanıt: güncel site satırı."""
        e = d.get("etkin")
        if not isinstance(e, bool):
            raise HTTPException(422, "etkin: true|false")
        bilinen = {s.ad for s in _db(r).siteler_listele()} | set(SITELER)
        if ad not in bilinen:
            raise HTTPException(404, f"bilinmeyen site: {ad}")
        _db(r).site_etkin_yaz(ad, e)
        await _bus(r).yayinla(Olay(tur="site_degisti", mesaj=f"{ad} {'aramaya alındı' if e else 'aramadan çıkarıldı'}",
                                   veri={"site": ad, "etkin": e}))
        return next(x for x in pano_mod.siteler(_db(r)) if x["ad"] == ad)

    @app.get("/pano")
    async def pano(r: Request) -> dict:
        return pano_mod.pano(_db(r))

    # ---- takip listesi: çıkar / geri al, alarm fiyatı (14.11) ----
    def _simdi_iso() -> str:
        from datetime import datetime
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def _urunler_var(r: Request, ids: list[str]) -> None:
        bilinmeyen = set(ids) - {u.id for u in _db(r).urunler_listele()}
        if not ids or bilinmeyen:
            raise HTTPException(404, f"bilinmeyen ürün: {sorted(bilinmeyen) or 'boş liste'}")

    @app.post("/takip/cikar")
    async def takipten_cikar(r: Request, d: dict = Body(...)) -> dict:
        """{"urun_idler": [...]} → takip listesinden düşer (geri alınabilir; eski aktarım geri getirmez)."""
        ids = list(d.get("urun_idler") or [])
        _urunler_var(r, ids)
        # Σ seçimine DOKUNMA: takip dışı ürün panoda zaten toplama girmez; geri alınınca eski seçim aynen döner
        _db(r).takipten_cikar(ids, _simdi_iso())
        await _bus(r).yayinla(Olay(tur="takip_degisti", mesaj=f"{len(ids)} ürün takipten çıkarıldı", veri={"cikan": ids}))
        return pano_mod.pano(_db(r))

    @app.post("/takip/geri_al")
    async def takibe_al(r: Request, d: dict = Body(...)) -> dict:
        ids = list(d.get("urun_idler") or [])
        _urunler_var(r, ids)
        _db(r).takibe_al(ids)
        await _bus(r).yayinla(Olay(tur="takip_degisti", mesaj=f"{len(ids)} ürün yeniden takipte", veri={"giren": ids}))
        return pano_mod.pano(_db(r))

    @app.put("/urunler/{id}/alarm")
    async def alarm_fiyati(id: str, r: Request, d: dict = Body(...)) -> dict:
        """{"alarm_fiyat": 110000 | null}. Kaydedilince hemen denetlenir: fiyat zaten altındaysa Telegram'a gider."""
        _urunler_var(r, [id])
        f = d.get("alarm_fiyat")
        if f is not None and (not isinstance(f, (int, float)) or isinstance(f, bool) or f <= 0):
            raise HTTPException(422, "alarm_fiyat: pozitif sayı ya da null")
        _db(r).alarm_yaz(id, f, _simdi_iso())
        from .bildirim import alarm_denetle
        gonderilen = await asyncio.to_thread(alarm_denetle, _db(r)) if f is not None else []
        return {"urun_id": id, "alarm_fiyat": f, "hemen_gonderilen": gonderilen}

    # ---- Telegram ayarları (14.11) ----
    @app.get("/ayarlar/telegram")
    async def telegram_ayari() -> dict:
        from .bildirim import ayar_ozeti
        return ayar_ozeti()

    @app.put("/ayarlar/telegram")
    async def telegram_ayari_yaz(d: dict = Body(...)) -> dict:
        """{"token"?, "chat_id"?, "etkin"?} — token boşsa mevcut korunur; yanıtta token maskeli."""
        from .bildirim import ayar_yaz
        alanlar = {}
        if "token" in d:
            if d["token"] and not isinstance(d["token"], str):
                raise HTTPException(422, "token metin olmalı")
            alanlar["token"] = (d["token"] or "").strip()
        if "chat_id" in d:
            try:
                alanlar["chat_id"] = int(d["chat_id"])
            except (TypeError, ValueError):
                raise HTTPException(422, "chat_id sayı olmalı")
        if "etkin" in d:
            alanlar["etkin"] = bool(d["etkin"])
        if "rapor_aralik_dk" in d:
            from .bildirim.rapor import ARALIK_SECENEKLERI
            if d["rapor_aralik_dk"] not in ARALIK_SECENEKLERI:
                raise HTTPException(422, f"rapor_aralik_dk: {list(ARALIK_SECENEKLERI)} (0 = kapalı)")
            alanlar["rapor_aralik_dk"] = d["rapor_aralik_dk"]
        if "sessiz_saatler" in d:
            s = d["sessiz_saatler"]
            from datetime import time as _t
            try:
                assert isinstance(s, list) and len(s) == 2 and all(_t.fromisoformat(x) for x in s)
            except (AssertionError, ValueError, TypeError):
                raise HTTPException(422, 'sessiz_saatler: ["01:00", "07:00"]')
            alanlar["sessiz_saatler"] = s
        return ayar_yaz(**alanlar)

    @app.post("/ayarlar/telegram/rapor")
    async def telegram_rapor_simdi(r: Request) -> dict:
        """Özeti hemen gönder (sıklık/sessiz saat beklemeden)."""
        from .bildirim.rapor import rapor_gonder_gerekirse
        return {"gonderildi": await asyncio.to_thread(rapor_gonder_gerekirse, _db(r), None, None, True)}

    @app.post("/ayarlar/telegram/test")
    async def telegram_test() -> dict:
        from .bildirim import gonder
        ok = await asyncio.to_thread(gonder, "✅ <b>Asistan</b> — test mesajı. Alarm bildirimleri buraya gelecek.")
        return {"gonderildi": ok}

    @app.put("/toplam")
    async def toplam_secimi(r: Request, d: dict = Body(...)) -> dict:
        """Takip Listesi kutucukları: {"secimler": {"urun_id": true|false, ...}} → güncel /pano sistem_toplami."""
        s = d.get("secimler")
        if not isinstance(s, dict) or not all(isinstance(x, bool) for x in s.values()):
            raise HTTPException(422, 'secimler: {"urun_id": true|false}')
        bilinen = {u.id for u in _db(r).urunler_listele()}
        if set(s) - bilinen:
            raise HTTPException(404, f"bilinmeyen ürün: {sorted(set(s) - bilinen)}")
        _db(r).toplam_sec(s)
        return pano_mod.pano(_db(r))["sistem_toplami"]

    @app.get("/urunler/{id}/detay")
    async def urun_detay(id: str, r: Request) -> dict:
        d = pano_mod.detay(_db(r), id)
        if d is None:
            raise HTTPException(404, "Ürün bulunamadı")
        return d

    @app.get("/seyahat")
    async def seyahat_ekrani(r: Request) -> dict:
        """Önbellek 120 sn. Süresi dolmuşsa ESKİ sonuç hemen döner, yenisi arkada hesaplanır (15.11: hesap uzayınca
        arayüz 8 sn'de zaman aşımına düşüp "çekirdek bağlantı uyarısı" gösteriyordu). İlk istekte (önbellek boş) beklenir."""
        if "v" not in seyahat_onbellek:
            is_ = seyahat_onbellek.get("is")
            if is_ is not None and not is_.done():        # açılış ısıtması sürüyorsa onu bekle
                return await asyncio.shield(is_)
            return await _seyahat_hesapla(r.app)
        if time.time() - seyahat_onbellek.get("z", 0) > 120:
            is_ = seyahat_onbellek.get("is")
            if is_ is None or is_.done():
                seyahat_onbellek["is"] = asyncio.create_task(_seyahat_hesapla(r.app))
        return seyahat_onbellek["v"]

    # ---- kayıt defteri (15.11): tüm tarayıcı/site/uçuş/zamanlayıcı/alarm kayıtları ----
    @app.get("/kayitlar")
    async def kayitlar(limit: int = Query(300), once_id: int | None = Query(None), sonra_id: int | None = Query(None),
                       seviye: str | None = Query(None), kaynak: str | None = Query(None),
                       site: str | None = Query(None), kategori: str | None = Query(None),
                       ara: str | None = Query(None)) -> dict:
        from . import gunluk
        return await asyncio.to_thread(gunluk.oku, limit=limit, once_id=once_id, sonra_id=sonra_id, seviye=seviye,
                                       kaynak=kaynak, site=site, kategori=kategori, ara=ara)

    @app.get("/kayitlar/ozet")
    async def kayit_ozeti(saat: int = Query(24)) -> dict:
        from . import gunluk
        return await asyncio.to_thread(gunluk.ozet, saat)

    # ---- seyahat planı (arayüzden düzenlenir; SARTNAME §13.1–13.2) ----
    @app.get("/seyahat/plan")
    async def plan_getir(r: Request) -> dict:
        d = plan_depo.yukle(r.app.state.plan_yolu)
        return {"plan": d, "onizleme": plan_depo.onizle(d), "kayitli": r.app.state.plan_yolu.exists()}

    @app.post("/seyahat/plan/onizle")
    async def plan_onizle(d: dict = Body(...)) -> dict:
        return plan_depo.onizle(d)

    @app.put("/seyahat/plan")
    async def plan_kaydet(r: Request, d: dict = Body(...), eski_tarayiciya: bool = Query(True)) -> dict:
        hatalar = plan_depo.dogrula(d)
        if hatalar:
            return JSONResponse(status_code=422, content={"hata": "plan geçersiz", "ayrinti": hatalar})
        kayit = plan_depo.kaydet(d, r.app.state.plan_yolu)
        aktarim = None
        if eski_tarayiciya and r.app.state.eski_plan_yolu is not None:
            try:
                aktarim = plan_depo.eski_plana_aktar(kayit, r.app.state.eski_plan_yolu)
            except Exception as e:                      # eski sistem yoksa plan yine kaydedilir
                aktarim = {"hata": f"{type(e).__name__}: {e}"}
        seyahat_onbellek.clear()
        await _bus(r).yayinla(Olay(tur="plan_guncellendi", mesaj="Seyahat planı kaydedildi",
                                   veri={"bacaklar": [b["id"] for b in kayit["bacaklar"]]}))
        return {"plan": kayit, "onizleme": plan_depo.onizle(kayit), "eski_tarayici": aktarim}

    # ---- tarama ayarları: Brave göster/gizle (14.11) ----
    def _brave_uygula(gorunur: bool | None) -> list[dict]:
        """Çalışan Brave'lere (eski sistem 9333, kazi 9334) ayarı anında uygula; bağlantıyı kapatır, Brave'i kapatmaz."""
        import urllib.request
        from playwright.sync_api import sync_playwright
        from .tarayici.gorunurluk import pencereleri_ayarla
        sonuc = []
        for ad, port in (("eski sistem", 9333), ("kazi", 9334)):
            d = {"ad": ad, "port": port, "calisiyor": False, "pencereler": []}
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
                d["calisiyor"] = True
            except Exception:
                sonuc.append(d)
                continue
            try:
                with sync_playwright() as p:
                    b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=8000)
                    d["pencereler"] = pencereleri_ayarla(b, gorunur)
            except Exception as e:
                d["hata"] = f"{type(e).__name__}: {e}"[:200]
            sonuc.append(d)
        return sonuc

    @app.get("/ayarlar/tarama")
    async def tarama_ayari_getir() -> dict:
        from .tarayici.gorunurluk import ayar_oku, gorunur_mu
        from .tarayici.profil import gizlilik_ayarlari
        from .tarayici.paralel_ayar import paralel_ayarlar, sistem_bilgisi
        a = ayar_oku()
        return {**a, "brave_gorunur": gorunur_mu(), **gizlilik_ayarlari(a), **paralel_ayarlar(a),
                "sistem": sistem_bilgisi()}

    @app.put("/ayarlar/tarama")
    async def tarama_ayari_yaz(r: Request, d: dict = Body(...)) -> dict:
        """{"brave_gorunur"?, "analiz_kapali"?, "bildirim_engelle"?, "popup_engelle"?, "cerez_reddet"?,
        "reklam_engelle"?, "gorsel_engelle"? (bool), "sekme_sayisi"? (2–10), "kategori_esz"? (1–3)}."""
        from .tarayici.gorunurluk import ayar_yaz
        from .tarayici.paralel_ayar import VARSAYILAN as PARALEL, dogrula, paralel_ayarlar, sistem_bilgisi
        from .tarayici.profil import ANAHTARLAR, gizlilik_ayarlari
        izinli = ("brave_gorunur", *ANAHTARLAR, *PARALEL)
        degisen = {k: v for k, v in d.items() if k in izinli}
        hatalar = [h for h in (dogrula(k, v) for k, v in degisen.items() if k in PARALEL) if h]
        hatalar += [f"{k}: true | false" for k, v in degisen.items() if k not in PARALEL and not isinstance(v, bool)]
        if not degisen or hatalar or set(d) - set(izinli):
            raise HTTPException(422, "; ".join(hatalar) or f"alanlar: {', '.join(izinli)}")
        yeni = ayar_yaz(**degisen)
        brave: list = []
        if "brave_gorunur" in degisen:
            brave = await asyncio.to_thread(_brave_uygula, degisen["brave_gorunur"])
            await _bus(r).yayinla(Olay(tur="ayar_degisti", mesaj="Brave " + ("görünür" if degisen["brave_gorunur"] else "gizli (arka plan)"),
                                       veri={"brave_gorunur": degisen["brave_gorunur"]}))
        if set(degisen) & set(ANAHTARLAR):
            await _bus(r).yayinla(Olay(tur="ayar_degisti", mesaj="Tarayıcı gizlilik ayarları güncellendi",
                                       veri={k: v for k, v in degisen.items() if k in ANAHTARLAR}))
        if set(degisen) & set(PARALEL):
            await _bus(r).yayinla(Olay(tur="ayar_degisti", mesaj="Paralel tarama ayarları güncellendi",
                                       veri={k: v for k, v in degisen.items() if k in PARALEL}))
        return {**yeni, **gizlilik_ayarlari(yeni), **paralel_ayarlar(yeni), "sistem": sistem_bilgisi(), "brave": brave}

    @app.get("/yerler")
    async def yer_ara(q: str = Query(..., min_length=1), azami: int = Query(12, ge=1, le=50)) -> list[dict]:
        return plan_depo.yer_ara(q, azami)

    @app.get("/ulkeler")
    async def ulkeler() -> dict:
        return plan_depo.veri("ulkeler.json")

    @app.get("/urunler", response_model=list[UrunKimligi])
    async def urunler(r: Request):
        return _db(r).urunler_listele()

    @app.get("/urunler/{id}", response_model=UrunKimligi)
    async def urun(id: str, r: Request):
        u = _db(r).urun_getir(id)
        if not u:
            raise HTTPException(404, "Ürün bulunamadı")
        return u

    @app.get("/urunler/{id}/teklifler", response_model=list[Teklif])
    async def teklifler(id: str, r: Request, gecmis: int = Query(0, ge=0, le=1)):
        if not _db(r).urun_getir(id):
            raise HTTPException(404, "Ürün bulunamadı")
        return _db(r).teklifler(id, yalniz_son=(gecmis == 0))

    @app.post("/tanimla", response_model=TanimlaYanit)
    async def tanimla(istek: TanimlaIstek, r: Request):
        sorgu, adaylar = await asyncio.to_thread(tanimla_calistir, istek.metin, str(istek.tur),
                                                 r.app.state.katalog_getir)
        iid = uuid.uuid4().hex[:12]
        onb = r.app.state.tanimlamalar
        simdi = time.time()
        for k in [k for k, v in onb.items() if simdi - v[0] > _TANIMLA_SURE_SN]:
            del onb[k]
        onb[iid] = (simdi, sorgu, adaylar)
        return TanimlaYanit(istek_id=iid, adaylar=adaylar, sorgu=sorgu.sozluk())

    @app.post("/tanimla/{istek_id}/onayla", response_model=UrunKimligi)
    async def onayla(istek_id: str, istek: OnaylaIstek, r: Request):
        kayit = r.app.state.tanimlamalar.get(istek_id)
        secilen = []
        kurallar = istek.kurallar
        bant = None
        if kayit and istek.aday_idler:
            _, sorgu, adaylar = kayit
            secilen = [a for a in adaylar if a.id in set(istek.aday_idler)]
            if not secilen and not istek.kanonik_ad:
                raise HTTPException(400, "aday_idler bu tanımlama isteğinde yok")
            if kurallar is None and secilen:
                kurallar = kurallar_uret(secilen, sorgu)
            if kurallar and (kurallar.get("min_fiyat") or kurallar.get("max_fiyat")):
                bant = FiyatBandi(alt=kurallar.get("min_fiyat"), ust=kurallar.get("max_fiyat"))
        elif istek.aday_idler and not istek.kanonik_ad and kurallar is None:
            raise HTTPException(404, "tanımlama isteği bulunamadı ya da süresi doldu (yeniden /tanimla)")
        ad = istek.kanonik_ad or (secilen[0].baslik if len(secilen) == 1 else None) \
            or (f"{sorgu.marka or ''} {sorgu.model or sorgu.kategori or ''} ({len(secilen)} model)".strip() if secilen else None)
        if not ad:
            raise HTTPException(400, "kanonik_ad ya da aday_idler gerekli")
        mpn = istek.mpn or (secilen[0].mpn if len(secilen) == 1 else None)
        uid = _db(r).urun_ekle(UrunKimligi(kanonik_ad=ad, mpn=mpn, ean=istek.ean,
                                           eslesme_kurallari=kurallar or {}, fiyat_bandi=bant))
        u = _db(r).urun_getir(uid)
        await _bus(r).yayinla(Olay(tur="urun_tanimlandi", urun_id=uid, mesaj=f"Ürün tanımlandı: {ad}",
                                   veri={"istek_id": istek_id}))
        return u

    @app.post("/tara", response_model=TaramaYanit)
    async def tara(istek: TaramaIstek, r: Request):
        if not _db(r).urun_getir(istek.urun_id):
            raise HTTPException(404, "Ürün bulunamadı")
        gid = "gorev-" + uuid.uuid4().hex[:12]
        # A2/A4 dolduracak: gerçek tarama kuyruğu
        await _bus(r).yayinla(Olay(tur="tarama_basladi", urun_id=istek.urun_id, gorev_id=gid,
                                   mesaj="Tarama kuyruğa alındı"))
        return TaramaYanit(gorev_id=gid)

    @app.websocket("/olaylar")
    async def olaylar(ws: WebSocket):
        await ws.accept()
        bus: YayinVeriyolu = ws.app.state.bus
        kuyruk = bus.abone_ol()
        try:
            while True:
                olay = await kuyruk.get()
                await ws.send_json(olay.model_dump(mode="json"))
        except WebSocketDisconnect:
            pass
        finally:
            bus.abone_cikar(kuyruk)

    return app
