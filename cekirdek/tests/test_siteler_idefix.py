from pathlib import Path

from kazi.siteler import Kart, idefix

F = Path(__file__).resolve().parent / "veri_siteler" / "idefix_ara.html"


def test_fixture_var():
    assert F.exists()
    assert 10_000 < F.stat().st_size <= 1_500_000


def test_arama_teklifleri():
    ts = idefix.teklifler(F.read_text(encoding="utf-8"))
    assert len(ts) >= 20
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "idefix"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.idefix.com/")
        assert "-p-" in t.url


def test_liste_fiyati_ve_5080():
    ts = idefix.teklifler(F.read_text(encoding="utf-8"))
    listeli = [t for t in ts if t.liste_fiyati is not None]
    assert len(listeli) >= 15
    assert all(t.liste_fiyati > t.sepet_fiyati for t in listeli)
    assert any("5080" in t.baslik for t in ts)


def test_bos_html():
    assert idefix.teklifler("") == []
    assert idefix.dogrudan_url_yasak is True
