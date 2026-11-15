from pathlib import Path

from kazi.siteler import Kart, sinerji

KOK = Path(__file__).resolve().parent / "veri_siteler"
ARAMA_F = KOK / "sinerji_ara.html"
URUN_F = KOK / "sinerji_urun.html"


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
    ts = sinerji.teklifler(ARAMA_F.read_text(encoding="utf-8"))
    assert len(ts) >= 7
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "sinerji"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.sinerji.gen.tr/")


def test_5080_kartlari():
    ts = sinerji.teklifler(ARAMA_F.read_text(encoding="utf-8"))
    kartlar = [t for t in ts if "RTX 5080" in t.baslik.upper()]
    assert len(kartlar) >= 3
    assert min(t.sepet_fiyati for t in kartlar) == 122352.0


def test_urun_taksit():
    tk = sinerji.urun(
        SahteTarayici(URUN_F.read_text(encoding="utf-8")),
        "https://www.sinerji.gen.tr/x-p-1",
    )
    assert tk is not None
    assert tk.sepet_fiyati > 0
    assert tk.pesin_taksit == 6


def test_bos_html():
    assert sinerji.teklifler("") == []
    assert sinerji.dogrudan_url_yasak is False
