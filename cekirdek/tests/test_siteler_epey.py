from __future__ import annotations

from pathlib import Path

from kazi.siteler import SITELER, Kart, epey

FIXTURE = Path(__file__).resolve().parent / "veri_siteler" / "epey_ara.html"


def test_arama_url():
    assert epey.arama_url("rtx 5080") == "https://www.epey.com/ara/?ara=rtx+5080"


def test_fixture_var():
    assert FIXTURE.exists(), "canlı denemeden epey_ara.html kaydedilmeli"
    assert 10_000 < FIXTURE.stat().st_size <= 1_500_000


def test_fixture_teklifleri():
    teklifler = epey.teklifler(FIXTURE.read_text(encoding="utf-8"))
    assert len(teklifler) >= 5
    for t in teklifler:
        assert isinstance(t, Kart)
        assert t.site == "epey"
        assert t.satici is None
        assert t.sepet_fiyati > 0
        assert t.baslik
        assert t.url.startswith("https://www.epey.com/")
    fiyatlar = [t.sepet_fiyati for t in teklifler]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in teklifler}) == len(teklifler)


def test_en_ucuz_gercek():
    teklifler = epey.teklifler(FIXTURE.read_text(encoding="utf-8"))
    assert teklifler[0].sepet_fiyati < 200_000
    assert "5080" in teklifler[0].baslik
    assert any("RTX 5080" in t.baslik for t in teklifler)


def test_modeller_ayni():
    h = FIXTURE.read_text(encoding="utf-8")
    assert [t.url for t in epey.modeller(h)] == [t.url for t in epey.teklifler(h)]


def test_bos_html():
    assert epey.teklifler("") == []
    assert epey.teklifler("<html><body>yok</body></html>") == []


def test_siteler_kaydi():
    assert SITELER["epey"] is epey
    assert hasattr(SITELER["epey"], "ara")
