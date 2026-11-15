"""Takipten çıkarma, alarm fiyatı, Telegram ayarı ve alarm bildirimi (14.11). Ağ YOK."""
import json
import stat
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from kazi.api import uygulama_olustur
from kazi.ayar import Ayar
from kazi.bildirim import telegram as tg
from kazi.modeller import Teklif, UrunKimligi

TR = timezone(timedelta(hours=3))


@pytest.fixture
def ortam(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    evren = tmp_path / "evren.json"
    evren.write_text(json.dumps({"bot": "deneme_bot", "token": "123456:ABCDEFGHIJKLMNOP", "chat_id": 42}))
    monkeypatch.setattr(tg, "EVREN_AYAR", evren)
    app = uygulama_olustur(Ayar(veri_dizini=tmp_path))
    with TestClient(app) as c:
        v = c.app.state.db
        simdi = datetime.now(TR).isoformat(timespec="seconds")
        for uid, fiyat in (("gpu", 100000.0), ("cpu", 20000.0)):
            v.urun_ekle(UrunKimligi(id=uid, kanonik_ad=uid.upper(), eslesme_kurallari={"sistemde": True}))
            v.teklif_ekle(Teklif(urun_id=uid, site="n11", satici="s", baslik=uid, url=f"https://n11.com/{uid}",
                                 sepet_fiyati=fiyat, stok=True, gozlem_zamani=simdi))
        yield c, v


def test_telegram_ayari_evrenden_okunur_maskeli_ve_yazilir(ortam):
    c, _ = ortam
    o = c.get("/ayarlar/telegram").json()
    assert o["kaynak"] == "evren-asistan" and o["chat_id"] == 42 and o["token_var"] is True
    assert "ABCDEFGHIJKLMNOP" not in json.dumps(o) and o["token_maskeli"] == "1234…MNOP"
    o = c.put("/ayarlar/telegram", json={"chat_id": "99", "token": ""}).json()     # boş token → mevcut korunur
    assert o["chat_id"] == 99 and o["kaynak"] == "kazi" and o["token_maskeli"] == "1234…MNOP"
    assert stat.S_IMODE(tg.kazi_ayar_yolu().stat().st_mode) == 0o600
    assert c.put("/ayarlar/telegram", json={"chat_id": "abc"}).status_code == 422


def test_takipten_cikar_ve_geri_al(ortam):
    c, _ = ortam
    p = c.post("/takip/cikar", json={"urun_idler": ["cpu"]}).json()
    assert [u["id"] for u in p["urunler"]] == ["gpu"] and p["takip_disi"][0]["id"] == "cpu"
    assert p["sistem_toplami"]["secili"] == ["gpu"]
    assert c.post("/takip/cikar", json={"urun_idler": ["yok"]}).status_code == 404
    p = c.post("/takip/geri_al", json={"urun_idler": ["cpu"]}).json()
    assert {u["id"] for u in p["urunler"]} == {"gpu", "cpu"} and p["takip_disi"] == []
    assert sorted(p["sistem_toplami"]["secili"]) == ["cpu", "gpu"]      # geri alınca Σ seçimi de geri gelir
    # kullanıcı Σ'yi kapattıysa çıkar/geri al bu seçimi de korur
    c.put("/toplam", json={"secimler": {"cpu": False}})
    c.post("/takip/cikar", json={"urun_idler": ["cpu"]})
    p = c.post("/takip/geri_al", json={"urun_idler": ["cpu"]}).json()
    assert p["sistem_toplami"]["secili"] == ["gpu"]


def test_alarm_fiyati_ve_aninda_bildirim(ortam, monkeypatch):
    c, v = ortam
    giden = []
    monkeypatch.setattr(tg, "gonder", lambda m, *a, **k: giden.append(m) or True)
    r = c.put("/urunler/gpu/alarm", json={"alarm_fiyat": 90000}).json()      # fiyat 100.000 > 90.000 → mesaj yok
    assert r["hemen_gonderilen"] == [] and giden == []
    gpu = next(u for u in c.get("/pano").json()["urunler"] if u["id"] == "gpu")
    assert gpu["alarm_fiyat"] == 90000 and gpu["en_iyi"]["alarm_altinda"] is False
    r = c.put("/urunler/gpu/alarm", json={"alarm_fiyat": 100000}).json()     # eşit → alarm
    assert r["hemen_gonderilen"][0]["fiyat"] == 100000 and len(giden) == 1
    assert "🔔 <b>ALARM (" in giden[0] and "<b>GPU</b>\n<b>Fiyat: 100.000 TL</b>" in giden[0]
    assert "tam alarm fiyatında" in giden[0] and '<a href="https://n11.com/gpu">n11 · s</a>' in giden[0]
    assert "teyit" not in giden[0]
    assert tg.alarm_denetle(v, gonder_fn=lambda m: giden.append(m) or True) == []   # 12 saat tekrar koruması
    c.put("/urunler/cpu/alarm", json={"alarm_fiyat": 25000})
    assert "<b>5.000 TL altında</b>" in giden[-1]
    assert c.put("/urunler/gpu/alarm", json={"alarm_fiyat": -5}).status_code == 422
    assert c.put("/urunler/gpu/alarm", json={"alarm_fiyat": None}).json()["alarm_fiyat"] is None


def test_gonder_ayar_yoksa_false(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(tg, "EVREN_AYAR", tmp_path / "yok.json")
    assert tg.gonder("x") is False


def test_rapor_metni_ve_zamanlama(ortam, monkeypatch):
    from kazi.bildirim import rapor
    c, v = ortam
    v.alarm_yaz("gpu", 90000, "2026-11-14T19:00:00+03:00")
    giden = []
    gonder = lambda m, a=None: giden.append(m) or True
    seyahat = {"bacaklar": [{"id": "ist_tyo", "baslik": "İstanbul → Tokyo", "secenekler": [
        {"kisi_basi": 25825, "kalkis": "2026-12-27T16:20:00+03:00", "firma": "MIAT", "aktarma_sayisi": 1, "aktarmalar": ["UBN"], "sure_dk": 970}]}],
               "yan_turlar": []}
    t0 = datetime(2026, 11, 14, 12, 0, tzinfo=TR)
    assert rapor.rapor_gonder_gerekirse(v, t0, gonder, seyahat_fn=lambda: seyahat) is True
    m = "\n".join(giden)
    assert "Fiyat Raporu (14.11.2026 12:00)" in m
    assert "<b>GPU</b>\n<b>Fiyat: 100.000 TL</b>" in m and '<a href="https://n11.com/gpu">n11 · s</a>' in m
    assert "alarm" not in m and "teyit" not in m and "★" not in m
    assert "Seçili toplam (2 ürün): 120.000 TL" in m and "<b>25.825 TL</b> kişi başı" in m
    assert "1 aktarma" in m and "27 Ara Paz · 16:20" in m and "🏢 MIAT" in m
    assert rapor.rapor_gonder_gerekirse(v, t0 + timedelta(minutes=30), gonder, seyahat_fn=lambda: seyahat) is False  # 60 dk dolmadı
    assert rapor.rapor_gonder_gerekirse(v, datetime(2026, 11, 15, 3, 0, tzinfo=TR), gonder, seyahat_fn=lambda: seyahat) is False  # sessiz
    v.teklif_ekle(Teklif(urun_id="gpu", site="vatan", satici="v", baslik="gpu", url="https://v/gpu", sepet_fiyati=98000.0, stok=True,
                         gozlem_zamani=datetime.now(TR).isoformat(timespec="seconds")))
    giden.clear()
    assert rapor.rapor_gonder_gerekirse(v, t0 + timedelta(minutes=61), gonder, seyahat_fn=lambda: seyahat) is True
    assert "<b>Fiyat: 98.000 TL</b> ↓2.000 TL" in giden[0]
    assert rapor.sessiz_mi(datetime(2026, 1, 1, 23, 30), ["23:00", "07:00"]) and not rapor.sessiz_mi(datetime(2026, 1, 1, 12), ["23:00", "07:00"])


def test_api_rapor_ayari(ortam):
    c, _ = ortam
    o = c.get("/ayarlar/telegram").json()
    assert o["rapor_aralik_dk"] == 60 and o["sessiz_saatler"] == ["01:00", "07:00"] and 0 in o["aralik_secenekleri"]
    assert c.put("/ayarlar/telegram", json={"rapor_aralik_dk": 120, "sessiz_saatler": ["00:00", "08:00"]}).json()["rapor_aralik_dk"] == 120
    assert c.put("/ayarlar/telegram", json={"rapor_aralik_dk": 45}).status_code == 422
    assert c.put("/ayarlar/telegram", json={"sessiz_saatler": ["25:00", "x"]}).status_code == 422


def test_rapor_bicim_yardimcilari():
    from kazi.bildirim import rapor
    assert rapor.marka_model("Anakart Gigabyte X870 Aorus Elite WIFI7") == "Gigabyte X870 Aorus Elite WIFI7"
    assert rapor.marka_model("Sıvı soğutucu Arctic Liquid Freezer III Pro 360") == "Arctic Liquid Freezer III Pro 360"
    assert rapor.marka_model("Kasalar") == "Kasalar"
    assert rapor._parcala(["a" * 10, "b" * 10, "c" * 10], sinir=25) == ["a" * 10 + "\n\n" + "b" * 10, "c" * 10]
    satir = rapor._secenek_satirlari({"kisi_basi": 1000, "toplam": 2000, "kalkis": "2026-12-26T23:59:00+03:00",
                                      "varis": "2026-12-27T05:00:00+03:00", "kalkis_nokta": "ESB", "varis_nokta": "IST",
                                      "aktarma_sayisi": 0, "sure_dk": 301, "firma": "Otobüs", "arac": "otobus"}, 2)
    assert satir[0] == "💰 <b>1.000 TL</b> kişi başı · 2 kişi 2.000 TL"
    assert satir[1] == "📅 26 Ara Cmt · 23:59 → 05:00 (+1)" and satir[2] == "🚌 ESB → IST · direkt · 5 sa 01 dk"


def test_saatsiz_ucusta_sure_ve_tekrar_uyari_yok():
    from kazi.bildirim import rapor
    satir = rapor._secenek_satirlari({"kisi_basi": 20000, "kalkis": "2027-01-06T12:00:00+09:00",
                                      "varis": "2027-01-06T15:00:00+09:00", "kalkis_nokta": "NRT", "varis_nokta": "SAW",
                                      "aktarma_sayisi": 2, "sure_dk": 180, "saat_bilinmiyor": True,
                                      "uyarilar": ["⚠ kalkış/varış saati kaynakta yok"]}, 1)
    assert "📅 6 Oca Çar · saat ?" in satir and "✈️ NRT → SAW · 2 aktarma" in satir
    assert not any("kaynakta yok" in x for x in satir)


def test_alarm_mesaji_rapor_bicimi():
    from datetime import datetime
    m = tg.alarm_mesaji("Anakart Gigabyte X870 Aorus Elite WIFI7", 12000,
                        {"sepet_fiyati": 11900, "site": "Amazon", "satici": "incehesap", "url": "https://a/x",
                         "baslik": "GIGABYTE X870 AORUS", "taksit": {"pesin_fiyatina": 6}},
                        kategori="anakart", zaman=datetime(2026, 11, 15, 19, 10, tzinfo=TR))
    assert m.split("\n") == [
        "🔔 <b>ALARM (15.11.2026 19:10)</b>", "", "🧩 <b>Anakart</b>", "<b>Gigabyte X870 Aorus Elite WIFI7</b>",
        '<b>Fiyat: 11.900 TL</b> · <b>6 ay vade farksız</b> · <a href="https://a/x">Amazon · incehesap</a>',
        "📦 <i>GIGABYTE X870 AORUS</i>", "🎯 Alarm fiyatı 12.000 TL · <b>100 TL altında</b>"]
