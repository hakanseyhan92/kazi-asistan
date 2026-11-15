"""Çekirdek iskeleti: ayar, veritabanı, API uç noktaları, olay akışı, OpenAPI."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kazi.__main__ import main
from kazi.api import uygulama_olustur
from kazi.ayar import Ayar
from kazi.db import VeriTabani, goc, surum_oku
from kazi.modeller import Teklif, UrunKimligi


@pytest.fixture
def ayar(tmp_path):
    return Ayar(veri_dizini=tmp_path)


VERI = Path(__file__).parent / "veri_tanimlama"


@pytest.fixture
def istemci(ayar):
    app = uygulama_olustur(ayar)
    app.state.katalog_getir = lambda url: ""          # testlerde ağ YOK
    with TestClient(app) as c:
        yield c


def test_tanimla_gercek_akakce_sayfasiyla_aday_ve_kural(ayar):
    app = uygulama_olustur(ayar)
    html = (VERI / "akakce_rtx5080.html").read_text(encoding="utf-8")
    app.state.katalog_getir = lambda url: html
    with TestClient(app) as c:
        r = c.post("/tanimla", json={"metin": "rtx 5080 en ucuz", "tur": "urun"}).json()
        assert r["sorgu"]["kategori"] == "gpu" and r["sorgu"]["model"] == "RTX 5080"
        assert len(r["adaylar"]) >= 5 and all("5080" in a["baslik"] for a in r["adaylar"][:5])
        sec = [a["id"] for a in r["adaylar"][:3]]
        u = c.post(f"/tanimla/{r['istek_id']}/onayla", json={"aday_idler": sec}).json()
        k = u["eslesme_kurallari"]
        assert k["kategori"] == "gpu" and k["dahil"] and "laptop" in " ".join(k["haric"]).lower()
        assert u["fiyat_bandi"]["alt"] < u["fiyat_bandi"]["ust"]
        assert "3 model" in u["kanonik_ad"]
        tek = c.post(f"/tanimla/{r['istek_id']}/onayla", json={"aday_idler": sec[:1]}).json()
        assert tek["kanonik_ad"] == r["adaylar"][0]["baslik"]
        assert c.post("/tanimla/yok/onayla", json={"aday_idler": ["x"]}).status_code == 404


def test_sema_ve_goc_idempotent(ayar):
    v = VeriTabani(ayar.db_yolu)
    s1 = surum_oku(v.baglanti)
    goc(v.baglanti)
    goc(v.baglanti)
    assert surum_oku(v.baglanti) == s1 == 1
    v.kapat()


def test_teklifler_yalniz_son_ve_gecmis(ayar):
    v = VeriTabani(ayar.db_yolu)
    uid = v.urun_ekle(UrunKimligi(kanonik_ad="RTX 5080"))
    v.teklif_ekle(Teklif(urun_id=uid, site="n11", satici="a", sepet_fiyati=120000, gozlem_zamani="2026-11-14T10:00:00+03:00"))
    v.teklif_ekle(Teklif(urun_id=uid, site="n11", satici="a", sepet_fiyati=115000, gozlem_zamani="2026-11-14T12:00:00+03:00"))
    v.teklif_ekle(Teklif(urun_id=uid, site="n11", satici="b", sepet_fiyati=118000))
    son = v.teklifler(uid)
    assert sorted(t.sepet_fiyati for t in son) == [115000, 118000]
    assert len(v.teklifler(uid, yalniz_son=False)) == 3
    v.kapat()


def test_saglik(istemci, ayar):
    r = istemci.get("/saglik")
    assert r.status_code == 200
    d = r.json()
    assert d["surum"] == 1 and d["db_yolu"] == str(ayar.db_yolu) and d["calisma_suresi_sn"] >= 0


def test_bos_listeler(istemci):
    assert istemci.get("/siteler").json() == []
    assert istemci.get("/urunler").json() == []


def test_olmayan_urun_tutarli_hata(istemci):
    r = istemci.get("/urunler/yok")
    assert r.status_code == 404
    assert set(r.json()) == {"hata", "ayrinti"}
    assert istemci.post("/tara", json={"urun_id": "yok"}).status_code == 404


def test_tanimla_onayla_teklif_akisi(istemci):
    r = istemci.post("/tanimla", json={"metin": "rtx 5080 en ucuz", "tur": "urun"})
    assert r.status_code == 200 and r.json()["adaylar"] == []
    iid = r.json()["istek_id"]
    r = istemci.post(f"/tanimla/{iid}/onayla", json={"aday_idler": ["a1"], "kanonik_ad": "Inno3D RTX 5080 X3", "mpn": "N50803-16D7"})
    assert r.status_code == 200
    urun = r.json()
    assert urun["kanonik_ad"] == "Inno3D RTX 5080 X3" and urun["id"]
    assert [u["id"] for u in istemci.get("/urunler").json()] == [urun["id"]]
    assert istemci.get(f"/urunler/{urun['id']}").json()["mpn"] == "N50803-16D7"
    assert istemci.get(f"/urunler/{urun['id']}/teklifler").json() == []
    assert istemci.get(f"/urunler/{urun['id']}/teklifler?gecmis=1").json() == []
    assert istemci.get(f"/urunler/{urun['id']}/teklifler?gecmis=5").status_code == 422


def test_olay_akisi_websocket(istemci):
    urun = istemci.post("/tanimla/x/onayla", json={"kanonik_ad": "Test"}).json()
    with istemci.websocket_connect("/olaylar") as ws:
        r = istemci.post("/tara", json={"urun_id": urun["id"]})
        gid = r.json()["gorev_id"]
        olay = ws.receive_json()
        assert olay["tur"] == "tarama_basladi" and olay["gorev_id"] == gid and olay["urun_id"] == urun["id"]


def test_openapi_cikti(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZI_VERI", str(tmp_path / "veri"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    hedef = tmp_path / "openapi.json"
    monkeypatch.setattr("sys.argv", ["kazi-cekirdek", "openapi", "--cikti", str(hedef)])
    main()
    d = json.loads(hedef.read_text(encoding="utf-8"))
    for yol in ("/saglik", "/siteler", "/urunler", "/urunler/{id}", "/urunler/{id}/teklifler",
                "/tanimla", "/tanimla/{istek_id}/onayla", "/tara"):
        assert yol in d["paths"], yol
