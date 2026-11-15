"""Kural üretimi + eski urunler.json (gerçek ürün tanımları) ile karşılaştırma."""
import json
import re
from pathlib import Path

import pytest
from veri_tanimlama.yardimci import sahte_arama_html

from kazi.modeller import Aday, FiyatBandi, UrunKimligi
from kazi.tanimlama import ayristir, katalog_ara, kurallar_uret
from kazi.tanimlama.kural import STANDART_HARIC, esnek_kod, fiyat_bandi, tr_regex

VERI = Path(__file__).parent / "veri_tanimlama"
ESKI = {u["id"]: u for u in json.loads((VERI / "eski_urunler.json").read_text(encoding="utf-8"))["urunler"]}
GPU_HTML = (VERI / "akakce_rtx5080.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- yardımcılar
def uygun(kural: dict, baslik: str, fiyat: float | None = None) -> bool:
    """eski fiyat/eslestir.py::uygun_mu ile AYNI anlam (dahil HEPSİ, haric HİÇBİRİ, fiyat bandı)."""
    for d in kural.get("dahil") or []:
        if not re.search(d, baslik, re.IGNORECASE):
            return False
    for h in kural.get("haric") or []:
        if re.search(h, baslik, re.IGNORECASE):
            return False
    if fiyat is not None:
        if kural.get("min_fiyat") is not None and fiyat < kural["min_fiyat"]:
            return False
        if kural.get("max_fiyat") is not None and fiyat > kural["max_fiyat"]:
            return False
    return True


def adaylar_uret(sorgu_metni: str, basliklar: list[str], fiyat: float = 10000.0):
    """Gerçek hat: sahte Akakçe sayfası → katalog_ara (puanlama dahil) → adaylar."""
    html = sahte_arama_html([{"code": 100 + i, "name": b, "price": fiyat + i} for i, b in enumerate(basliklar)])
    sorgu = ayristir(sorgu_metni)
    adaylar = katalog_ara(sorgu, lambda u: html)
    assert len(adaylar) == len(basliklar), [a.baslik for a in adaylar]
    return sorgu, adaylar


def aday(kod, baslik, alt, ust=None, **oz):
    return Aday(id=f"akakce:{kod}", baslik=baslik, fiyat_araligi=FiyatBandi(alt=alt, ust=ust), ozellikler=oz)


# her eski ürün: (id, kullanıcı metni, onaylanan adaylar, kabul edilmesi gerekenler, reddedilmesi gerekenler)
URUNLER = {
    "gpu_rtx5080": dict(
        metin="rtx 5080",
        adaylar=["Inno3D RTX 5080 X3 N50803-16D7-176068N 256 Bit GDDR7 16 GB Ekran Kartı",
                 "MSI RTX 5080 16G Ventus 3X OC G5080-16V3C 256 Bit GDDR7 16 GB Ekran Kartı",
                 "Gigabyte RTX 5080 Windforce OC SFF 16G GV-N5080WF3OC-16GD 256 Bit GDDR7 16 GB Ekran Kartı"],
        kabul=["Asus PRIME RTX5080-O16G GeForce RTX 5080 16GB GDDR7 Ekran Kartı",
               "ZOTAC GAMING GeForce RTX 5080 SOLID OC 16GB",
               "Palit GeForce RTX 5080 GameRock OC 16GB 256 Bit GDDR7 Ekran Kartı"],
        red=["Asus ROG Strix SCAR 16 Gaming Laptop RTX 5080 32GB",
             "GIGABYTE GAMING A18 PRO Slim Gaming Notebook RTX 5080 16GB",
             "Gaming PC Ryzen 7 9800X3D RTX 5080 Hazır Sistem",
             "MSI RTX 5080 Ti Gaming Trio 24GB",
             "MSI RTX 5080 Super Ventus 24GB",
             "MSI RTX 5090 Suprim 32GB Ekran Kartı",
             "RTX 5080 Ekran Kartı Su Soğutma Bloğu Waterblock",
             "RTX 5080 12V-2x6 Adaptör Kablo",
             "RTX 5080 Dikey GPU Bracket Tutucu",
             "Zotac RTX 5080 Solid OC Outlet Teşhir Ürünü",
             "Gigabyte RTX 5080 Yenilenmiş Ekran Kartı"],
    ),
    "cpu_9800x3d": dict(
        metin="ryzen 7 9800x3d",
        adaylar=["AMD Ryzen 7 9800X3D 4.7 GHz 96MB AM5 İşlemci 100-100001084WOF",
                 "AMD Ryzen 7 9800X3D 8 Çekirdek 5.2GHz AM5 Kutulu İşlemci"],
        kabul=["Ryzen 7 9800X3D Tray Fansız İşlemci", "AMD 9800X3D 8 Core Soket AM5 Box", "AMD Ryzen 7 9800 X3D"],
        red=["AMD Ryzen 7 9800X3D + MSI B650 Anakart Bundle", "Ryzen 7 9800X3D RTX 5070 Gaming PC Hazır Sistem",
             "AMD Ryzen 7 9850X3D AM5 İşlemci", "AMD Ryzen 7 7800X3D AM5 İşlemci",
             "Ryzen 7 9800X3D Dizüstü Bilgisayar", "9800X3D Anakart Set Paketi", "Ryzen 7 9800X3D Outlet"],
    ),
    "cpu_9850x3d": dict(
        metin="ryzen 7 9850x3d",
        adaylar=["AMD Ryzen 7 9850X3D 4.7 GHz AM5 İşlemci Kutulu"],
        kabul=["AMD Ryzen 7 9850X3D Tray"],
        red=["AMD Ryzen 7 9800X3D Tray", "Ryzen 7 9850X3D Bundle Anakart", "Ryzen 7 9850X3D Notebook"],
    ),
    "cpu_9950x3d": dict(
        metin="ryzen 9 9950x3d",
        adaylar=["AMD Ryzen 9 9950X3D 16 Çekirdek AM5 Kutulu İşlemci", "AMD Ryzen 9 9950X3D Tray İşlemci"],
        kabul=["Ryzen 9 9950X3D 4.3 GHz AM5"],
        red=["Ryzen 9 9950X Tray", "Ryzen 9 9950X3D RTX 5090 Hazır Sistem", "Ryzen 9 9950X3D Set"],
    ),
    "ram_64gb_ddr5": dict(
        metin="AM5'e uyan QVL'li 2x32 ddr5 6000 cl30",
        adaylar=["G.Skill Trident Z5 Neo RGB 64GB (2x32GB) DDR5 6000MHz CL30 F5-6000J3040G32GX2-TZ5NR",
                 "XPG Lancer RGB 64GB (2x32GB) DDR5 6000MHz CL30 AX5U6000C3032G-DCLARBK",
                 "Kingston FURY Beast EXPO 64GB (2x32GB) DDR5 6000MT/s CL30 KF560C30BBEK2-64"],
        kabul=["Corsair Vengeance RGB 64GB (2x32GB) DDR5 6000MHz CL30 Siyah",
               "G.SKILL Trident Z5 RGB 64GB 2x32GB DDR5-6000 CL 30",
               "TeamGroup T-Create Expert 2x32GB 6000MT/s C30 DDR5"],
        red=["Crucial 64GB DDR5 6000MHz CL30 SO-DIMM Laptop Bellek",
             "Kingston FURY Beast 64GB (2x32GB) DDR5 6000MT/s CL36",
             "Kingston FURY Renegade 64GB 1x64GB DDR5 6000MHz CL30",
             "G.Skill Trident Z5 32GB (2x16GB) DDR5 6000MHz CL30",
             "Corsair Vengeance 64GB (2x32GB) DDR5 5600MHz CL30",
             "Corsair Vengeance 64GB (2x32GB) DDR5 6000MHz CL40",
             "Oyuncu Bilgisayarı 64GB DDR5 6000MHz CL30 RAM Hazır Sistem",
             "Kingston Fury Beast 64GB (2x32GB) DDR4 3200MHz CL16"],
    ),
    "anakart_x870": dict(
        metin="Gigabyte X870 Aorus Elite WiFi 7 anakart",
        adaylar=["Gigabyte X870 AORUS ELITE WIFI7 AM5 DDR5 ATX Anakart"],
        kabul=["GIGABYTE X870 AORUS ELITE WIFI7 Wi-Fi 7 Bluetooth 5.4 Anakart", "Gigabyte X870 Aorus Elite Wi-Fi 7 AM5"],
        red=["Gigabyte X870E AORUS ELITE WIFI7 AM5 Anakart", "Gigabyte X870 AORUS ELITE WIFI6 AM5 Anakart",
             "Gigabyte X870 Aorus Elite WIFI7 + Ryzen 7 9800X3D Bundle",
             "Gigabyte B650 AORUS ELITE AX V2 AM5 Anakart", "MSI X870 Tomahawk WIFI AM5 Anakart"],
    ),
    "ssd_gen5_1tb": dict(
        metin="1tb nvme m.2 pcie 5.0 ssd",
        adaylar=["Samsung 9100 PRO 1TB M.2 NVMe PCIe 5.0 SSD MZ-VAP1T0BW",
                 "Crucial T705 1TB PCIe Gen5 NVMe M.2 SSD CT1000T705SSD3"],
        kabul=["Samsung 9100 Pro 1TB PCIe 5.0 NVMe M.2 SSD Soğutuculu", "Kingston FURY Renegade G5 1TB PCIe Gen5 M.2 SSD"],
        red=["Samsung 9100 PRO 2TB M.2 NVMe PCIe 5.0 SSD", "Crucial T705 4TB PCIe Gen5 NVMe M.2",
             "Samsung 990 Pro 1TB PCIe 4.0 NVMe M.2 SSD", "Samsung T7 Shield Harici SSD 1TB USB 3.2 PCIe 5.0 Gen5",
             "Crucial X10 External SSD 1TB Gen5"],
    ),
    "psu_1000w": dict(
        metin="1000w 80+ gold atx 3.1 psu",
        adaylar=["Corsair RM1000x SHIFT 1000W 80+ Gold ATX 3.1 Tam Modüler PSU",
                 "Asus TUF Gaming 1000G 1000W 80+ Gold ATX 3.1 Tam Modüler PSU"],
        kabul=["Corsair RM1000x 2024 ATX 3.1 80 PLUS Gold 1000 W Güç Kaynağı",
               "MSI MAG A1000GL PCIE5 1000W 80+ Gold ATX 3.1 PSU"],
        red=["MSI MAG A850GL 850W 80+ Gold ATX 3.1 PSU", "Corsair RM1000x 1000W ATX 2.4 80+ Gold",
             "1000W 80+ Gold ATX 3.1 Güç Kaynağı Kasa Birlikte", "Corsair RM1000x 1000W 80+ Gold ATX 3.1 Outlet"],
    ),
    "sogutucu_lf3_360": dict(
        metin="Arctic Liquid Freezer III Pro 360 sıvı soğutma",
        adaylar=["Arctic Liquid Freezer III Pro 360 A-RGB İşlemci Sıvı Soğutucu ACFRE00180A",
                 "Arctic Liquid Freezer III Pro 360 Siyah İşlemci Sıvı Soğutucu ACFRE00136A"],
        kabul=["ARCTIC Liquid Freezer III Pro 360 AIO CPU Water Cooler", "Arctic Liquid Freezer III Pro 360 Sıvı Soğutma"],
        red=["Arctic Liquid Freezer III Pro 280 Sıvı Soğutucu", "Arctic Liquid Freezer II 360 Sıvı Soğutucu",
             "Arctic Liquid Freezer III Pro 360 Fan Paketi", "Arctic Liquid Freezer III Pro 360 Notebook"],
    ),
    "kasa_lancool3": dict(
        metin="Lian Li Lancool III kasa",
        adaylar=["Lian Li LANCOOL III RGB Mid Tower Kasa Siyah", "Lian Li LANCOOL III Mid Tower Kasa Beyaz"],
        kabul=["Lian Li Lancool 3 RGB Mid Tower Gaming Kasa", "LIAN LI LANCOOL III Siyah Kasa Tempered Cam"],
        red=["Lian Li Lancool 216 RGB Kasa", "Lian Li Lancool II Mesh Kasa", "Lian Li Lancool 215 Kasa",
             "Lian Li Lancool III Dizüstü Bilgisayar Oyuncu Bilgisayar Hazır Sistem"],
    ),
}


# Bilinçli farklar: "Super" varyantı eski 5080 kuralında yoktu (yeni reddeder); eski RAM kuralı yalnız "64GB"
# yazan başlığı kabul ederdi, yenisi "2x32GB" yazanı da kabul eder.
BILINEN_FARK = {"gpu_rtx5080": {"MSI RTX 5080 Super Ventus 24GB"},
                "ram_64gb_ddr5": {"TeamGroup T-Create Expert 2x32GB 6000MT/s C30 DDR5"}}


def test_eski_urunlerin_hepsi_kapsandi():
    assert set(URUNLER) == set(ESKI)


@pytest.mark.parametrize("uid", list(URUNLER))
def test_uretilen_kural_eski_gecerli_basliklari_kabul_eder(uid):
    v = URUNLER[uid]
    sorgu, adaylar = adaylar_uret(v["metin"], v["adaylar"])
    kural = kurallar_uret(adaylar, sorgu)
    assert kural["mod"] == "ozellik" and kural["dahil"] and kural["haric"]
    for b in v["adaylar"] + v["kabul"]:
        assert uygun(kural, b), f"{uid}: kabul edilmeliydi: {b}\n{kural['dahil']}"
    for b in v["red"]:
        assert not uygun(kural, b), f"{uid}: reddedilmeliydi: {b}"


@pytest.mark.parametrize("uid", list(URUNLER))
def test_uretilen_kural_eski_kuralla_ayni_karar(uid):
    """Karşılaştırmalı: eski elle yazılmış kural ile üretilen kural, tüm örnek başlıklarda AYNI karar."""
    v = URUNLER[uid]
    sorgu, adaylar = adaylar_uret(v["metin"], v["adaylar"])
    yeni, eski = kurallar_uret(adaylar, sorgu), ESKI[uid]
    for b in v["adaylar"] + v["kabul"] + v["red"]:
        if b in BILINEN_FARK.get(uid, ()):
            continue
        assert uygun(yeni, b) == uygun(eski, b), f"{uid} ayrışma: {b!r}  yeni={uygun(yeni, b)} eski={uygun(eski, b)}"


def test_bilinen_fark_cl_yalniz_mpn_kodunda():
    """Bilinçli fark: eski kural 'J3040' MPN parçasından CL30 sayardı; yenisi yazılı CL ister."""
    sorgu, adaylar = adaylar_uret(URUNLER["ram_64gb_ddr5"]["metin"], URUNLER["ram_64gb_ddr5"]["adaylar"])
    yeni, eski = kurallar_uret(adaylar, sorgu), ESKI["ram_64gb_ddr5"]
    b = "G.Skill Trident Z5 Neo 64GB 2x32GB DDR5 6000 F5-6000J3040G32GX2-TZ5NR"
    assert uygun(eski, b) and not uygun(yeni, b)


def test_qvl_eski_alanla_ayni_deger():
    sorgu, adaylar = adaylar_uret(URUNLER["ram_64gb_ddr5"]["metin"], URUNLER["ram_64gb_ddr5"]["adaylar"])
    assert kurallar_uret(adaylar, sorgu)["qvl"] == ESKI["ram_64gb_ddr5"]["qvl"] == "bellek"
    sorgu2 = ayristir("1tb nvme pcie 5.0 qvl ssd")
    assert kurallar_uret([], sorgu2)["qvl"] == "ssd"
    k = kurallar_uret([], ayristir("anakart wifi 7 qvl"))
    assert "qvl" not in k and k["uyumluluk"] == {"qvl": True}


# ---------------------------------------------------------------- gerçek Akakçe sayfası: "Bunların hepsi"
def test_hepsi_gercek_sayfa_rtx5080():
    sorgu = ayristir("rtx 5080")
    adaylar = katalog_ara(sorgu, lambda u: GPU_HTML)
    assert len(adaylar) >= 10
    kural = kurallar_uret(adaylar, sorgu)
    assert kural["kategori"] == "gpu" and kural["kanonik_ad"] == "Ekran kartı RTX 5080"
    for a in adaylar:                                       # her onaylı aday kabul
        assert uygun(kural, a.baslik, a.fiyat_araligi.alt), a.baslik
    # sayfadaki laptop ve hazır sistem başlıkları reddedilir
    for b in ("GIGABYTE GAMING A18 PRO Slim Gaming Notebook, 18 inç 165Hz QHD 16:10 3ms IPS Ekran, NVIDIA GeForce RTX 5080 16GB 115W",
              "Asus ROG G700 GM700TZ-R9800X1080 Ryzen 7 9800X3D 16 GB 1 TB SSD RTX5080 Masaüstü Oyun Bilgisayarı",
              "GG NOVA-5090 / AMD Ryzen 9 9900X3D / GIGABYTE GeForce RTX 5080 16G / 32GB DDR5 / 2TB NVME M.2 SSD / OEM PAKET"):
        assert not uygun(kural, b), b
    # fiyat bandı 0.6x – 2.5x
    alt = [a.fiyat_araligi.alt for a in adaylar]
    assert kural["min_fiyat"] <= min(alt) * 0.6 + 100 and kural["min_fiyat"] >= min(alt) * 0.6 - 100
    assert kural["max_fiyat"] >= max(alt) * 2.5 and kural["max_fiyat"] <= max(alt) * 2.5 + 100
    assert not uygun(kural, adaylar[0].baslik, kural["min_fiyat"] - 1)
    assert uygun(kural, adaylar[0].baslik, kural["min_fiyat"] + 1)


# ---------------------------------------------------------------- tek aday (MPN)
def test_tek_aday_mpn_kipi():
    a = aday(1, "MSI RTX 5080 16G Ventus 3X OC G5080-16V3C 256 Bit GDDR7 16 GB Ekran Kartı", 133549.0,
             mpn=None, kategori="gpu", model="RTX 5080")
    a = a.model_copy(update={"mpn": "G5080-16V3C"})
    kural = kurallar_uret([a], ayristir("rtx 5080"))
    assert kural["mod"] == "mpn" and kural["mpn"] == ["G5080-16V3C"] and kural["kanonik_ad"] == a.baslik
    assert uygun(kural, a.baslik)
    assert uygun(kural, "MSI GeForce RTX 5080 VENTUS 3X OC G5080-16V3C")
    assert uygun(kural, "MSI RTX5080 g5080 16v3c 16GB")                  # boşluk/tire toleransı
    assert not uygun(kural, "MSI RTX 5080 16G Shadow 3X OC G5080-16S3C")      # başka MPN
    assert not uygun(kural, "MSI RTX 5080 Ventus 3X OC G5080-16V3C Laptop")


def test_tek_aday_mpn_yoksa_ozellik_kipi():
    a = aday(1, "Corsair RM1000x 1000W 80+ Gold ATX 3.1", 7000.0, watt=1000, **{"80plus": "Gold", "atx": "3.1"})
    kural = kurallar_uret([a], ayristir("1000w gold atx 3.1 psu"))
    assert kural["mod"] == "ozellik" and uygun(kural, a.baslik)


# ---------------------------------------------------------------- ortak özellik, hariç, bant
def test_coklu_aday_ortak_ozellikler():
    adaylar = [aday(1, "A 64GB (2x32GB) DDR5 6000MHz CL30", 60000.0, kit="2x32", kapasite_gb=64, hiz_mts=6000, cl=30,
                    bellek_tipi="DDR5", kategori="ram"),
               aday(2, "B 64GB (2x32GB) DDR5 6000MHz CL32", 52000.0, kit="2x32", kapasite_gb=64, hiz_mts=6000, cl=32,
                    bellek_tipi="DDR5", kategori="ram")]
    kural = kurallar_uret(adaylar, ayristir("ddr5 ram"))
    assert uygun(kural, "X 64GB DDR5 6000MHz CL30") and uygun(kural, "X 2x32 DDR5 6000 CL36")   # CL ortak değil → serbest
    assert not uygun(kural, "X 32GB (2x16GB) DDR5 6000MHz") and not uygun(kural, "X 64GB DDR5 5600MHz")
    assert not uygun(kural, "X 64GB (1x64GB) DDR5 6000MHz")                                     # farklı düzen


def test_sorgu_haric_kurala_girer():
    sorgu = ayristir("ram rgb olmasın 2x32 ddr5 hazır sistem hariç")
    kural = kurallar_uret([], sorgu)
    assert not uygun(kural, "Corsair 64GB (2x32GB) DDR5 RGB Ram")
    assert not uygun(kural, "Hazır Sistem 64GB (2x32GB) DDR5")
    assert uygun(kural, "Corsair 64GB (2x32GB) DDR5 Ram")


def test_standart_haric_turkce_buyuk_harf():
    kural = kurallar_uret([], ayristir("rtx 5080"))
    for b in ("RTX 5080 DİZÜSTÜ", "RTX 5080 HAZIR SİSTEM", "RTX 5080 TEŞHİR", "RTX 5080 OUTLET", "RTX 5080 İKİNCİ EL",
              "RTX 5080 YENİLENMİŞ", "rtx 5080 kutusu hasarlı"):
        assert not uygun(kural, b), b
    assert uygun(kural, "ASUS RTX 5080 PRIME")
    for p in STANDART_HARIC:
        re.compile(p)


def test_fiyat_bandi_carpanlari():
    adaylar = [aday(1, "a", 100000.0), aday(2, "b", 120000.0, ust=150000.0)]
    assert fiyat_bandi(adaylar) == (60000.0, 375000.0)
    assert fiyat_bandi([aday(1, "a", 12345.0)]) == (7400.0, 30900.0)
    assert fiyat_bandi([]) == (None, None)
    kural = kurallar_uret(adaylar, ayristir("rtx 5080"))
    assert (kural["min_fiyat"], kural["max_fiyat"]) == (60000.0, 375000.0)


def test_kural_json_ve_kimlik_uyumu():
    sorgu, adaylar = adaylar_uret(URUNLER["ram_64gb_ddr5"]["metin"], URUNLER["ram_64gb_ddr5"]["adaylar"])
    kural = kurallar_uret(adaylar, sorgu)
    json.dumps(kural)
    for d in kural["dahil"] + kural["haric"]:
        re.compile(d, re.IGNORECASE)
    kimlik = UrunKimligi(kanonik_ad=kural["kanonik_ad"], eslesme_kurallari=kural,
                         fiyat_bandi=FiyatBandi(alt=kural["min_fiyat"], ust=kural["max_fiyat"]))
    assert kimlik.kanonik_ad.startswith("RAM") and "64GB (2x32)" in kimlik.kanonik_ad
    assert len(set(kural["dahil"])) == len(kural["dahil"]) and len(set(kural["haric"])) == len(kural["haric"])


def test_yardimcilar():
    assert re.search(esnek_kod("G5080-16V3C"), "msi g5080 16v3c", re.I)
    assert not re.search(esnek_kod("9800X3D"), "9800X3DX", re.I)
    assert re.search(tr_regex("teşhir"), "TESHIR ürün", re.I) and re.search(tr_regex("ıslak"), "ISLAK", re.I)
    assert re.search(tr_regex("hazır sistem"), "HAZIR   SİSTEM", re.I)


def test_aday_yok_ve_model_yok():
    kural = kurallar_uret([], ayristir("kırmızı çorap"))
    assert kural["dahil"] == [] and kural["min_fiyat"] is None and kural["kategori"] is None
