from __future__ import annotations

from pathlib import Path

from kazi.siteler import Kart, gamegaraj

F = Path(__file__).resolve().parent / "veri_siteler" / "gamegaraj_ara.html"


def test_fixture_var():
    assert F.exists()
    assert 10_000 < F.stat().st_size <= 1_500_000


def test_arama_teklifleri():
    ts = gamegaraj.teklifler(F.read_text(encoding="utf-8"))
    assert len(ts) >= 10
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "gamegaraj"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.gamegaraj.com/")


def test_sistem_kartlari():
    ts = gamegaraj.teklifler(F.read_text(encoding="utf-8"))
    assert any(t.sepet_fiyati == 68878.2 for t in ts)
    assert any("RTX 5060" in t.baslik for t in ts)


def test_bos_html():
    assert gamegaraj.teklifler("") == []
