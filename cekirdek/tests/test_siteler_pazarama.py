from pathlib import Path

from kazi.siteler import Kart, pazarama

KOK = Path(__file__).resolve().parent / "veri_siteler"
ARAMA_F = KOK / "pazarama_ara.html"
URUN_F = KOK / "pazarama_urun.html"


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
    ts = pazarama.teklifler(ARAMA_F.read_text(encoding="utf-8"))
    assert len(ts) >= 20
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "pazarama"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.pazarama.com/")


def test_sepet_fiyati_ve_liste():
    ts = pazarama.teklifler(ARAMA_F.read_text(encoding="utf-8"))
    listeli = [t for t in ts if t.liste_fiyati is not None]
    assert len(listeli) >= 15
    assert all(t.liste_fiyati > t.sepet_fiyati for t in listeli)
    assert ts[0].sepet_fiyati == 121652.25


def test_urun():
    tk = pazarama.urun(
        SahteTarayici(URUN_F.read_text(encoding="utf-8")),
        "https://www.pazarama.com/x",
    )
    assert tk is not None and tk.sepet_fiyati > 0 and tk.baslik


def test_bos_html():
    assert pazarama.teklifler("") == []
    assert pazarama.dogrudan_url_yasak is False
