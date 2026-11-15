"""Bileşen ürününde hazır sistem / dizüstü ilanı elenir (15.11, canlı RTX 5080 verisinden örnekler)."""
import pytest

from kazi.eslestir import neden, sistem_ilani_mi
from kazi.siteler._kart import Kart

GPU = {"dahil": ["5080"], "haric": ["5080\\s*ti"], "kategori": "gpu", "min_fiyat": 90000, "max_fiyat": 260000}


def _kart(baslik, fiyat=150000.0):
    return Kart(site="n11", baslik=baslik, sepet_fiyati=fiyat, url="https://n11.com/x")


@pytest.mark.parametrize("baslik", [
    "Asus Rog Strıx G18 Intel Core Ultra 9 275HX 32GB 1tb SSD Rtx 5080 16GB 18\" Wqxga 240Hz",
    "GamersArena Asga RTX 5080 Ryzen 7 7800X3D 16 GB 1 TB M2 SSD 850W OEM PAKET",
    "Dragos DRx9521 i7 14700KF 16GB 512GB NVMe 16GB RTX5080 Oyuncu Oem Paket",
    "Game Garaj Titan R7 AMD R7 9800X3D RTX5080 32GB RAM 1TB SSD 360mm Sıvı Soğt FreeDOS Oyuncu",
    "Acer Predator PH18-73 Core Ultra9 275HX 128 GB 3tb RTX5080 18''wqxga IPS 250Hz Freedos",
    "Lenovo Legion Pro 7 AMD Ryzen 9 9955HX3D 32GB 1TB SSD RTX5080 Freedos 16\" Taşınabilir Bilgisayar",
    "VOLKAN ÖGE-5080",
    "ELITE-TUF5080",
    "Rubinum - RTX5080",
    "RTX5080 - Aufhellung",
    "November 7A - RTX5080",
])
def test_gpu_sistem_ilanlari_elenir(baslik):
    g = neden(GPU, _kart(baslik))
    assert g and g.startswith("hazır sistem ilanı")


@pytest.mark.parametrize("baslik", [
    "ASUS ROG Astral GeForce RTX 5080 OC 16GB GDDR7 256 Bit DLSS 4 Ekran Kartı",
    "Asus PRIME-RTX5080-O16G 16GB Gddr7 256BIT 1xhdmı 3xdp Ekran Kartı",
    "MSI Geforce Rtx 5080 Ventus 3x Oc Plus 16G Gddr7 256BIT Dlss 4 Ekran Kartı",
    "Palit GeForce RTX 5080 GamingPro 16GB GDDR7",
    "PRIME-RTX5080-O16G RTX5080 16GB GDDR7 256Bit 3xDP/1xHDMI",
    "Lenovo RTX 5080 4X61U02755 GDDR7 16 GB Ekran Kartı",
    "GeForce RTX 5080 WINDFORCE OC SFF 16G GV-N5080WF3OC-16GD 256bit GDDR7",
])
def test_gercek_ekran_kartlari_gecer(baslik):
    assert neden(GPU, _kart(baslik)) is None


@pytest.mark.parametrize("kategori,baslik", [
    ("cpu", "AMD Ryzen 7 9800X3D 4.70-5.20GHz 8/16 AM5 DDR5 120 W Soğutucu Yok İşlemci"),
    ("ram", "Kingston Fury Beast 64GB (2x32GB) DDR5 6000MHz CL30 EXPO"),
    ("ssd", "Samsung 9100 Pro 1TB PCIe 5.0 NVMe M.2 SSD"),
    ("anakart", "Gigabyte X870 AORUS ELITE WIFI7 AMD AM5 DDR5 Ryzen 9000 ATX Anakart"),
    ("kasa", "Lian Li Lancool III ARGB Fanlı E-ATX Gaming Bilgisayar Kasası"),
])
def test_kendi_kategorisinin_izi_elemez(kategori, baslik):
    assert sistem_ilani_mi(kategori, baslik) is None


def test_cpu_hazir_sistemde_elenir_ve_izin_kapatir():
    assert sistem_ilani_mi("cpu", "Ryzen 7 9800X3D 32GB DDR5 1TB NVMe RTX5080 Oyuncu Oem Paket")
    k = {**GPU, "sistem_ilanina_izin": True}
    assert neden(k, _kart("VOLKAN ÖGE-5080")) is None
    assert sistem_ilani_mi(None, "Ryzen 7 RTX 5080 1TB SSD") is None
