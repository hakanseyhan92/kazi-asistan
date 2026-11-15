"""Pano/detay görünümleri (api/SOZLESME.md)."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from kazi.api import uygulama_olustur
from kazi.ayar import Ayar
from kazi.db import VeriTabani
from kazi.modeller import Katman, Taksit, Teklif, UrunKimligi
from kazi import pano

TR = timezone(timedelta(hours=3))


def _z(saat_once=1):
    return (datetime.now(TR) - timedelta(hours=saat_once)).isoformat(timespec="seconds")


@pytest.fixture
def v(tmp_path):
    v = VeriTabani(Ayar(veri_dizini=tmp_path).db_yolu)
    v.urun_ekle(UrunKimligi(id="gpu", kanonik_ad="RTX 5080", eslesme_kurallari={"kategori": "gpu", "sistemde": True}))
    v.urun_ekle(UrunKimligi(id="cpu", kanonik_ad="9800X3D", eslesme_kurallari={"kategori": "cpu", "sistemde": True}))
    v.baglanti.execute("INSERT INTO izleme (urun_id, hedef_fiyat) VALUES ('gpu', 110000)")
    v.baglanti.commit()
    for site, f, durum in (("a", 120000, "bekliyor"), ("b", 130000, "bekliyor"), ("c", 125000, "bekliyor"),
                           ("d", 108000, "teyitli"), ("e", 90000, "urun_farkli")):
        tid = v.teklif_ekle(Teklif(urun_id="gpu", site=site, sepet_fiyati=f, stok=True, gozlem_zamani=_z(),
                                   teyit_durumu=durum, kaynak_katmani=Katman.K2))
        if site == "d":
            v.taksit_ekle(Taksit(teklif_id=tid, taksit_sayisi=9, vade_farki_yuzde=0))
            v.taksit_ekle(Taksit(teklif_id=tid, banka="Bonus", taksit_sayisi=12, toplam=122000, vade_farki_yuzde=13))
    v.teklif_ekle(Teklif(urun_id="gpu", site="eski", sepet_fiyati=50000, stok=True, gozlem_zamani=_z(30)))
    yield v
    v.kapat()


def test_pano_en_iyi_hedef_ve_sistem_toplami(v):
    p = pano.pano(v)
    gpu = next(u for u in p["urunler"] if u["id"] == "gpu")
    assert gpu["hedef_fiyat"] == 110000 and gpu["ozet"]["adet"] == 4          # urun_farkli ve 30 saatlik dışarıda
    assert gpu["en_iyi"]["site"] == "d" and gpu["en_iyi"]["hedef_altinda"] is True
    assert "yildiz_sayilari" not in gpu and "en_akilli" not in gpu and "akilli" not in gpu["en_iyi"]
    assert gpu["en_iyi"]["taksit"]["pesin_fiyatina"] == 9 and gpu["en_iyi"]["taksit"]["tablo"][0]["banka"] == "Bonus"
    assert p["sistem_toplami"] == {"tutar": 108000, "eksik": ["9800X3D"], "adet": 2, "secili": ["cpu", "gpu"]}


def test_detay_sirali_ve_gecmis(v):
    d = pano.detay(v, "gpu")
    assert [t["site"] for t in d["teklifler"]] == ["e", "d", "a", "c", "b"]
    assert d["teklifler"][0]["hedef_altinda"] is False and "yildiz" not in d["teklifler"][0]
    assert d["gecmis"][-1]["en_dusuk"] == 108000 and pano.detay(v, "yok") is None


def test_api_pano_detay_uclari(tmp_path):
    with TestClient(uygulama_olustur(Ayar(veri_dizini=tmp_path))) as c:
        assert c.get("/pano").json()["urunler"] == []
        assert c.get("/urunler/yok/detay").status_code == 404


def test_toplu_islem_hata_olursa_geri_alinir(tmp_path):
    """C1 #1: aktarım ortasında hata → önceki veri yerinde kalır."""
    from kazi.db import VeriTabani
    from kazi.modeller import UrunKimligi
    v = VeriTabani(tmp_path / "k.db")
    v.urun_ekle(UrunKimligi(id="eski", kanonik_ad="Eski"))
    try:
        with v.toplu():
            v.baglanti.execute("DELETE FROM urun_kimligi")
            v.urun_ekle(UrunKimligi(id="yeni", kanonik_ad="Yeni"))
            raise RuntimeError("okuma hatası")
    except RuntimeError:
        pass
    assert [u.id for u in v.urunler_listele()] == ["eski"]


def test_fiyatsiz_teklif_hedef_denetimini_dusurmez():
    """C1 #8."""
    from kazi.kurallar.piyasa import hedef_altinda
    assert hedef_altinda({"sepet_fiyati": None, "teyit_durumu": "teyitli"}, 90.0) is False


def test_ucus_kaynaklari_site_sagligina_aktarilir(tmp_path):
    import sqlite3
    from datetime import datetime, timedelta
    from kazi import eski
    from kazi.db import VeriTabani
    u = tmp_path / "ucus.db"
    c = sqlite3.connect(u)
    c.execute("CREATE TABLE tarama (id INTEGER PRIMARY KEY, zaman TEXT, kaynak TEXT, bacak_id TEXT, tarih TEXT, sonuc INTEGER, yontem TEXT, hata TEXT)")
    simdi = datetime.now().isoformat(timespec="seconds")
    c.executemany("INSERT INTO tarama (zaman,kaynak,bacak_id,sonuc,yontem,hata) VALUES (?,?,?,?,?,?)",
                  [(simdi, "google", "ist_tyo", 12, "http", None), (simdi, "thy", "ist_tyo", 0, None, "desteklenmiyor: captcha")])
    c.commit(); c.close()
    v = VeriTabani(tmp_path / "k.db")
    eski._ucus_siteleri_aktar(v, (datetime.now() - timedelta(hours=24)).isoformat(), ucus_db=u)
    s = {x.ad: x for x in v.siteler_listele()}
    assert str(s["google"].tur) == "ucus" and str(s["google"].katman_tercihi) == "K0" and s["google"].hata_turu is None
    assert str(s["thy"].katman_tercihi) == "K3" and s["thy"].hata_turu.startswith("desteklenmiyor")


def test_toplam_kutucuklari(v, tmp_path):
    """14.11: Takip Listesi kutucuğu toplamı belirler; seçim yoksa eski 'sistemde' işareti; seçim aktarımda silinmez."""
    p = pano.pano(v)
    assert p["sistem_toplami"]["secili"] == [u["id"] for u in p["urunler"] if u["sistemde"]]
    v.toplam_sec({"gpu": False})
    p = pano.pano(v)
    gpu = next(u for u in p["urunler"] if u["id"] == "gpu")
    assert gpu["toplamda"] is False and "gpu" not in p["sistem_toplami"]["secili"]
    assert p["sistem_toplami"]["tutar"] == 0 and p["sistem_toplami"]["adet"] == len(p["sistem_toplami"]["secili"])
    v.toplam_sec({"gpu": True})
    assert pano.pano(v)["sistem_toplami"]["tutar"] == 108000


def test_api_toplam(tmp_path):
    from kazi.modeller import UrunKimligi
    with TestClient(uygulama_olustur(Ayar(veri_dizini=tmp_path))) as c:
        c.app.state.db.urun_ekle(UrunKimligi(id="x", kanonik_ad="X", eslesme_kurallari={"sistemde": True}))
        assert c.get("/pano").json()["sistem_toplami"]["secili"] == ["x"]
        assert c.put("/toplam", json={"secimler": {"x": False}}).json()["secili"] == []
        assert c.put("/toplam", json={"secimler": {"yok": True}}).status_code == 404
        assert c.put("/toplam", json={"secimler": {"x": "evet"}}).status_code == 422


def test_ucus_iki_kaynak_birlesir_en_yeni_kalir(tmp_path):
    """Geçiş: /seyahat eski ucus.db + kazi uçuş deposunu birleştirir; aynı uçuşun en yeni gözlemi kalır."""
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from kazi import eski
    from kazi.db import VeriTabani
    from kazi.seyahat.plan import Yolcu
    from kazi.seyahat.segment import Segment
    from kazi.ucus import depo
    TRz = timezone(timedelta(hours=3))
    simdi = datetime.now(TRz)
    od = sqlite3.connect(":memory:")
    od.row_factory = sqlite3.Row
    od.execute("""CREATE TABLE ucus (id INTEGER PRIMARY KEY, zaman TEXT, kaynak TEXT, bacak_id TEXT, tarih TEXT, fiyat_toplam REAL,
                  havayolu TEXT, rota TEXT, aktarma INTEGER, sure_dk INTEGER, kalkis_saat TEXT, varis_saat TEXT, ayri_bilet INTEGER DEFAULT 0,
                  link TEXT, para TEXT DEFAULT 'TRY', yontem TEXT, yolcu INTEGER DEFAULT 1, uyarilar TEXT, kaynaklar TEXT)""")
    od.execute("INSERT INTO ucus (zaman,kaynak,bacak_id,tarih,fiyat_toplam,havayolu,rota,aktarma,sure_dk,kalkis_saat,varis_saat,yolcu) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               ((simdi - timedelta(hours=5)).replace(tzinfo=None).isoformat(timespec="seconds"), "google", "ist_tyo", "2026-12-27",
                60000, "Türk Hava Yolları", "IST>HND", 0, 780, "02:00", "20:00", 2))
    v = VeriTabani(tmp_path / "k.db")
    depo.kur(v)
    k = datetime(2026, 12, 27, 2, 0, tzinfo=TRz)
    ortak = dict(arac="ucak", kalkis_nokta="IST", varis_nokta="HND", firma="Türk Hava Yolları", toplam=None)
    yeni = Segment(id="n1", kalkis=k.isoformat(), varis=(k + timedelta(minutes=780)).astimezone(timezone(timedelta(hours=9))).isoformat(),
                   kisi_basi=27000, gozlem_zamani=simdi.isoformat(timespec="seconds"), kaynak="kayak", **ortak)
    baska = Segment(id="n2", kalkis=(k + timedelta(hours=10)).isoformat(),
                    varis=(k + timedelta(hours=23)).astimezone(timezone(timedelta(hours=9))).isoformat(),
                    kisi_basi=31000, gozlem_zamani=simdi.isoformat(timespec="seconds"), kaynak="kayak", **ortak)
    depo.kaydet(v, [yeni, baska], "ist_tyo")
    from datetime import date
    sec, _, _ = eski._bacak_secenekleri(od, "ist_tyo", [Yolcu("ali", "Ali", ["TR"])], date(2026, 12, 25), date(2026, 12, 29),
                                        36, 50, v)
    assert [round(s.kisi_basi) for s, _ in sec] == [27000, 31000]        # aynı uçuşun eski 30.000'lik gözlemi düştü


def test_ayni_saticinin_farkli_ilanlari_ezilmez(tmp_path):
    """14.11: en son gözlem ilan (link) bazında; aynı satıcının ucuz ilanı sonraki pahalı ilanı yüzünden kaybolmaz."""
    from kazi.db import VeriTabani
    from kazi.modeller import Teklif, UrunKimligi
    v = VeriTabani(tmp_path / "k.db")
    v.urun_ekle(UrunKimligi(id="gpu", kanonik_ad="GPU"))
    v.teklif_ekle(Teklif(urun_id="gpu", site="pttavm", satici="cimri üzerinden", url="https://a/1", sepet_fiyati=119190.0,
                         gozlem_zamani="2026-11-14T19:21:00+03:00"))
    v.teklif_ekle(Teklif(urun_id="gpu", site="pttavm", satici="cimri üzerinden", url="https://a/2", sepet_fiyati=150000.0,
                         gozlem_zamani="2026-11-14T19:22:00+03:00"))
    v.teklif_ekle(Teklif(urun_id="gpu", site="pttavm", satici="cimri üzerinden", url="https://a/1", sepet_fiyati=121000.0,
                         gozlem_zamani="2026-11-14T18:00:00+03:00"))          # aynı ilanın ESKİ gözlemi → düşer
    assert sorted(t.sepet_fiyati for t in v.teklifler("gpu")) == [119190.0, 150000.0]


def test_son_taramada_gorulmeyen_ilan_bayattir(tmp_path):
    from datetime import datetime, timedelta, timezone
    from kazi import pano as pano_mod
    from kazi.db import VeriTabani
    from kazi.modeller import Teklif, UrunKimligi
    TRz = timezone(timedelta(hours=3))
    simdi = datetime.now(TRz)
    v = VeriTabani(tmp_path / "k.db")
    v.urun_ekle(UrunKimligi(id="gpu", kanonik_ad="GPU"))
    v.teklif_ekle(Teklif(urun_id="gpu", site="epey", url="https://e/1", sepet_fiyati=109999.0, stok=True,
                         gozlem_zamani=(simdi - timedelta(hours=12)).isoformat()))       # sabah görülmüş, sonra yok
    v.teklif_ekle(Teklif(urun_id="gpu", site="pttavm", url="https://p/1", sepet_fiyati=119190.0, stok=True,
                         gozlem_zamani=(simdi - timedelta(minutes=10)).isoformat()))
    assert pano_mod.pano(v)["urunler"][0]["en_iyi"]["sepet_fiyati"] == 119190.0
