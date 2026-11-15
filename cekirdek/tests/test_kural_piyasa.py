"""Piyasa özeti, kovalama ve hedef fiyat (kurallar/piyasa.py). Yıldız sistemi 14.11'de kaldırıldı."""
from datetime import datetime, timedelta, timezone

from kazi.kurallar.piyasa import hedef_altinda, kirpilmis_ortalama, kovalanacaklar, medyan, piyasa_ozeti, tl, yuzde

TR = timezone(timedelta(hours=3))
SIMDI = datetime(2026, 11, 14, 15, 0, tzinfo=TR)


def tk(fiyat, site="s", satici="x", saat_once=1, stok=True, durum="bekliyor", **ek):
    return dict(sepet_fiyati=fiyat, site=site, satici=satici, stok=stok, teyit_durumu=durum,
                gozlem_zamani=(SIMDI - timedelta(hours=saat_once)).isoformat(), **ek)


# ---------------- ürün
def test_kirpilmis_ortalama_uc_degeri_atar():
    f = [100] * 9 + [10000]
    assert kirpilmis_ortalama(f) == 100
    assert kirpilmis_ortalama([]) is None
    assert kirpilmis_ortalama([5, 7]) == 6  # n<10: kırpma yok


def test_medyan():
    assert medyan([3, 1, 2]) == 2 and medyan([1, 2, 3, 4]) == 2.5 and medyan([]) is None


def test_piyasa_ozeti_disarida_kalanlar():
    t = [tk(100, "a"), tk(200, "b"), tk(50, "c", saat_once=30), tk(10, "d", stok=False), tk(20, "e", durum="urun_farkli")]
    o = piyasa_ozeti(t, SIMDI)
    assert o["adet"] == 2 and o["ortalama"] == 150


def test_ayni_site_satici_tek_sayilir():
    t = [tk(100, "a", "x", saat_once=5), tk(300, "a", "x", saat_once=1), tk(100, "b")]
    o = piyasa_ozeti(t, SIMDI)
    assert o["adet"] == 2 and o["ortalama"] == 200


def test_kovalama_sirasi_ve_secimi():
    t = [tk(90, "a"), tk(80, "b"), tk(120, "c"), tk(70, "d", durum="teyitli", teyit_fiyati=70),
         tk(60, "e", durum="teyitli", teyit_fiyati=65)]
    k = kovalanacaklar(t, {"ortalama": 100})
    assert [x["site"] for x in k] == ["e", "b", "a"]  # e: teyitten sonra fiyat değişti


def test_bicimler():
    assert tl(108499.4) == "108.499 TL" and yuzde(-8.63) == "−%8,6" and yuzde(3) == "+%3,0"



def test_hedef_altinda_yalniz_teyitli():
    assert hedef_altinda(tk(99000, durum="teyitli"), 100000) is True
    assert hedef_altinda(tk(100000, durum="teyitli"), 100000) is True
    assert hedef_altinda(tk(99000, durum="bekliyor"), 100000) is False
    assert hedef_altinda(tk(101000, durum="teyitli"), 100000) is False
    assert hedef_altinda(tk(99000, durum="teyitli", teyit_fiyati=101000), 100000) is False   # teyit fiyatı esas
    assert hedef_altinda(tk(99000, durum="teyitli"), None) is False
