from pathlib import Path

from kazi.siteler import Kart, vatan

F = Path(__file__).resolve().parent / "veri_siteler" / "vatan_ara.html"


def test_fixture_var():
    assert F.exists()
    assert 10_000 < F.stat().st_size <= 1_500_000


def test_arama_teklifleri():
    ts = vatan.teklifler(F.read_text(encoding="utf-8"))
    assert len(ts) >= 2
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "vatan"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.vatanbilgisayar.com/")


def test_5080_kartlari():
    ts = vatan.teklifler(F.read_text(encoding="utf-8"))
    kartlar = [t for t in ts if "5080" in t.baslik]
    assert len(kartlar) >= 2
    assert min(t.sepet_fiyati for t in kartlar) == 129279.0


def test_bos_html():
    assert vatan.teklifler("") == []
    assert vatan.dogrudan_url_yasak is False
