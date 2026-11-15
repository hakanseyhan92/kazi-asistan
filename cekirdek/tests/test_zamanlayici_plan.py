"""Kategori bazlı zamanlayıcı saf planlama, ayar ve DB durumu testleri — AĞ YOK."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from kazi.db import VeriTabani
from kazi.modeller import TZ_ISTANBUL, UrunKimligi
from kazi.tarayici import gorunurluk
from kazi.zamanlayici import plan as plan_mod


def _dt(saat: int, dakika: int, saniye: int = 0) -> datetime:
    return datetime(2026, 11, 14, saat, dakika, saniye, tzinfo=TZ_ISTANBUL)


@pytest.fixture
def vt(tmp_path: Path) -> VeriTabani:
    db = VeriTabani(tmp_path / "kazi.db")
    plan_mod.kur(db)
    yield db
    db.kapat()


def test_kategoriler_gruplama_turkce_adlar_ve_takip_disi_dusmesi(vt: VeriTabani):
    vt.urun_ekle(UrunKimligi(id="gpu_1", kanonik_ad="RTX 5080", eslesme_kurallari={"kategori": "gpu"}))
    vt.urun_ekle(UrunKimligi(id="gpu_2", kanonik_ad="RTX 5070", eslesme_kurallari={"kategori": "gpu"}))
    vt.urun_ekle(UrunKimligi(id="cpu_1", kanonik_ad="Ryzen 7", eslesme_kurallari={"kategori": "cpu"}))
    vt.urun_ekle(UrunKimligi(id="ozel_1", kanonik_ad="Monitör", eslesme_kurallari={"kategori": "monitor"}))
    vt.urun_ekle(UrunKimligi(id="pasif_1", kanonik_ad="Pasif PSU", eslesme_kurallari={"kategori": "psu"}, aktif=False))

    kats = plan_mod.kategoriler(vt)
    by_id = {k.id: k for k in kats}

    assert [k.id for k in kats] == ["gpu", "cpu", "monitor", "ucus"]
    assert by_id["gpu"].ad == "Ekran kartı" and by_id["gpu"].tur == "urun"
    assert sorted(by_id["gpu"].urunler) == ["gpu_1", "gpu_2"]
    assert by_id["cpu"].ad == "İşlemci" and by_id["cpu"].urunler == ["cpu_1"]
    assert by_id["monitor"].ad == "monitor"  # bilinmeyen -> kategori kodu
    assert by_id["ucus"].ad == "Uçuşlar" and by_id["ucus"].tur == "ucus" and by_id["ucus"].urunler == []

    # Takipten çıkan ürünün kategorisi listeden düşer
    vt.takipten_cikar(["cpu_1"], "2026-11-14T19:00:00+03:00")
    kats2 = plan_mod.kategoriler(vt)
    assert "cpu" not in {k.id for k in kats2}

    # Geri alınınca geri gelir
    vt.takibe_al(["cpu_1"])
    kats3 = plan_mod.kategoriler(vt)
    assert "cpu" in {k.id for k in kats3}


def test_tum_bilinen_turkce_kategori_adlari():
    beklenen = {
        "gpu": "Ekran kartı",
        "cpu": "İşlemci",
        "ram": "Bellek (RAM)",
        "anakart": "Anakart",
        "ssd": "SSD",
        "psu": "Güç kaynağı",
        "kasa": "Kasa",
        "sogutucu": "Soğutucu",
        "ucus": "Uçuşlar",
    }
    for kod, ad in beklenen.items():
        assert plan_mod.kategori_adi(kod) == ad


def test_ayar_varsayilan_gecersiz_reddi_ve_brave_gorunur_korunur(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    # Varsayılanlar: gpu 30, ucus 120, diğerleri 60
    assert plan_mod.aralik_dk_coz("gpu", {}) == 30
    assert plan_mod.aralik_dk_coz("ucus", {}) == 120
    assert plan_mod.aralik_dk_coz("cpu", {}) == 60
    assert plan_mod.aralik_dk_coz("ram", {"ram": 15, "gpu": True, "ucus": "120"}) == 60
    assert plan_mod.aralik_dk_coz("gpu", {"gpu": 10}) == 30

    # Mevcut brave_gorunur anahtarı korunmalı
    gorunurluk.ayar_yaz(brave_gorunur=False)
    plan_mod.kategori_aralik_yaz({"gpu": 40, "cpu": 90})

    dosya = json.loads((tmp_path / "kazi" / "tarama.json").read_text(encoding="utf-8"))
    assert dosya["brave_gorunur"] is False
    assert dosya["kategori_aralik_dk"] == {"gpu": 40, "cpu": 90}

    # 30 dk alt sınır ve seçenek dışı değer reddi
    for gecersiz in (0, 10, 20, 29, 33, 47, 112, 125, 150, True, "30", 30.0):
        with pytest.raises(ValueError):
            plan_mod.kategori_aralik_yaz({"gpu": gecersiz})  # type: ignore[dict-item]


def test_plan_hic_taranmamis_kategoriler_sirayla():
    kats = [
        plan_mod.Kategori("gpu", "Ekran kartı", "urun", ["g1"]),
        plan_mod.Kategori("cpu", "İşlemci", "urun", ["c1"]),
        plan_mod.Kategori("ucus", "Uçuşlar", "ucus", []),
    ]
    simdi = _dt(19, 0)
    sonuc = plan_mod.plan_hesapla(kats, durum={}, ayar={}, simdi=simdi, calisan=None, kuyruk=[])

    assert sonuc["secenekler_dk"] == list(range(30, 121, 5))   # 15.11: 5'er dk
    assert sonuc["min_aralik_dk"] == 2
    assert sonuc["manuel_bekleme_dk"] == 10
    assert sonuc["simdi"] == "2026-11-14T19:00:00+03:00"

    by_id = {k["id"]: k for k in sonuc["kategoriler"]}
    assert by_id["gpu"]["kalan_sn"] == 0
    assert by_id["cpu"]["kalan_sn"] == 120
    assert by_id["ucus"]["kalan_sn"] == 240
    assert by_id["cpu"]["sonraki"] == "2026-11-14T19:02:00+03:00"
    assert by_id["ucus"]["sonraki"] == "2026-11-14T19:04:00+03:00"
    assert plan_mod.siradaki(kats, {}, {}, simdi) == "gpu"


def test_plan_global_2dk_bosluk_ve_en_cok_gecikmis_once():
    kats = [
        plan_mod.Kategori("gpu", "Ekran kartı", "urun", ["g1"]),
        plan_mod.Kategori("cpu", "İşlemci", "urun", ["c1"]),
        plan_mod.Kategori("ram", "Bellek (RAM)", "urun", ["r1"]),
    ]
    simdi = _dt(19, 0)
    # vade = son BİTİŞ + aralık: cpu 17:15+60 = 18:15, gpu 18:20+30 = 18:50, ram 18:59+60 = 19:59
    # son_global_bitis = 18:59 -> global izin (2 dk) = 19:01
    durum = {
        "gpu": {"son_baslangic": _dt(18, 15).isoformat(), "son_bitis": _dt(18, 20).isoformat()},
        "cpu": {"son_baslangic": _dt(17, 10).isoformat(), "son_bitis": _dt(17, 15).isoformat()},
        "ram": {"son_baslangic": _dt(18, 25).isoformat(), "son_bitis": _dt(18, 59).isoformat()},
    }
    ayar = {"gpu": 30, "cpu": 60, "ram": 60}

    sonuc = plan_mod.plan_hesapla(kats, durum, ayar, simdi, calisan=None, kuyruk=[])
    by_id = {k["id"]: k for k in sonuc["kategoriler"]}

    assert plan_mod.siradaki(kats, durum, ayar, simdi) is None
    assert by_id["cpu"]["sonraki"] == "2026-11-14T19:01:00+03:00"
    assert by_id["cpu"]["kalan_sn"] == 60
    assert by_id["gpu"]["sonraki"] == "2026-11-14T19:03:00+03:00"
    assert by_id["ram"]["sonraki"] == "2026-11-14T19:59:00+03:00"

    # 19:01'de en çok gecikmiş olan cpu başlar
    assert plan_mod.siradaki(kats, durum, ayar, _dt(19, 1)) == "cpu"


def test_aralik_taramanin_bitisinden_sayilir():
    """15.11 kullanıcı: seçilen süre biten taramanın ardından dolunca tarama otomatik başlar."""
    kats = [plan_mod.Kategori("gpu", "Ekran kartı", "urun", ["g1"])]
    # 18:00'de başlayıp 18:40'ta biten tarama, 35 dk aralık -> 19:15 (başlangıçtan sayılsa 18:35 olurdu)
    durum = {"gpu": {"son_baslangic": _dt(18, 0).isoformat(), "son_bitis": _dt(18, 40).isoformat()}}
    ayar = {"gpu": 35}
    sonuc = plan_mod.plan_hesapla(kats, durum, ayar, _dt(19, 0), calisan=None, kuyruk=[])
    assert sonuc["kategoriler"][0]["sonraki"] == "2026-11-14T19:15:00+03:00"
    assert plan_mod.siradaki(kats, durum, ayar, _dt(19, 14)) is None
    assert plan_mod.siradaki(kats, durum, ayar, _dt(19, 15)) == "gpu"
    # yarım kalmış tarama (bitiş başlangıçtan eski) -> başlangıçtan sayılır
    durum = {"gpu": {"son_baslangic": _dt(18, 40).isoformat(), "son_bitis": _dt(18, 0).isoformat()}}
    assert plan_mod.siradaki(kats, durum, ayar, _dt(19, 15)) == "gpu"


def test_plan_elle_istek_oncelikli_ve_kilit_suresi():
    kats = [
        plan_mod.Kategori("gpu", "Ekran kartı", "urun", ["g1"]),
        plan_mod.Kategori("ucus", "Uçuşlar", "ucus", []),
    ]
    simdi = _dt(19, 0)
    # 2 dk önce gpu bitti -> 10 dk boşluk kuralı normalde 19:08'e kadar engeller
    durum = {
        "gpu": {"son_baslangic": _dt(18, 55).isoformat(), "son_bitis": _dt(18, 58).isoformat()},
        "ucus": {
            "son_baslangic": _dt(18, 0).isoformat(),
            "son_bitis": _dt(18, 10).isoformat(),
            "manuel_istek_zamani": _dt(18, 59).isoformat(),
            "manuel_son": _dt(18, 59).isoformat(),
        },
    }

    # Elle istek 10 dk boşluk kuralını atlar ve hemen seçilir
    assert plan_mod.siradaki(kats, durum, {}, simdi, calisan=None, kuyruk=["ucus"]) == "ucus"

    sonuc = plan_mod.plan_hesapla(kats, durum, {}, simdi, calisan=None, kuyruk=["ucus"])
    by_id = {k["id"]: k for k in sonuc["kategoriler"]}
    assert by_id["ucus"]["durum"] == "kuyrukta"
    assert by_id["ucus"]["kalan_sn"] == 0
    assert by_id["ucus"]["manuel_kilit_bitis"] == "2026-11-14T19:09:00+03:00"
    assert by_id["ucus"]["manuel_kalan_sn"] == 540

    # Aynı anda bir kategori çalışıyorsa siradaki None döner
    assert plan_mod.siradaki(kats, durum, {}, simdi, calisan="gpu", kuyruk=["ucus"]) is None


def test_tarama_kategori_goc_idempotent(vt: VeriTabani):
    plan_mod.kur(vt)
    plan_mod.kur(vt)
    plan_mod.tarama_basladi(vt, "gpu", "2026-11-14T19:00:00+03:00")
    plan_mod.tarama_bitti(vt, "gpu", "2026-11-14T19:03:00+03:00", {"teklif": 12, "hata": 0, "sure_sn": 180})
    d = plan_mod.durum_oku(vt)
    assert d["gpu"]["son_baslangic"] == "2026-11-14T19:00:00+03:00"
    assert d["gpu"]["son_bitis"] == "2026-11-14T19:03:00+03:00"
    assert d["gpu"]["son_sonuc"] == {"teklif": 12, "hata": 0, "sure_sn": 180}


def test_hic_taranmamis_kategori_vadesi_gecmis_olanlardan_once():
    """15.11 canlı hata: hiç taranmamış kategori 'şimdi' sayılınca vadesi geçmiş gpu hep öne geçiyordu (açlık)."""
    kats = [
        plan_mod.Kategori("gpu", "Ekran kartı", "urun", ["g1"]),
        plan_mod.Kategori("anakart", "Anakart", "urun", ["a1"]),
        plan_mod.Kategori("ssd", "SSD", "urun", ["s1"]),
    ]
    simdi = _dt(19, 0)
    durum = {"gpu": {"son_baslangic": _dt(18, 0).isoformat(), "son_bitis": _dt(18, 10).isoformat()}}   # 30 dk vadesi 18:30, geçmiş
    ayar = {"gpu": 30}
    assert plan_mod.siradaki(kats, durum, ayar, simdi) == "anakart"
    by_id = {k["id"]: k for k in plan_mod.plan_hesapla(kats, durum, ayar, simdi)["kategoriler"]}
    assert by_id["anakart"]["kalan_sn"] == 0 and by_id["ssd"]["sonraki"] < by_id["gpu"]["sonraki"]
    # anakart taranınca sıra ssd'de, gpu ondan sonra
    durum["anakart"] = {"son_baslangic": _dt(19, 0).isoformat(), "son_bitis": _dt(19, 5).isoformat()}
    assert plan_mod.siradaki(kats, durum, ayar, _dt(19, 15)) == "ssd"


# ------------------------------------------------------------------ "aynı anda kategori" (kategori_esz)
def _kat_listesi() -> list:
    return [
        plan_mod.Kategori(id="gpu", ad="Ekran kartı", tur="urun", urunler=["g1", "g2"]),
        plan_mod.Kategori(id="cpu", ad="İşlemci", tur="urun", urunler=["c1"]),
        plan_mod.Kategori(id="ram", ad="Bellek (RAM)", tur="urun", urunler=["r1"]),
        plan_mod.Kategori(id="ucus", ad="Uçuşlar", tur="ucus", urunler=[]),
    ]


def test_siradakiler_n2_iki_vadeli_kategori_birlesir():
    kats = _kat_listesi()
    assert plan_mod.siradakiler(kats, {}, {}, _dt(12, 0), azami=2) == ["gpu", "cpu"]
    assert plan_mod.siradakiler(kats, {}, {}, _dt(12, 0), azami=3) == ["gpu", "cpu", "ram"]


def test_siradakiler_ucus_asla_birlesmez():
    kats = _kat_listesi()
    durum = {
        "ucus": {"son_baslangic": _dt(6, 0).isoformat(), "son_bitis": _dt(6, 5).isoformat()},
        "gpu": {"son_baslangic": _dt(9, 0).isoformat(), "son_bitis": _dt(9, 5).isoformat()},
        "cpu": {"son_baslangic": _dt(11, 50).isoformat(), "son_bitis": _dt(11, 55).isoformat()},
        "ram": {"son_baslangic": _dt(11, 50).isoformat(), "son_bitis": _dt(11, 55).isoformat()},
    }
    # ucus en öncelikli -> tek başına
    assert plan_mod.siradakiler(kats, durum, {}, _dt(12, 30), azami=3) == ["ucus"]
    # ucus en öncelikli değilse gruptan atlanır
    durum["ucus"] = {"son_baslangic": _dt(10, 0).isoformat(), "son_bitis": _dt(10, 5).isoformat()}
    durum["cpu"] = {"son_baslangic": _dt(8, 0).isoformat(), "son_bitis": _dt(8, 5).isoformat()}
    assert plan_mod.siradakiler(kats, durum, {}, _dt(12, 30), azami=3) == ["cpu", "gpu"]


def test_siradakiler_kuyruk_onceligi_tek_basina():
    kats = _kat_listesi()
    assert plan_mod.siradakiler(kats, {}, {}, _dt(12, 0), kuyruk=["ram"], azami=3) == ["ram"]


def test_siradakiler_n1_siradaki_ile_ayni_ve_calisan_bos():
    kats = _kat_listesi()
    assert plan_mod.siradakiler(kats, {}, {}, _dt(12, 0), azami=1) == [plan_mod.siradaki(kats, {}, {}, _dt(12, 0))] == ["gpu"]
    assert plan_mod.siradakiler(kats, {}, {}, _dt(12, 0), calisan=["gpu", "cpu"], azami=2) == []
    durum = {"gpu": {"son_baslangic": _dt(11, 58).isoformat(), "son_bitis": _dt(11, 59).isoformat()}}
    assert plan_mod.siradakiler(kats, durum, {}, _dt(12, 0), azami=2) == []  # 10 dk boşluk


def test_plan_hesapla_n2_birlikte_baslayanlar_ayni_sonraki_ve_gruplar_arasi_bosluk():
    kats = _kat_listesi()
    p = plan_mod.plan_hesapla(kats, {}, {}, _dt(12, 0), azami=2)
    sonraki = {k["id"]: k["sonraki"] for k in p["kategoriler"]}
    assert sonraki["gpu"] == sonraki["cpu"]
    assert sonraki["ram"] != sonraki["gpu"]
    t_gpu = plan_mod.iso_coz(sonraki["gpu"])
    t_ram = plan_mod.iso_coz(sonraki["ram"])
    assert t_gpu <= _dt(12, 0) and t_ram == _dt(12, 0) + timedelta(minutes=plan_mod.MIN_ARALIK_DK)
    # N=1: hepsi ayrı, eskisi gibi
    p1 = plan_mod.plan_hesapla(kats, {}, {}, _dt(12, 0))
    s1 = [k["sonraki"] for k in p1["kategoriler"]]
    assert len(set(s1)) == len(s1)


def test_plan_hesapla_birden_cok_calisan_hepsi_calisiyor():
    kats = _kat_listesi()
    durum = {
        "gpu": {"son_baslangic": _dt(11, 59).isoformat()},
        "cpu": {"son_baslangic": _dt(11, 59).isoformat()},
    }
    p = plan_mod.plan_hesapla(kats, durum, {}, _dt(12, 0), calisan=["gpu", "cpu"], azami=2)
    etiket = {k["id"]: k["durum"] for k in p["kategoriler"]}
    assert etiket["gpu"] == "calisiyor" and etiket["cpu"] == "calisiyor"
    assert etiket["ram"] == "bekliyor"
    calisanlar, kuyruk = plan_mod.aktif_durumlar_coz(durum, _dt(12, 0))
    assert sorted(calisanlar) == ["cpu", "gpu"] and kuyruk == []
