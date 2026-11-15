"""Kategori bazlı zamanlayıcı API router testleri (`api_tarama.py`) — AĞ YOK."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from kazi.api_tarama import router
from kazi.db import VeriTabani
from kazi.modeller import TZ_ISTANBUL, UrunKimligi
from kazi.tarayici import gorunurluk
from kazi.zamanlayici import plan as plan_mod


@pytest.fixture
def istemci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    gorunurluk.ayar_yaz(brave_gorunur=False)

    saat = {"simdi": datetime(2026, 11, 14, 19, 0, 0, tzinfo=TZ_ISTANBUL)}
    db = VeriTabani(tmp_path / "kazi.db")
    db.urun_ekle(UrunKimligi(id="gpu_rtx5080", kanonik_ad="RTX 5080", eslesme_kurallari={"kategori": "gpu"}))
    db.urun_ekle(UrunKimligi(id="cpu_9800x3d", kanonik_ad="Ryzen 7 9800X3D", eslesme_kurallari={"kategori": "cpu"}))

    app = FastAPI()
    app.state.db = db
    app.state.simdi_fn = lambda: saat["simdi"]
    app.include_router(router)

    try:
        with TestClient(app) as c:
            yield c, db, saat, tmp_path / "cfg" / "kazi" / "tarama.json"
    finally:
        db.kapat()


def test_get_tarama_plan_sozlesme_bicimi(istemci):
    c, _db, _saat, _cfg = istemci
    r = c.get("/tarama/plan")
    assert r.status_code == 200
    d = r.json()

    assert d["secenekler_dk"] == list(range(30, 121, 5))
    assert d["min_aralik_dk"] == 2
    assert d["manuel_bekleme_dk"] == 10
    assert d["simdi"] == "2026-11-14T19:00:00+03:00"

    ids = [k["id"] for k in d["kategoriler"]]
    assert ids == ["gpu", "cpu", "ucus"]

    by_id = {k["id"]: k for k in d["kategoriler"]}
    assert by_id["gpu"]["ad"] == "Ekran kartı"
    assert by_id["gpu"]["tur"] == "urun"
    assert by_id["gpu"]["urunler"] == ["gpu_rtx5080"]
    assert by_id["gpu"]["aralik_dk"] == 30
    assert by_id["gpu"]["durum"] == "bekliyor"
    assert by_id["gpu"]["kalan_sn"] == 0
    assert by_id["cpu"]["aralik_dk"] == 60
    assert by_id["cpu"]["kalan_sn"] == 120   # 2 dk boşluk (15.11)
    assert by_id["ucus"]["aralik_dk"] == 120
    assert by_id["ucus"]["kalan_sn"] == 240


def test_put_tarama_plan_dogrulama_ve_brave_gorunur_koruma(istemci):
    c, _db, _saat, cfg_yolu = istemci

    r = c.put("/tarama/plan", json={"kategoriler": {"gpu": 40, "ucus": 90}})
    assert r.status_code == 200
    by_id = {k["id"]: k for k in r.json()["kategoriler"]}
    assert by_id["gpu"]["aralik_dk"] == 40
    assert by_id["ucus"]["aralik_dk"] == 90
    assert by_id["cpu"]["aralik_dk"] == 60

    kayitli = json.loads(cfg_yolu.read_text(encoding="utf-8"))
    assert kayitli["brave_gorunur"] is False
    assert kayitli["kategori_aralik_dk"] == {"gpu": 40, "ucus": 90}

    # 30 dk altı ve seçenek dışı değerler reddedilir (422)
    for kotu in (10, 20, 29, 33, 117, 125, 180, True, "40"):
        resp = c.put("/tarama/plan", json={"kategoriler": {"gpu": kotu}})
        assert resp.status_code == 422, kotu

    assert c.put("/tarama/plan", json={"yanlis": {}}).status_code == 422


def test_post_kategori_simdi_202_429_404_ve_kilit_suresi(istemci):
    c, _db, saat, _cfg = istemci

    # Bilinmeyen kategori -> 404
    assert c.post("/tarama/kategori/yok/simdi").status_code == 404

    # İlk elle tarama -> 202
    r1 = c.post("/tarama/kategori/gpu/simdi")
    assert r1.status_code == 202
    assert r1.json() == {"durum": "kuyrukta"}

    # Plan çıktısında durum kuyrukta ve 10 dk kilit aktif
    plan1 = c.get("/tarama/plan").json()
    gpu = next(k for k in plan1["kategoriler"] if k["id"] == "gpu")
    assert gpu["durum"] == "kuyrukta"
    assert gpu["manuel_kilit_bitis"] == "2026-11-14T19:10:00+03:00"
    assert gpu["manuel_kalan_sn"] == 600

    # 3 dk sonra tekrar tıklama -> 429 {"hata": ..., "kalan_sn": 420}
    saat["simdi"] = saat["simdi"] + timedelta(minutes=3)
    r2 = c.post("/tarama/kategori/gpu/simdi")
    assert r2.status_code == 429
    body2 = r2.json()
    assert "hata" in body2 and body2["kalan_sn"] == 420

    # 10 dk dolunca tekrar izin verilir -> 202
    saat["simdi"] = saat["simdi"] + timedelta(minutes=7)
    r3 = c.post("/tarama/kategori/gpu/simdi")
    assert r3.status_code == 202
    assert r3.json() == {"durum": "kuyrukta"}


def test_takipten_cikan_urun_kategorisi_listeden_duser(istemci):
    c, db, _saat, _cfg = istemci
    db.takipten_cikar(["cpu_9800x3d"], "2026-11-14T19:00:00+03:00")
    ids = [k["id"] for k in c.get("/tarama/plan").json()["kategoriler"]]
    assert ids == ["gpu", "ucus"]
    assert c.post("/tarama/kategori/cpu/simdi").status_code == 404


def test_sifirla_sayaci_simdiden_baslatir_calisana_dokunmaz(istemci):
    """15.11 kullanıcı: süreleri değiştirince "Sıfırla" → yeni süre şimdiden sayılsın; taranıyor olana karışmasın."""
    c, db, saat, _cfg = istemci
    plan_mod.kur(db)
    plan_mod.tarama_basladi(db, "gpu", "2026-11-14T18:00:00+03:00")
    plan_mod.tarama_bitti(db, "gpu", "2026-11-14T18:05:00+03:00", {"teklif": 3, "hata": 0, "sure_sn": 300})
    plan_mod.tarama_basladi(db, "cpu", "2026-11-14T18:58:00+03:00")          # şu an taranıyor
    assert c.put("/tarama/plan", json={"kategoriler": {"gpu": 45}}).status_code == 200

    saat["simdi"] = datetime(2026, 11, 14, 19, 0, 0, tzinfo=TZ_ISTANBUL)
    r = c.post("/tarama/sifirla", json={})
    assert r.status_code == 200
    d = r.json()
    assert d["sifirlanan"] == ["gpu", "ucus"] and d["atlanan"] == ["cpu"]
    by_id = {k["id"]: k for k in d["kategoriler"]}
    assert by_id["gpu"]["sonraki"] == "2026-11-14T19:45:00+03:00"            # 19:00 + 45 dk
    assert by_id["gpu"]["son_bitis"] == "2026-11-14T18:05:00+03:00"          # son tarama bilgisi aynı
    assert by_id["gpu"]["sayac_sifir"] == "2026-11-14T19:00:00+03:00"
    assert by_id["cpu"]["durum"] == "calisiyor" and by_id["cpu"]["sayac_sifir"] is None
    assert by_id["ucus"]["sonraki"] == "2026-11-14T21:00:00+03:00"

    # tek kategori; yeni tarama bitince sayaç yine bitişten sayılır
    saat["simdi"] = datetime(2026, 11, 14, 19, 10, 0, tzinfo=TZ_ISTANBUL)
    d = c.post("/tarama/sifirla", json={"kategoriler": ["gpu"]}).json()
    assert d["sifirlanan"] == ["gpu"]
    assert {k["id"]: k for k in d["kategoriler"]}["gpu"]["sonraki"] == "2026-11-14T19:55:00+03:00"
    plan_mod.tarama_basladi(db, "gpu", "2026-11-14T19:55:00+03:00")
    plan_mod.tarama_bitti(db, "gpu", "2026-11-14T19:57:00+03:00", None)
    d = c.get("/tarama/plan").json()
    assert {k["id"]: k for k in d["kategoriler"]}["gpu"]["sonraki"] == "2026-11-14T20:42:00+03:00"

    assert c.post("/tarama/sifirla", json={"kategoriler": ["yok"]}).status_code == 404
    assert c.post("/tarama/sifirla", json={"kategoriler": "gpu"}).status_code == 422


def test_sifir_sutunu_eski_tabloya_eklenir(tmp_path: Path):
    import sqlite3
    con = sqlite3.connect(tmp_path / "eski.db")
    con.execute("""CREATE TABLE tarama_kategori (id TEXT PRIMARY KEY, son_baslangic TEXT, son_bitis TEXT,
                   son_sonuc TEXT, manuel_istek_zamani TEXT, manuel_son TEXT)""")
    con.execute("INSERT INTO tarama_kategori (id, son_bitis) VALUES ('gpu', '2026-11-14T18:00:00+03:00')")
    assert plan_mod.durum_oku(con)["gpu"]["sayac_sifir"] is None   # göç öncesi okuma bozulmaz
    plan_mod.sayac_sifirla(con, "gpu", "2026-11-14T19:00:00+03:00")
    assert plan_mod.durum_oku(con)["gpu"]["sayac_sifir"] == "2026-11-14T19:00:00+03:00"
    assert plan_mod.durum_oku(con)["gpu"]["son_bitis"] == "2026-11-14T18:00:00+03:00"
