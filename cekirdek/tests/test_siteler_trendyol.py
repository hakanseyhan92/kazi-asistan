from pathlib import Path

from kazi.siteler import Kart, SITELER, trendyol

KOK = Path(__file__).resolve().parent / "veri_siteler"
ARAMA_F = KOK / "trendyol_ara.html"
URUN_F = KOK / "trendyol_urun.html"


class _Yanit:
    def __init__(self, html):
        self.html = html
        self.yontem = "brave"


class SahteTarayici:
    def __init__(self, html):
        self.html = html

    def getir(self, url, site=None, zaman_asimi=40.0):
        return _Yanit(self.html)


def test_fixture_var():
    for f in (ARAMA_F, URUN_F):
        assert f.exists(), f"canlı denemeden {f.name} kaydedilmeli"
        assert 10_000 < f.stat().st_size <= 1_500_000


def test_arama_teklifleri():
    ts = trendyol.teklifler_ayristir(ARAMA_F.read_text(encoding="utf-8"))
    assert len(ts) >= 20
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "trendyol"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.trendyol.com/")
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)


def test_arama_5080_ve_taksit():
    ts = trendyol.teklifler_ayristir(ARAMA_F.read_text(encoding="utf-8"))
    kartlar = [
        t
        for t in ts
        if "5080" in t.baslik.upper()
        and "EKRAN KARTI" in t.baslik.upper()
        and not any(x in t.baslik.upper() for x in ("LAPTOP", "NOTEBOOK", "PAKET"))
    ]
    assert len(kartlar) >= 3
    assert all(90_000 <= t.sepet_fiyati <= 400_000 for t in kartlar)
    assert any(t.pesin_taksit == 9 for t in kartlar), "Trendyol kartında 9 taksit rozeti var"


def test_liste_fiyati_buyuk():
    ts = trendyol.teklifler_ayristir(ARAMA_F.read_text(encoding="utf-8"))
    listeli = [t for t in ts if t.liste_fiyati is not None]
    assert listeli
    assert all(t.liste_fiyati > t.sepet_fiyati for t in listeli)


def test_urun():
    tk = trendyol.urun(SahteTarayici(URUN_F.read_text(encoding="utf-8")), "https://x/y")
    assert tk is not None
    assert tk.sepet_fiyati == 130918.5
    assert tk.stok is True
    assert "RTX 5080" in tk.baslik
    assert 2 <= (tk.pesin_taksit or 0) <= 24


def test_bos_html():
    assert trendyol.teklifler_ayristir("") == []
    assert trendyol.teklifler_ayristir("<html></html>") == []


def test_siteler_kaydi():
    assert SITELER["trendyol"] is trendyol
    assert trendyol.dogrudan_url_yasak is True
    assert hasattr(SITELER["trendyol"], "ara")
    assert hasattr(SITELER["trendyol"], "urun")
