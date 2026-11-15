from kazi.site_adi import okunur_ad, site_anahtari


def test_ayni_magaza_tek_anahtar():
    for ad in ("Hepsiburada", "hepsiburada", " HEPSİBURADA "):
        assert site_anahtari(ad) == "hepsiburada"
    assert site_anahtari("İnce Hesap") == site_anahtari("incehesap.com") == "incehesap"
    assert site_anahtari("Amazon Türkiye") == "amazon" and site_anahtari("PttAVM") == site_anahtari("pttavm") == "pttavm"
    assert site_anahtari("Trendyol Plus") == "trendyol" and site_anahtari("Gaming.Gen.TR") == "gaminggentr"
    assert site_anahtari("Xaser Bilgisayar") == "Xaser Bilgisayar"           # bilinmeyen mağaza adı korunur
    assert okunur_ad("pttavm") == "PttAVM" and okunur_ad("Bizdehesapli") == "Bizdehesapli" and okunur_ad(None) is None
