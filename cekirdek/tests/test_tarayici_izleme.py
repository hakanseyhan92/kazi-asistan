"""Tarayıcı izleme: saf durum kuralları + API uç noktaları (sözleşme: gorevler/TARAYICI_IZLEME_SOZLESME.md)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kazi.api_tarayici as AT
from kazi.tarayici import dogrulama
from kazi.tarayici import izleme as I

TZ = ZoneInfo("Europe/Istanbul")
SIMDI = datetime(2026, 11, 15, 10, 20, 0, tzinfo=TZ)


def kayit(olay, *, tarayici="Brave (kazı)", site=None, seviye="bilgi", url=None, mesaj=None,
          dk_once=0.0, sn_once=0.0, veri=None, zaman=None):
    z = zaman or (SIMDI - timedelta(minutes=dk_once, seconds=sn_once)).isoformat()
    return {"id": 0, "zaman": z, "seviye": seviye, "kaynak": "tarayici", "olay": olay,
            "kategori": None, "site": site, "tarayici": tarayici, "url": url, "adet": None,
            "sure_sn": None, "mesaj": mesaj or olay, "veri": veri or {}}


def motor(motorlar, ad="brave_kazi"):
    return next(m for m in motorlar if m["ad"] == ad)


def hesapla(kayitlar, *, sekmeler=None, kurulu=lambda ad: True, istekler=None):
    sabit = sekmeler or {}
    return I.motor_durumlari(kayitlar, lambda port: sabit.get(port), kurulu, SIMDI, istekler or {})


# ---------------------------------------------------------------- kurulum / temel
def test_kurulu_degil_gri():
    m = motor(hesapla([], kurulu=lambda ad: False), "google_chrome")
    assert m["kurulu"] is False and m["durum"] == "kurulu_degil" and m["renk"] == "gri"
    assert m["destekli"] is False and m["bot"] is None


def test_bosta_gri_ve_calisiyor_false():
    m = motor(hesapla([]))
    assert m["durum"] == "bosta" and m["renk"] == "gri"
    assert m["calisiyor"] is False and m["sekmeler"] == [] and m["is_ozeti"] == ""


def test_site_adi_ve_bot_regex():
    assert I.site_adi("https://www.hepsiburada.com/ara?q=x") == "hepsiburada.com"
    assert I.site_adi("") == ""
    assert I.BOT_BASLIK.search("Just a moment...")
    assert I.BOT_BASLIK.search("Erişim engellendi")
    assert I.BOT_BASLIK.search("pardon our interruption")
    assert not I.BOT_BASLIK.search("Ürün listesi")


# ---------------------------------------------------------------- bot kuralları
def test_bot_sari():
    k = [kayit("dogrulama_gerekli", seviye="uyari", site="hepsiburada.com",
               url="https://www.hepsiburada.com/ara?q=rtx", dk_once=2)]
    m = motor(hesapla(k))
    assert m["durum"] == "bot" and m["renk"] == "sari"
    assert m["bot"]["site"] == "hepsiburada.com" and m["bot"]["bekliyor"] is False


def test_bot_sonra_gezinme_yesil():
    k = [kayit("gezinme", site="hepsiburada.com", mesaj="sayfa açıldı", sn_once=20),
         kayit("dogrulama_gerekli", seviye="uyari", site="hepsiburada.com", dk_once=2)]
    sekmeler = {9334: [{"id": "1", "url": "https://www.hepsiburada.com/x", "baslik": "Ürünler"}]}
    m = motor(hesapla(k, sekmeler=sekmeler))
    assert m["bot"] is None and m["durum"] == "aktif" and m["renk"] == "yesil"


def test_dogrulama_gecildi_yesil():
    k = [kayit("dogrulama_gecildi", site="trendyol.com", sn_once=40),
         kayit("dogrulama_gerekli", seviye="uyari", site="trendyol.com", dk_once=3)]
    m = motor(hesapla(k))
    assert m["bot"] is None and m["renk"] != "sari"


def test_10_dakikadan_eski_bot_sayilmaz():
    k = [kayit("dogrulama_gerekli", seviye="uyari", site="trendyol.com", dk_once=11)]
    m = motor(hesapla(k))
    assert m["durum"] == "bosta" and m["bot"] is None


def test_farkli_sitenin_gezinmesi_botu_temizlemez():
    k = [kayit("gezinme", site="trendyol.com", sn_once=10),
         kayit("dogrulama_gerekli", seviye="uyari", site="hepsiburada.com", dk_once=2)]
    m = motor(hesapla(k))
    assert m["durum"] == "bot" and m["bot"]["site"] == "hepsiburada.com"


def test_bekliyor_ve_kalan_sn():
    bitis = (SIMDI + timedelta(seconds=18)).timestamp()
    k = [kayit("dogrulama_bekleniyor", seviye="uyari", site="hepsiburada.com",
               dk_once=1, veri={"bitis": bitis, "kalan_sn": 18}),
         kayit("dogrulama_gerekli", seviye="uyari", site="hepsiburada.com", dk_once=2)]
    bot = motor(hesapla(k))["bot"]
    assert bot["bekliyor"] is True and 17 <= bot["kalan_sn"] <= 18


def test_bot_baslikli_sekme_sari():
    sekmeler = {9334: [{"id": "ABCD", "url": "https://www.hepsiburada.com/x",
                        "baslik": "Just a moment..."}]}
    m = motor(hesapla([], sekmeler=sekmeler))
    assert m["durum"] == "bot" and m["renk"] == "sari"
    assert m["sekmeler"][0]["bot"] is True
    assert m["bot"]["sekme_id"] == "ABCD" and m["bot"]["site"] == "hepsiburada.com"


def test_dogrulama_istegi_kalan_sn_bota_yazilir():
    k = [kayit("dogrulama_gerekli", seviye="uyari", site="hepsiburada.com", dk_once=2)]
    bot = motor(hesapla(k, istekler={"hepsiburada.com": 240}))["bot"]
    assert bot["kalan_sn"] == 240 and bot["bekliyor"] is False


# ---------------------------------------------------------------- aktif / sekmeler
def test_cdpli_aktif_yesil():
    sekmeler = {9334: [{"id": "1", "url": "https://www.trendyol.com/", "baslik": "Trendyol"}]}
    m = motor(hesapla([], sekmeler=sekmeler))
    assert m["durum"] == "aktif" and m["renk"] == "yesil" and m["calisiyor"] is True
    assert m["sekmeler"][0]["site"] == "trendyol.com"


def test_about_blank_sekmesi_gosterilmez_ama_aktif_saymaz():
    sekmeler = {9334: [{"id": "1", "url": "about:blank", "baslik": ""}]}
    m = motor(hesapla([], sekmeler=sekmeler))
    assert m["sekmeler"] == []            # about:blank listelenmez
    assert m["durum"] == "bosta"          # boş sekme iş sayılmaz


def test_cdpsiz_aktif_son_30_sn():
    k = [kayit("gezinme", tarayici="HTTP (K0)", site="trendyol.com", sn_once=10)]
    m = motor(hesapla(k), "http_k0")
    assert m["durum"] == "aktif" and m["renk"] == "yesil" and m["calisiyor"] is True


def test_cdpsiz_30_sn_eski_kayit_aktif_degil():
    k = [kayit("gezinme", tarayici="HTTP (K0)", site="trendyol.com", sn_once=31)]
    m = motor(hesapla(k), "http_k0")
    assert m["durum"] == "bosta"


# ---------------------------------------------------------------- özet / son kayıtlar
def test_son_kayitlar_en_cok_5_ve_is_ozeti():
    k = [kayit("gezinme", site=f"site{i}.com", mesaj=f"mesaj {i}", sn_once=i)
         for i in range(7)]                                  # 0 en yeni
    m = motor(hesapla(k))
    assert len(m["son_kayitlar"]) == 5
    assert m["son_kayitlar"][0]["mesaj"] == "mesaj 0"
    assert m["son_kayitlar"][-1]["mesaj"] == "mesaj 4"
    assert m["is_ozeti"] == "site0.com — mesaj 0"
    assert set(m["son_kayitlar"][0]) == {"zaman", "seviye", "site", "mesaj"}


# ---------------------------------------------------------------- API
@pytest.fixture
def istemci():
    app = FastAPI()
    app.include_router(AT.router)
    with TestClient(app) as c:
        yield c


def test_api_get_tarayicilar(istemci, monkeypatch):
    govde = {"zaman": "2026-11-15T10:20:00+03:00", "dogrulama_istekleri": {},
             "motorlar": [{"ad": "brave_kazi", "etiket": "Brave (kazı)", "durum": "bosta"}]}
    monkeypatch.setattr(AT.izleme, "durum", lambda: govde)
    r = istemci.get("/tarayicilar")
    assert r.status_code == 200 and r.json() == govde


def test_api_goruntu_404_bilinmeyen_motor(istemci):
    assert istemci.get("/tarayicilar/yok/goruntu/ABC").status_code == 404
    assert istemci.get("/tarayicilar/http_k0/goruntu/ABC").status_code == 409   # CDP'siz motor (sözleşme)


def test_api_goruntu_jpeg(istemci, monkeypatch):
    monkeypatch.setattr(AT.izleme, "ekran_goruntusu", lambda port, sid, **kw: b"\xff\xd8JPEG")
    r = istemci.get("/tarayicilar/brave_kazi/goruntu/ABC?genislik=480")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8JPEG"


def test_api_goruntu_hata_kodlari(istemci, monkeypatch):
    def patlat(tur):
        def _f(port, sid, **kw):
            raise tur("x")
        return _f
    monkeypatch.setattr(AT.izleme, "ekran_goruntusu", patlat(LookupError))
    assert istemci.get("/tarayicilar/brave_kazi/goruntu/ABC").status_code == 404
    monkeypatch.setattr(AT.izleme, "ekran_goruntusu", patlat(ConnectionError))
    assert istemci.get("/tarayicilar/brave_kazi/goruntu/ABC").status_code == 409
    monkeypatch.setattr(AT.izleme, "ekran_goruntusu", patlat(TimeoutError))
    assert istemci.get("/tarayicilar/brave_kazi/goruntu/ABC").status_code == 504


def test_api_dogrulama_post_ve_delete_dosyaya_yazar(istemci):
    r = istemci.post("/tarayicilar/dogrulama",
                     json={"site": "www.Hepsiburada.com", "sure_sn": 300, "ad": "http_k0"})
    assert r.status_code == 200
    govde = r.json()
    assert govde["tamam"] is True and govde["sekme_id"] is None and govde["gosterildi"] is False
    assert dogrulama.istekler(simdi=govde["bitis"] - 300) == {"hepsiburada.com": 300}

    d = istemci.delete("/tarayicilar/dogrulama/hepsiburada.com")
    assert d.status_code == 200 and d.json() == {"tamam": True, "silindi": True}
    assert dogrulama.istekler(simdi=govde["bitis"] - 300) == {}
    assert istemci.delete("/tarayicilar/dogrulama/hepsiburada.com").json()["silindi"] is False


def test_api_dogrulama_bos_site_422(istemci):
    assert istemci.post("/tarayicilar/dogrulama", json={"site": "  "}).status_code == 422


def test_api_dogrulama_sekme_bulunup_gosterilir(istemci, monkeypatch):
    sekmeler = [{"id": "SEC1", "url": "https://www.hepsiburada.com/x", "baslik": "HB",
                 "site": "hepsiburada.com", "bot": False}]
    monkeypatch.setattr(AT.izleme, "cdp_sekmeleri", lambda port: sekmeler)
    monkeypatch.setattr(AT.izleme, "pencereyi_goster", lambda port, sid: sid == "SEC1")
    govde = istemci.post("/tarayicilar/dogrulama",
                         json={"site": "hepsiburada.com", "ad": "brave_kazi", "sure_sn": 120}).json()
    assert govde["sekme_id"] == "SEC1" and govde["gosterildi"] is True


def test_cdp_acik_bos_liste_ve_son_kayit_aktif():
    """İnceleme 15.11: CDP açık ama sekme listesi boşken son 30 sn kaydı varsa motor aktif sayılır."""
    k = [{"zaman": "2026-11-15T10:19:50+03:00", "seviye": "bilgi", "kaynak": "ucus", "olay": "ucus_bitti:ok",
          "site": "kayak", "tarayici": "Brave (kazı)", "mesaj": "x", "veri": {}}]
    m = I.motor_durumlari(k, lambda port: [] if port == 9334 else None, lambda ad: True, SIMDI, {})[0]
    assert m["calisiyor"] is True and m["durum"] == "aktif"


def test_bekleme_geri_sayimi_baska_sitenin_kaydini_kullanmaz():
    def kayit(z, olay, site, **veri):
        return {"zaman": z, "seviye": "uyari", "kaynak": "tarayici", "olay": olay, "site": site,
                "tarayici": "Brave (kazı)", "mesaj": olay, "veri": veri}
    simdi_ep = I._simdi_coz(SIMDI).timestamp()
    k = [kayit("2026-11-15T10:19:58+03:00", "dogrulama_bekleniyor", "b.com", bitis=simdi_ep + 200),
         kayit("2026-11-15T10:19:40+03:00", "dogrulama_gerekli", "a.com")]
    sekme = [{"id": "S", "url": "https://a.com/x", "title": "Just a moment...", "type": "page"}]
    m = I.motor_durumlari(k, lambda port: sekme if port == 9334 else None, lambda ad: True, SIMDI, {})[0]
    assert m["bot"]["site"] == "a.com" and m["bot"]["bekliyor"] is False
