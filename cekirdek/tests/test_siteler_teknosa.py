from pathlib import Path

from kazi.siteler import Kart, teknosa

F = Path(__file__).resolve().parent / "veri_siteler" / "teknosa_ara.html"


def test_fixture_var():
    assert F.exists()
    assert 10_000 < F.stat().st_size <= 1_500_000


def test_arama_teklifleri():
    ts = teknosa.teklifler(F.read_text(encoding="utf-8"))
    assert len(ts) >= 20
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "teknosa"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.teknosa.com/")


def test_taksit_dolu():
    ts = teknosa.teklifler(F.read_text(encoding="utf-8"))
    assert sum(1 for t in ts if t.pesin_taksit) >= 15
    assert all(2 <= t.pesin_taksit <= 24 for t in ts if t.pesin_taksit)


def test_5080_fiyati():
    ts = teknosa.teklifler(F.read_text(encoding="utf-8"))
    pny = [t for t in ts if "PNY" in t.baslik and "5080" in t.baslik]
    assert pny and pny[0].sepet_fiyati == 152069.0


def test_bos_html():
    assert teknosa.teklifler("") == []
    assert teknosa.dogrudan_url_yasak is True
