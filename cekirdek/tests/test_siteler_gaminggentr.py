from __future__ import annotations

from pathlib import Path

from kazi.siteler import Kart, gaminggentr

KOK = Path(__file__).resolve().parent / "veri_siteler"
ARAMA_F = KOK / "gaminggentr_ara.html"
URUN_F = KOK / "gaminggentr_urun.html"


class _Yanit:
    def __init__(self, html: str):
        self.html = html
        self.yontem = "http"


class SahteTarayici:
    def __init__(self, html: str):
        self.html = html

    def getir(self, url, site=None, zaman_asimi=40.0):
        return _Yanit(self.html)


def test_fixture_var():
    for f in (ARAMA_F, URUN_F):
        assert f.exists()
        assert 10_000 < f.stat().st_size <= 1_500_000


def test_arama_teklifleri():
    ts = gaminggentr.teklifler(ARAMA_F.read_text(encoding="utf-8"))
    assert len(ts) >= 15
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "gaminggentr"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.gaming.gen.tr/")


def test_liste_fiyati_ve_5080():
    ts = gaminggentr.teklifler(ARAMA_F.read_text(encoding="utf-8"))
    listeli = [t for t in ts if t.liste_fiyati is not None]
    assert len(listeli) >= 10
    assert all(t.liste_fiyati > t.sepet_fiyati for t in listeli)
    assert any(t.sepet_fiyati == 154999.0 and "5080" in t.baslik for t in ts)


def test_urun():
    tk = gaminggentr.urun(SahteTarayici(URUN_F.read_text(encoding="utf-8")),
                          "https://www.gaming.gen.tr/x")
    assert tk is not None and tk.sepet_fiyati > 0 and tk.baslik


def test_bos_html():
    assert gaminggentr.teklifler("") == []
