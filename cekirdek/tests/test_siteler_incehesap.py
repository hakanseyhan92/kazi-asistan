from pathlib import Path

from kazi.siteler import Kart, incehesap

F = Path(__file__).resolve().parent / "veri_siteler" / "incehesap_ara.html"


def test_fixture_var():
    assert F.exists()
    assert 10_000 < F.stat().st_size <= 1_500_000


def test_arama_teklifleri():
    ts = incehesap.teklifler(F.read_text(encoding="utf-8"))
    assert len(ts) >= 25
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "incehesap"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.incehesap.com/")


def test_taksit_dolu():
    ts = incehesap.teklifler(F.read_text(encoding="utf-8"))
    assert sum(1 for t in ts if t.pesin_taksit) >= 20
    assert all(2 <= t.pesin_taksit <= 24 for t in ts if t.pesin_taksit)


def test_5080_kartlari():
    ts = incehesap.teklifler(F.read_text(encoding="utf-8"))
    kartlar = [t for t in ts if "5080" in t.baslik]
    assert kartlar


def test_bos_html():
    assert incehesap.teklifler("") == []
    assert incehesap.dogrudan_url_yasak is True
