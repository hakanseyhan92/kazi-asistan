"""Hızlı HTTP turu (zamanlayici/hizli.py + /tarama/http uçları) — AĞ YOK, tarayıcı YOK."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from kazi.api_tarama import router
from kazi.db import VeriTabani
from kazi.modeller import TZ_ISTANBUL, Katman, Site, UrunKimligi
from kazi.tarayici import gorunurluk
from kazi.tarayici.cekirdek import BotEngeli
from kazi.zamanlayici import hizli
from kazi.zamanlayici import plan as plan_mod

T0 = datetime(2026, 11, 15, 12, 0, 0, tzinfo=TZ_ISTANBUL)


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from kazi.tarayici import cekirdek as tc
    monkeypatch.setattr(tc, "DURUM_DOSYA", tmp_path / "durum.json")     # kullanıcının gerçek site durumu okunmasın
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    gorunurluk.ayar_yaz(brave_gorunur=False)
    v = VeriTabani(tmp_path / "kazi.db")
    v.urun_ekle(UrunKimligi(id="gpu_rtx5080", kanonik_ad="RTX 5080", eslesme_kurallari={"kategori": "gpu"}))
    v.site_kaydet(Site(ad="akakce", katman_tercihi=Katman.K0))
    v.site_kaydet(Site(ad="vatan", katman_tercihi=Katman.K0))
    v.site_kaydet(Site(ad="pazarama", katman_tercihi=Katman.K0, etkin=False))   # kapalı site → listede yok
    v.site_kaydet(Site(ad="trendyol", katman_tercihi=Katman.K2))                # tarayıcılı → listede yok
    v.site_kaydet(Site(ad="google", katman_tercihi=Katman.K0))                  # uçuş kaynağı
    v.site_kaydet(Site(ad="kayak", katman_tercihi=Katman.K2))
    try:
        yield v
    finally:
        v.kapat()


def test_siteler_yalniz_etkin_k0(db):
    assert hizli.urun_siteleri(db) == ["akakce", "vatan"]
    assert hizli.ucus_kaynaklari(db) == ["google"]


def test_vade_hesabi(db):
    d = hizli.durum_oku(db)
    assert hizli.vadesi_geldi_mi(d, 10, T0)                              # hiç taranmadı → hemen
    hizli._yaz(db, son_baslangic=plan_mod.iso_bicim(T0))
    assert hizli.calisiyor_mu(hizli.durum_oku(db))
    assert not hizli.vadesi_geldi_mi(hizli.durum_oku(db), 10, T0 + timedelta(hours=1))
    hizli._yaz(db, son_bitis=plan_mod.iso_bicim(T0 + timedelta(minutes=1)))
    d = hizli.durum_oku(db)
    assert not hizli.calisiyor_mu(d)
    assert hizli.sonraki(d, 10) == T0 + timedelta(minutes=11)            # aralık BİTİŞTEN sayılır
    assert not hizli.vadesi_geldi_mi(d, 10, T0 + timedelta(minutes=10))
    assert hizli.vadesi_geldi_mi(d, 10, T0 + timedelta(minutes=11))
    hizli.manuel_iste(db, plan_mod.iso_bicim(T0 + timedelta(minutes=2)))
    assert hizli.vadesi_geldi_mi(hizli.durum_oku(db), 10, T0 + timedelta(minutes=2))
    hizli.sayac_sifirla(db, plan_mod.iso_bicim(T0 + timedelta(minutes=5)))
    assert hizli.sonraki(hizli.durum_oku(db), 10) == T0 + timedelta(minutes=15)


def test_http_tarayici_tarayici_acmaz(db):
    t = hizli.http_fabrikasi(db)(0)
    try:
        assert t.kademe.k1 is None
        assert t.kademe._denenecek_katmanlar("akakce") == [Katman.K0]
        with pytest.raises(BotEngeli):
            t._brave_baglan()
        with pytest.raises(BotEngeli):
            t.kademe._k2_dene("https://x", "akakce")
        assert t.gunluk_adi == "HTTP (K0)"
    finally:
        t.kapat()


def test_fabrika_bekleme_durumu_ortak(db):
    fab = hizli.http_fabrikasi(db)
    a, b = fab(0), fab(1)
    try:
        assert a._son_istek is b._son_istek and a.oto_hiz is b.oto_hiz
    finally:
        a.kapat()
        b.kapat()


def test_tara_ozet_ve_durum(db):
    cagri = {}

    def sahte_urun(v, t, **kw):
        cagri["urun"] = kw
        return SimpleNamespace(teklifler={"gpu_rtx5080": [1, 2, 3]}, kapsam={"akakce": {}, "vatan": {"hata": "x"}})

    def sahte_ucus(v, t, **kw):
        cagri["ucus"] = kw
        return SimpleNamespace(segmentler={"ist_tyo": [1, 2]}, kapsam={})

    saat = [T0, T0, T0 + timedelta(seconds=30)]          # başlangıç, uçuş rotasyonu, bitiş
    ozet = hizli.tara(db, simdi_fn=lambda: saat.pop(0) if len(saat) > 1 else saat[0], log=lambda m: None, urun_tara_fn=sahte_urun,
                      ucus_tara_fn=sahte_ucus, alarm_fn=lambda v: None)
    assert ozet["teklif"] == 3 and ozet["ucus"] == 2 and ozet["hata"] == 1
    assert cagri["urun"]["siteler"] == ["akakce", "vatan"]
    assert cagri["ucus"]["kaynaklar"] == ["google"]
    d = hizli.durum_oku(db)
    assert d["son_baslangic"] == plan_mod.iso_bicim(T0)
    assert d["son_bitis"] == plan_mod.iso_bicim(T0 + timedelta(seconds=30))
    assert d["son_sonuc"]["teklif"] == 3
    assert not hizli.calisiyor_mu(d)


def test_tara_hata_da_kapanir(db):
    def patlayan(v, t, **kw):
        raise RuntimeError("ağ yok")
    ozet = hizli.tara(db, simdi_fn=lambda: T0, log=lambda m: None, urun_tara_fn=patlayan, alarm_fn=lambda v: None)
    assert ozet["hata"] >= 1
    assert not hizli.calisiyor_mu(hizli.durum_oku(db))                   # yarım "çalışıyor" kalmaz


def test_dongu_yarim_kaydi_kapatir_ve_durur(db, tmp_path, monkeypatch):
    hizli._yaz(db, son_baslangic=plan_mod.iso_bicim(T0))                # elektrik kesintisi: bitiş yok
    gorunurluk.ayar_yaz(http_etkin=False)                                # döngü tur başlatmasın
    dur = threading.Event()
    th = threading.Thread(target=hizli.dongu, args=(tmp_path / "kazi.db", dur),
                          kwargs={"log": lambda m: None, "aralik_sn": 0.05, "simdi_fn": lambda: T0})
    th.start()
    import time
    time.sleep(0.3)
    dur.set()
    th.join(timeout=2)
    assert not th.is_alive()
    assert not hizli.calisiyor_mu(hizli.durum_oku(db))


# ------------------------------------------------------------------ API
@pytest.fixture
def istemci(db):
    app = FastAPI()
    app.state.db = db
    app.state.simdi_fn = lambda: T0
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def test_api_get(istemci):
    d = istemci.get("/tarama/http").json()
    assert d["etkin"] is True and d["aralik_dk"] == 10
    assert d["secenekler_dk"] == [5, 10, 15, 20, 30, 45, 60]
    assert d["urun_siteleri"] == ["akakce", "vatan"] and d["ucus_kaynaklari"] == ["google"]
    assert d["durum"] == "bekliyor" and d["sonraki"] is None and d["kalan_sn"] == 0


def test_api_put_dogrular(istemci):
    assert istemci.put("/tarama/http", json={"aralik_dk": 15}).json()["aralik_dk"] == 15
    assert istemci.put("/tarama/http", json={"aralik_dk": 7}).status_code == 422
    assert istemci.put("/tarama/http", json={"bilinmeyen": 1}).status_code == 422
    d = istemci.put("/tarama/http", json={"etkin": False}).json()
    assert d["etkin"] is False and d["durum"] == "kapali"


def test_api_simdi_ve_sifirla(istemci, db):
    assert istemci.post("/tarama/http/simdi").status_code == 202
    assert hizli.durum_oku(db)["manuel_istek_zamani"] == plan_mod.iso_bicim(T0)
    hizli._yaz(db, son_baslangic=plan_mod.iso_bicim(T0))
    assert istemci.post("/tarama/http/simdi").status_code == 429
    hizli._yaz(db, son_bitis=plan_mod.iso_bicim(T0))
    d = istemci.post("/tarama/http/sifirla").json()
    assert d["kalan_sn"] == 600


# ------------------------------------------------------------------ 15.11 canlı deneme düzeltmeleri
def test_brave_zorunlu_site_listeye_girmez(db, tmp_path):
    import json
    son = datetime.now(TZ_ISTANBUL).isoformat(timespec="seconds")
    (tmp_path / "durum.json").write_text(json.dumps({"vatan": {"http_ardisik_blok": 3, "son_http_blok": son}}))
    assert hizli.urun_siteleri(db) == ["akakce"]                       # cimri vakası: K0 görünür ama Brave'e zorlanır


def test_tara_engellileri_ozete_yazar(db):
    def sahte_urun(v, t, **kw):
        return SimpleNamespace(teklifler={}, kapsam={("gpu_rtx5080", "akakce"): {"tur": "ok"},
                                                     ("gpu_rtx5080", "vatan"): {"tur": "blok", "hata": "bot"}})
    gorunurluk.ayar_yaz(http_ucus=False)
    ozet = hizli.tara(db, simdi_fn=lambda: T0, log=lambda m: None, urun_tara_fn=sahte_urun, alarm_fn=lambda v: None)
    assert ozet["engelli"] == ["vatan"]
    assert hizli.son_tur_engelli(db) == {"vatan"}


def test_turda_iki_engelden_sonra_istek_atilmaz(db):
    t = hizli.http_fabrikasi(db)(0)
    try:
        t.hiz_sinirla("vatan")                                          # ilk istek serbest
        t.oto_hiz.yanit("vatan", 0.5, engel=True)
        t.oto_hiz.yanit("vatan", 0.5, engel=True)
        with pytest.raises(BotEngeli):
            t.hiz_sinirla("vatan")
        t.hiz_sinirla("akakce")                                         # başka site etkilenmez
    finally:
        t.kapat()


def test_oto_hiz_kendi_uykusunu_saymaz():
    from kazi.tarayici.oto_hiz import OtoHiz
    saat = {"t": 0.0}
    o = OtoHiz(alt_sn=2.0, ust_sn=60.0, baslangic_sn=2.5, zaman_fn=lambda: saat["t"],
               uyku_fn=lambda s: saat.__setitem__("t", saat["t"] + s), rastgele_fn=lambda a, b: 1.0)
    assert o.sayac("x") == (0, 0.0)
    o.bekle("x")
    o.bekle("x")
    n, u = o.sayac("x")
    assert n == 2 and u == pytest.approx(2.5)


def test_tek_engelden_toparlanan_site_engelli_sayilmaz(db):
    def sahte_urun(v, t, **kw):
        return SimpleNamespace(teklifler={}, kapsam={("a", "akakce"): {"tur": "ok"}, ("b", "akakce"): {"tur": "blok"},
                                                     ("a", "vatan"): {"tur": "blok"}})
    gorunurluk.ayar_yaz(http_ucus=False)
    ozet = hizli.tara(db, simdi_fn=lambda: T0, log=lambda m: None, urun_tara_fn=sahte_urun, alarm_fn=lambda v: None)
    assert ozet["engelli"] == ["vatan"]                                 # vatan: hiç başarı yok


def test_yalniz_tarayicili_arama_yapan_site_listeye_girmez(db):
    db.site_kaydet(Site(ad="cimri", katman_tercihi=Katman.K0))           # ürün sayfası HTTP'den geldiği için K0 görünür
    assert "cimri" not in hizli.urun_siteleri(db)


# ------------------------------------------------------------------ 15.11 tur ataması (Tarayıcı turu ↔ Hızlı HTTP turu)
def test_tur_atamasi_baslangic_ve_tarayici_turu(db):
    from kazi.zamanlayici import tur_atama
    a = tur_atama.atamalar(db)
    assert a["akakce"]["tur"] == "http" and a["trendyol"]["tur"] == "tarayici"
    assert a["google"]["tur"] == "http" and a["google"]["tip"] == "ucus" and a["kayak"]["tur"] == "tarayici"
    siteler, ek = hizli.tarayici_turu_urun(db)
    assert "akakce" not in siteler and "vatan" not in siteler and "trendyol" in siteler and ek == set()
    assert "google" not in hizli.tarayici_turu_ucus(db)
    gorunurluk.ayar_yaz(http_etkin=False)
    assert hizli.tarayici_turu_urun(db) == (None, set())                # HTTP turu kapalı → tarayıcı her şeyi tarar
    assert hizli.tarayici_turu_ucus(db) is None


def test_http_basarisiz_isler_tarayici_turuna_gider(db):
    import json
    hizli._yaz(db, son_sonuc=json.dumps({"basarisiz": [["gpu_rtx5080", "vatan"], ["cpu_x", "akakce"]],
                                          "basarisiz_ucus": ["google"]}))
    siteler, ek = hizli.tarayici_turu_urun(db, ["gpu_rtx5080"])
    assert ek == {("gpu_rtx5080", "vatan")}
    assert "google" in hizli.tarayici_turu_ucus(db)


def test_elle_tasima_ve_api(db):
    app = FastAPI()
    app.state.db = db
    app.state.simdi_fn = lambda: T0
    app.include_router(router)
    with TestClient(app) as c:
        d = c.put("/tarama/site/trendyol/tur", json={"tur": "http"}).json()
        assert "trendyol" in d["urun_siteleri"]
        tr = next(x for x in d["atamalar"] if x["site"] == "trendyol")
        assert tr["neden"] == "elle (arayüz)" and tr["http_arama"] is False
        assert c.put("/tarama/site/akakce/tur", json={"tur": "x"}).status_code == 422
        assert c.put("/tarama/site/yok/tur", json={"tur": "http"}).status_code == 422
        d = c.put("/tarama/site/akakce/tur", json={"tur": "tarayici"}).json()
        assert "akakce" not in d["urun_siteleri"]


def test_yuz_hatayi_asan_site_tarayici_turuna_duser(db):
    from kazi.zamanlayici import tur_atama
    assert tur_atama.http_hatalarini_isle(db, {"vatan": 60}, {"vatan", "akakce"}, None) == []
    assert tur_atama.atamalar(db)["vatan"]["http_hata"] == 60
    assert tur_atama.http_hatalarini_isle(db, {"vatan": 40}, {"vatan"}, None) == []    # 100: eşik AŞILMADI
    assert tur_atama.http_hatalarini_isle(db, {"vatan": 1}, {"vatan"}, None) == ["vatan"]
    a = tur_atama.atamalar(db)["vatan"]
    assert a["tur"] == "tarayici" and a["http_hata"] == 0 and a["neden"].startswith("otomatik")


def test_hatasiz_tur_sayaci_sifirlar(db):
    from kazi.zamanlayici import tur_atama
    tur_atama.http_hatalarini_isle(db, {"akakce": 50}, {"akakce"}, None)
    tur_atama.http_hatalarini_isle(db, {}, {"akakce"}, None)
    assert tur_atama.atamalar(db)["akakce"]["http_hata"] == 0


def test_tara_basarisiz_isleri_ve_sayaci_yazar(db):
    from kazi.zamanlayici import tur_atama
    def sahte_urun(v, t, **kw):
        return SimpleNamespace(teklifler={}, kapsam={("gpu_rtx5080", "akakce"): {"tur": "ok"},
                                                     ("gpu_rtx5080", "vatan"): {"tur": "blok", "hata": "bot"}})
    gorunurluk.ayar_yaz(http_ucus=False)
    ozet = hizli.tara(db, simdi_fn=lambda: T0, log=lambda m: None, urun_tara_fn=sahte_urun, alarm_fn=lambda v: None)
    assert ozet["basarisiz"] == [["gpu_rtx5080", "vatan"]] and ozet["site_hata"] == {"vatan": 1}
    assert tur_atama.atamalar(db)["vatan"]["http_hata"] == 1


