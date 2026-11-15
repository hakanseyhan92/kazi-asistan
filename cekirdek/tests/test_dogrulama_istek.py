"""Elle doğrulama istekleri (15.11): arayüz "elle geçeceğim" → tarama doğrulama sayfasında bekler."""
from kazi.tarayici import dogrulama as D


def test_yaz_oku_sil_ve_sure():
    b = D.istek_yaz("www.Hepsiburada.com", 300, simdi=1000.0)
    assert b == 1300.0
    assert D.istekler(simdi=1100.0) == {"hepsiburada.com": 200}
    assert D.istek_bitisi("hepsiburada", simdi=1100.0) == 1300.0          # modül adı ↔ alan adı
    assert D.istek_bitisi("trendyol.com", simdi=1100.0) is None
    assert D.istekler(simdi=1400.0) == {}                                  # süresi doldu
    assert D.istek_sil("hepsiburada.com") and not D.istek_sil("hepsiburada.com")


def test_sure_kirpma_ve_tum():
    assert D.istek_yaz("x.com", 99999, simdi=0.0) == D.AZAMI_SURE_SN
    D.istek_yaz("*", 60, simdi=0.0)
    assert D.istek_bitisi("herhangi.com", simdi=10.0) == 60.0


def test_bekleme_bitisi():
    # istek yok: ayar 0 olsa bile fırsat süresi kadar
    assert D.bekleme_bitisi("a.com", 100.0, 0, simdi_fn=lambda: 100.0) == 100.0 + D.ISTEK_FIRSATI_SN
    assert D.bekleme_bitisi("a.com", 100.0, 120, simdi_fn=lambda: 100.0) == 220.0
    D.istek_yaz("a.com", 300, simdi=110.0)
    assert D.bekleme_bitisi("a.com", 100.0, 0, simdi_fn=lambda: 111.0) == 410.0
