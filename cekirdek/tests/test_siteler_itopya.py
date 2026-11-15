from pathlib import Path

from kazi.siteler import Kart, itopya

KOK = Path(__file__).resolve().parent / "veri_siteler"
ARAMA_F = KOK / "itopya_ara.html"
URUN_F = KOK / "itopya_urun.html"


class _Yanit:
    def __init__(self, html):
        self.html = html
        self.yontem = "http"


class SahteTarayici:
    def __init__(self, html):
        self.html = html

    def getir(self, url, site=None, zaman_asimi=40.0):
        return _Yanit(self.html)


def test_fixture_var():
    for f in (ARAMA_F, URUN_F):
        assert f.exists()
        assert 10_000 < f.stat().st_size <= 1_500_000


def test_arama_teklifleri():
    ts = itopya.teklifler(ARAMA_F.read_text(encoding="utf-8"))
    assert len(ts) >= 20
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "itopya"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.itopya.com/")


def test_taksit_ve_5080():
    ts = itopya.teklifler(ARAMA_F.read_text(encoding="utf-8"))
    kart = [t for t in ts if "5080" in t.baslik and "WINDFORCE" in t.baslik.upper()]
    assert kart and kart[0].sepet_fiyati == 133797.0
    assert kart[0].pesin_taksit == 6


def test_urun():
    tk = itopya.urun(
        SahteTarayici(URUN_F.read_text(encoding="utf-8")),
        "https://www.itopya.com/x",
    )
    assert tk is not None and tk.sepet_fiyati > 0 and tk.baslik


def test_bos_html():
    assert itopya.teklifler("") == []
    assert itopya.dogrudan_url_yasak is False
