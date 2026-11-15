"""Kayıt defteri (15.11): yaz/oku/süz, canlı izleme (sonra_id), tarayıcı olayları, Python logları, temizlik, API."""
import logging
import sqlite3

from fastapi.testclient import TestClient

from kazi import gunluk
from kazi.api import uygulama_olustur
from kazi.ayar import Ayar


def test_yaz_oku_suz():
    gunluk.yaz("bilgi", "tarama", "rtx: 12 teklif", site="trendyol", tarayici="Brave (kazı)", adet=12, sure_sn=3.456)
    gunluk.yaz("hata", "ucus", "enuygun zaman aşımı", site="enuygun", url="https://x/y")
    gunluk.yaz("uyari", "tarayici", "HTTP 403", site="epey", tarayici="HTTP (K0)")
    r = gunluk.oku()
    assert [k["site"] for k in r["kayitlar"]] == ["epey", "enuygun", "trendyol"]       # en yeni önce
    assert r["kayitlar"][2]["sure_sn"] == 3.46 and r["kayitlar"][2]["adet"] == 12
    assert [k["site"] for k in gunluk.oku(seviye="uyari")["kayitlar"]] == ["epey", "enuygun"]
    assert [k["site"] for k in gunluk.oku(seviye="hata")["kayitlar"]] == ["enuygun"]
    assert [k["site"] for k in gunluk.oku(ara="403")["kayitlar"]] == ["epey"]
    assert [k["site"] for k in gunluk.oku(sonra_id=r["kayitlar"][1]["id"])["kayitlar"]] == ["epey"]
    assert [k["site"] for k in gunluk.oku(once_id=r["kayitlar"][1]["id"])["kayitlar"]] == ["trendyol"]
    o = gunluk.ozet()
    assert o["seviye"] == {"bilgi": 1, "hata": 1, "uyari": 1} and "enuygun" in o["siteler"]


def test_kategori_etiketi_ve_kapali(monkeypatch):
    gunluk.kategori_ayarla("gpu")
    try:
        gunluk.yaz("bilgi", "tarama", "x")
    finally:
        gunluk.kategori_ayarla(None)
    assert gunluk.oku()["kayitlar"][0]["kategori"] == "gpu"
    monkeypatch.setenv("KAZI_GUNLUK", "kapali")
    gunluk.yaz("bilgi", "tarama", "yazılmaz")              # istisna yok
    assert gunluk.oku()["kayitlar"] == []


def test_tarayici_olayi_ve_python_logu():
    gunluk.tarayici_olayi({"tur": "dogrulama_gerekli", "site": "epey", "url": "https://epey.com/x"}, "Brave (kazı)")
    gunluk.tarayici_olayi({"tur": "tarama_site_bitti", "site": "a"})        # site bitişi ayrı yazılır, burada atlanır
    gunluk.python_logu_bagla()
    logging.getLogger("ucus.oturum").warning("%s form istisnası: %s", "kayak.com.tr", "kalkış seçilemedi")
    k = gunluk.oku()["kayitlar"]
    assert k[0]["kaynak"] == "python" and "kayak.com.tr form istisnası" in k[0]["mesaj"] and k[0]["seviye"] == "uyari"
    assert k[1]["seviye"] == "uyari" and k[1]["site"] == "epey" and "doğrulama" in k[1]["mesaj"]
    assert len(k) == 2


def test_temizle_eskiyi_siler(tmp_path):
    gunluk.yaz("bilgi", "tarama", "yeni")
    b = sqlite3.connect(str(gunluk.yol()))
    b.execute("INSERT INTO kayit (zaman, seviye, kaynak, mesaj) VALUES ('2020-01-01T00:00:00+03:00','bilgi','x','eski')")
    b.commit()
    b.close()
    assert gunluk.temizle() == 1
    assert [k["mesaj"] for k in gunluk.oku()["kayitlar"]] == ["yeni"]


def test_api(tmp_path):
    gunluk.yaz("hata", "ucus", "enuygun hata", site="enuygun")
    with TestClient(uygulama_olustur(Ayar(veri_dizini=tmp_path))) as c:
        r = c.get("/kayitlar", params={"seviye": "hata"}).json()
        assert r["kayitlar"][0]["mesaj"] == "enuygun hata" and r["son_id"] >= 1
        assert c.get("/kayitlar/ozet").json()["seviye"]["hata"] == 1
