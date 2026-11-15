"""Ayarlar'dan site aramadan çıkarma (15.11): kapalı site taranmaz, toplayıcı kartı elenir, kayıtlı teklif görünmez."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from kazi.api import uygulama_olustur
from kazi.ayar import Ayar
from kazi.modeller import Teklif, UrunKimligi
from kazi.siteler._kart import Kart
from kazi.tarama.tara import _kapali_gerekce

TR = timezone(timedelta(hours=3))


@pytest.fixture
def ortam(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    app = uygulama_olustur(Ayar(veri_dizini=tmp_path))
    with TestClient(app) as c:
        v = c.app.state.db
        simdi = datetime.now(TR).isoformat(timespec="seconds")
        v.urun_ekle(UrunKimligi(id="gpu", kanonik_ad="GPU", eslesme_kurallari={"sistemde": True}))
        for site, fiyat in (("sinerji", 90000.0), ("n11", 100000.0)):
            v.teklif_ekle(Teklif(urun_id="gpu", site=site, satici="s", baslik="RTX 5080 16GB", url=f"https://{site}.com/x",
                                 sepet_fiyati=fiyat, stok=True, gozlem_zamani=simdi))
        yield c, v


def test_site_kapatilinca_teklifleri_gorunmez_acilinca_doner(ortam):
    c, v = ortam
    assert c.get("/pano").json()["urunler"][0]["en_iyi"]["site"].lower().startswith("sinerji")
    s = c.put("/siteler/sinerji/etkin", json={"etkin": False}).json()
    assert s["ad"] == "sinerji" and s["etkin"] is False and v.kapali_siteler() == {"sinerji"}
    p = c.get("/pano").json()["urunler"][0]
    assert p["en_iyi"]["site"] == "n11" and p["ozet"]["adet"] == 1
    assert all(t["site"] != "sinerji" for t in c.get("/urunler/gpu/detay").json()["teklifler"])
    assert all(t["site"] != "sinerji" for t in c.get("/urunler/gpu/teklifler?gecmis=1").json())
    c.put("/siteler/sinerji/etkin", json={"etkin": True})
    assert c.get("/pano").json()["urunler"][0]["ozet"]["adet"] == 2


def test_site_etkin_dogrulama(ortam):
    c, _ = ortam
    assert c.put("/siteler/sinerji/etkin", json={"etkin": "hayir"}).status_code == 422
    assert c.put("/siteler/boyle-site-yok/etkin", json={"etkin": False}).status_code == 404


def test_toplayici_karti_kapali_magazadan_gelirse_elenir():
    k = Kart(site="Sinerji Bilgisayar", baslik="RTX 5080", sepet_fiyati=1.0, url="https://akakce.com/x")
    assert _kapali_gerekce(k, {"sinerji"}).startswith("site aramadan çıkarıldı")
    assert _kapali_gerekce(k, set()) is None
    assert _kapali_gerekce(Kart(site="n11", baslik="x", sepet_fiyati=1.0, url="u"), {"sinerji"}) is None


def test_site_24s_yeni_taramalardan_hesaplanir(ortam):
    import json as _json

    from kazi.pano import site_24s_yeni
    c, v = ortam
    simdi = datetime.now(TR).isoformat(timespec="seconds")
    for veri in ({"site": "n11", "adet": 24, "hata": None}, {"site": "n11", "adet": 0, "hata": "blok: x"},
                 {"site": "epey", "adet": 0, "hata": "blok: y"}):
        v.baglanti.execute("INSERT INTO olay (tur, zaman, veri) VALUES ('tarama_site_bitti', ?, ?)",
                           (simdi, _json.dumps(veri)))
    v.baglanti.commit()
    d = site_24s_yeni(v)
    assert d["n11"] == {"basarili": 1, "hatali": 1, "teklif": 24} and d["epey"]["hatali"] == 1


def test_desteklenmeyen_ve_atlanan_hatali_sayilmaz(tmp_path):
    """15.11: THY/Qatar gibi desteklenmeyen kaynaklar ve aynı taramada atlanan denemeler site sağlığında hata değil."""
    import json as _json
    from datetime import datetime as _dt
    from kazi.db import VeriTabani as _VT
    from kazi.modeller import TZ_ISTANBUL as _TZ
    from kazi.pano import site_24s_yeni
    v = _VT(tmp_path / "k.db")
    z = _dt.now(_TZ).isoformat(timespec="seconds")
    for veri in ({"site": "thy", "adet": 0, "hata": "desteklenmiyor: API yok", "tur": "desteklenmiyor"},
                 {"site": "skyscanner", "adet": 0, "hata": "atlandı: bu taramada engellendi", "tur": "blok_atlandi"},
                 {"site": "skyscanner", "adet": 0, "hata": "blok: captcha", "tur": "blok"},
                 {"site": "skyscanner", "adet": 12, "hata": None, "tur": "ok"}):
        v.baglanti.execute("INSERT INTO olay (tur, zaman, mesaj, veri) VALUES ('tarama_site_bitti', ?, '', ?)",
                           (z, _json.dumps(veri)))
    v.baglanti.commit()
    d = site_24s_yeni(v)
    assert d["thy"] == {"basarili": 0, "hatali": 0, "teklif": 0, "atlanan": 1}
    assert d["skyscanner"] == {"basarili": 1, "hatali": 1, "teklif": 12, "atlanan": 1}
    v.kapat()
