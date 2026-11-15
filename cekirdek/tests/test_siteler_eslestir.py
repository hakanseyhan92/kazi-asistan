"""Eşleştirme kuralları (dahil/haric/min/max/qvl) ve elenme gerekçesi (neden) testleri."""
from __future__ import annotations

from kazi.eslestir import eslesenler, neden, urun_getir, urunler_yukle, uygun_mu
from kazi.modeller import UrunKimligi
from kazi.siteler import Kart, qvl


def _k(baslik: str, fiyat: float, site: str = "n11") -> Kart:
    return Kart(site=site, baslik=baslik, sepet_fiyati=fiyat, url="https://ornek/urun")


def test_urunler_yuklenir():
    urunler = urunler_yukle()
    assert any(u["id"] == "gpu_rtx5080" for u in urunler)
    assert urun_getir("gpu_rtx5080")["kategori"] == "gpu"
    assert urun_getir("yok_boyle") is None


def test_gpu_rtx5080_uygun_ve_neden_none():
    u = urun_getir("gpu_rtx5080")
    kart = _k("MSI GeForce RTX 5080 16G Ventus 3X OC", 120000)
    assert uygun_mu(u, kart)
    assert neden(u, kart) is None


def test_urun_kimligi_eslesme_kurallari_ile():
    u = urun_getir("gpu_rtx5080")
    kimlik = UrunKimligi(
        id="gpu_rtx5080",
        kanonik_ad=u["ad"],
        eslesme_kurallari={
            "dahil": u["dahil"],
            "haric": u["haric"],
            "min_fiyat": u["min_fiyat"],
            "max_fiyat": u["max_fiyat"],
        },
    )
    iyi = _k("MSI GeForce RTX 5080 16G Ventus 3X OC", 120000)
    kotu = _k("ASUS ROG Strix RTX 5080 Laptop", 120000)
    assert uygun_mu(kimlik.eslesme_kurallari, iyi)
    assert neden(kimlik.eslesme_kurallari, iyi) is None
    assert not uygun_mu(kimlik.eslesme_kurallari, kotu)
    gerekce = neden(kimlik.eslesme_kurallari, kotu)
    assert gerekce is not None and "laptop" in gerekce.lower()
    assert eslesenler(kimlik.eslesme_kurallari, [iyi, kotu]) == [iyi]


def test_gpu_rtx5080_laptop_elendi():
    u = urun_getir("gpu_rtx5080")
    k = _k("RTX 5080 Laptop", 120000)
    assert not uygun_mu(u, k)
    assert "laptop" in (neden(u, k) or "").lower()


def test_gpu_rtx5080_hazir_pc_elendi():
    u = urun_getir("gpu_rtx5080")
    k = _k("Oyuncu Bilgisayarı RTX 5080 9800X3D", 120000)
    assert not uygun_mu(u, k)
    assert neden(u, k) is not None


def test_gpu_rtx5080_aksesuar_elendi():
    u = urun_getir("gpu_rtx5080")
    k = _k("RTX 5080 su soğutma bloğu", 120000)
    assert not uygun_mu(u, k)
    assert neden(u, k) is not None


def test_gpu_rtx5080_fiyat_araligi():
    u = urun_getir("gpu_rtx5080")
    ucuz = _k("RTX 5080 16G Ventus 3X OC", 50000)
    pahali = _k("RTX 5080 16G Ventus 3X OC", 300000)
    assert not uygun_mu(u, ucuz)
    assert "alt" in (neden(u, ucuz) or "").lower()
    assert not uygun_mu(u, pahali)
    assert "üst" in (neden(u, pahali) or "").lower()


def test_ram_64gb_uygun():
    u = urun_getir("ram_64gb_ddr5")
    assert uygun_mu(u, _k("GSKILL 64GB (2x32) TRIDENT Z5 NEO RGB DDR5 6000mhz CL30 Dual Ram", 87899))
    assert uygun_mu(u, _k("G.Skill Trident Neo RGB F5-6000J3040G32GX2-TZ5NR 64 GB (2x32) DDR5 6000 MHz CL30 Ram", 95133))
    assert not uygun_mu(u, _k("GSKILL 64GB (2x32GB) TRIDENT Z5 RGB BEYAZ DDR5 6000MHz CL30 1.40V F5-6000J3040G32GX2-TZ5RW", 87939))
    assert not uygun_mu(u, _k("G.Skill Trident Z5 RGB 64GB (2x32) DDR5 6400 CL32 F5-6400J3239G32GX2-TZ5RK", 69919))
    assert uygun_mu(u, _k("Kingston Beast Expo KF560C30BBEK2-64TR 64 GB DDR5 6000 MHz CL30", 93175))
    k_qvl_disi = _k("Kingston Fury Beast 64GB 6000MHz CL30 KF560C30BBK2-64", 90000)
    assert not uygun_mu(u, k_qvl_disi)
    assert "qvl" in (neden(u, k_qvl_disi) or "").lower()


def test_ram_32gb_elendi():
    u = urun_getir("ram_64gb_ddr5")
    assert not uygun_mu(u, _k("32GB DDR5 6000", 60000))


def test_ram_sodimm_elendi():
    u = urun_getir("ram_64gb_ddr5")
    assert not uygun_mu(u, _k("64GB DDR5 5600 SO-DIMM", 60000))


def test_site_kisiti():
    u = {"id": "x", "dahil": ["5080"], "haric": [], "min_fiyat": 0, "max_fiyat": 999999,
         "siteler": ["n11"]}
    assert uygun_mu(u, _k("RTX 5080", 120000, site="n11"))
    k_dis = _k("RTX 5080", 120000, site="trendyol")
    assert not uygun_mu(u, k_dis)
    assert "site" in (neden(u, k_dis) or "").lower()


def test_anakart_secilen_model_x870_aorus_elite_wifi7():
    u = urun_getir("anakart_x870")
    assert uygun_mu(u, _k("Gigabyte X870 Aorus Elite WIFI7 AMD AM5 DDR5 ATX Anakart", 14199))
    assert uygun_mu(u, _k("Gıgabyte X870 Aorus Elıte Wıfı7 Am5 Atx Anakart", 14535))
    assert uygun_mu(u, _k("X870 AORUS ELITE WIFI7 ICE AMD X870 Soket AM5 DDR5 8000Mhz (OC) USB4 WIFI 7", 17999))
    assert not uygun_mu(u, _k("Gigabyte X870 Gaming X WIFI7 AMD AM5 DDR5 ATX Anakart", 12219))
    assert not uygun_mu(u, _k("X870E-A-ELT-WIFI7 AORUS ELİTE WIFI7 HDMI USB AM5", 16999))
    assert not uygun_mu(u, _k("Gigabyte X870 Aorus Elite X3D Ice AMD AM5 DDR5 ATX Anakart", 20261))
    assert not uygun_mu(u, _k("Gigabyte X870 Aorus Elite WIFI7 AMD AM5 Anakart Outlet", 12000))


def test_psu_atx3_sart():
    u = urun_getir("psu_1000w")
    assert uygun_mu(u, _k("Corsair RM1000e 1000W 80+ Gold ATX 3.1 PCIe 5.1 Tam Modüler", 9000))
    assert uygun_mu(u, _k("ASUS TUF-GAMING-1000G 1000W 80+ Gold Full Modüler ATX 3.0", 9399))
    assert not uygun_mu(u, _k("Corsair RM1000x 1000W 80+ Gold Tam Modüler CP-9020201", 9000))


def test_klevv_takipten_cikarildi():
    assert urun_getir("ram_klevv_6400") is None


def test_qvl_verisi_ve_etiketler():
    m = qvl.meta()
    assert m["bellek_tablo"]["id"] == 2630 and len(qvl.bellek_satirlari()) > 1500
    assert len(qvl.ssd_satirlari()) > 500
    assert qvl.etiket("bellek", "G.Skill Trident Neo RGB F5-6000J3040G32GX2-TZ5NR 64 GB DDR5 6000 CL30") == "✅ QVL"
    assert qvl.etiket("bellek", "GSKILL 64GB (2x32) TRIDENT Z5 NEO RGB DDR5 6000mhz CL30 Dual Ram") == "✅ QVL(model)"
    assert qvl.etiket("ssd", "Samsung MZ-VAP1T0BW 9100 PRO 1TB PCIe 5.0") == "✅ QVL"
    assert qvl.etiket("ssd", "Crucial T705 CT1000T705SSD5 1TB Gen5") == "✅ QVL"
    assert qvl.eslestir("ssd", "Samsung 990 Pro 1TB MZ-V9P1T0BW") is None
