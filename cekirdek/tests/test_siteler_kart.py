"""Kart ara nesnesi, tl_sayi, Kart.teklife dönüşümü ve SITELER kayıt defteri testleri."""
from __future__ import annotations

from kazi.modeller import Katman, Teklif, TeyitDurumu
from kazi.siteler import SITELER, Kart, tl_sayi
from kazi.siteler._kart import Kart as KartDogrudan

BEKLENEN_18_SITE = {
    "akakce", "amazon", "cimri", "epey", "gamegaraj", "gaminggentr",
    "hepsiburada", "idefix", "incehesap", "itopya", "mediamarkt", "n11",
    "pazarama", "pttavm", "sinerji", "teknosa", "trendyol", "vatan",
}


def test_tl_sayi_turkce_bicim():
    assert tl_sayi("119.559,02 TL") == 119559.02
    assert tl_sayi("112.209 TL") == 112209.0
    assert tl_sayi("₺1.299,90") == 1299.90
    assert tl_sayi("13 099 TL") == 13099.0


def test_tl_sayi_temizlik_ve_bos():
    assert tl_sayi("1.299,90\xa0TL") == 1299.90
    assert tl_sayi("₺ 45.000") == 45000.0
    assert tl_sayi("") is None
    assert tl_sayi(None) is None
    assert tl_sayi("fiyat yok") is None


def test_tl_sayi_sayi_girisi():
    assert tl_sayi(1234.5) == 1234.5
    assert tl_sayi("1500") == 1500.0


def test_kart_varsayilanlari():
    assert Kart is KartDogrudan
    k = Kart(site="n11", baslik="RTX 5080", url="https://www.n11.com/urun/x", sepet_fiyati=119999)
    assert k.sepet_fiyati == 119999.0
    assert k.yontem == "http"
    assert k.liste_fiyati is None
    assert k.pesin_taksit is None
    assert k.stok is None
    assert k.durum == "gecerli"
    assert k.zaman.endswith("+03:00")


def test_kart_teklife_katmanlar_ve_alanlar():
    k_http = Kart(
        site="n11",
        baslik="Inno3D RTX 5080 X3",
        url="https://www.n11.com/urun/inno3d-5080",
        sepet_fiyati=108499.0,
        liste_fiyati=114999.0,
        satici="GarajOnline",
        pesin_taksit=6,
        stok=True,
        yontem="http",
        zaman="2026-11-14T15:07:00+03:00",
    )
    t0 = k_http.teklife("gpu_rtx5080")
    assert isinstance(t0, Teklif)
    assert t0.urun_id == "gpu_rtx5080"
    assert t0.site == "n11"
    assert t0.satici == "GarajOnline"
    assert t0.baslik == "Inno3D RTX 5080 X3"
    assert t0.url == "https://www.n11.com/urun/inno3d-5080"
    assert t0.sepet_fiyati == 108499.0
    assert t0.liste_fiyati == 114999.0
    assert t0.pesin_fiyatina_taksit is True
    assert t0.stok is True
    assert t0.kaynak_katmani == Katman.K0
    assert t0.teyit_durumu == TeyitDurumu.bekliyor
    assert t0.gozlem_zamani == "2026-11-14T15:07:00+03:00"

    k_chr = Kart(site="itopya", baslik="RTX 5080", url="https://itopya.com/x",
                 sepet_fiyati=120000, yontem="chromium")
    assert k_chr.teklife("gpu_rtx5080").kaynak_katmani == Katman.K1

    k_brv = Kart(site="trendyol", baslik="RTX 5080", url="https://trendyol.com/x",
                 sepet_fiyati=115000, yontem="brave")
    assert k_brv.teklife("gpu_rtx5080").kaynak_katmani == Katman.K2


def test_kart_teklife_toplayici_donusumu():
    # Toplayıcıda satıcı biliniyorsa satıcı korunur, site = kaynak olur
    k1 = Kart(
        site="akakce",
        baslik="Zotac RTX 5080",
        url="https://www.akakce.com/r/?pr=1",
        sepet_fiyati=112000.0,
        satici="magicteknoloji",
        kaynak="n11",
        yontem="http",
    )
    t1 = k1.teklife("gpu_rtx5080")
    assert t1.site == "n11"
    assert t1.satici == "magicteknoloji"

    # Toplayıcıda satıcı yoksa "<toplayıcı> üzerinden" yazılır
    k2 = Kart(
        site="cimri",
        baslik="Gigabyte RTX 5080",
        url="https://www.hepsiburada.com/x",
        sepet_fiyati=118000.0,
        satici=None,
        kaynak="Hepsiburada",
        yontem="brave",
    )
    t2 = k2.teklife("gpu_rtx5080")
    assert t2.site == "hepsiburada"          # 14.11: mağaza adı tek anahtara indirgenir (site_adi.py)
    assert t2.satici == "cimri üzerinden"
    assert t2.kaynak_katmani == Katman.K2


def test_siteler_kayit_defteri_18_modul():
    assert set(SITELER.keys()) == BEKLENEN_18_SITE
    for ad, mod in SITELER.items():
        assert getattr(mod, "SITE", None) == ad
        assert callable(getattr(mod, "ara", None)), f"{ad}.ara eksik"
        assert callable(getattr(mod, "urun", None)), f"{ad}.urun eksik"
        assert callable(getattr(mod, "teklifler", None)), f"{ad}.teklifler eksik"
        assert isinstance(getattr(mod, "ANA_URL", None), str) and mod.ANA_URL.startswith("https://")
        assert isinstance(getattr(mod, "dogrudan_url_yasak", None), bool)


def test_sorgu_varyantlari_parantezde_secenekler():
    from kazi.siteler._kart import sorgu_varyantlari
    assert sorgu_varyantlari("SSD 1TB PCIe 5.0 NVMe (Samsung 9100 Pro / Crucial T705)") == [
        "Samsung 9100 Pro 1TB", "Crucial T705 1TB", "SSD 1TB PCIe 5.0 NVMe"]


def test_sorgu_varyantlari_duz_sorgu_degismez():
    from kazi.siteler._kart import sorgu_varyantlari
    assert sorgu_varyantlari("Ekran kartı RTX 5080") == ["Ekran kartı RTX 5080"]
    assert sorgu_varyantlari("RAM 64GB (2×32) DDR5-6000") == ["RAM 64GB (2×32) DDR5-6000"]
