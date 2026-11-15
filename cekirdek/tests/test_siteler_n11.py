from pathlib import Path

from kazi.siteler import Kart, n11

FIXTURE = Path(__file__).resolve().parent / "veri_siteler" / "n11_ara.html"


def test_arama_url():
    u = n11.arama_url("rtx 5080", 90000, 260000, sayfa=2)
    assert u.startswith("https://www.n11.com/arama?")
    assert "q=rtx+5080" in u
    assert "minp=90000" in u
    assert "maxp=260000" in u
    assert "srt=PRICE_LOW" in u
    assert "pg=2" in u
    assert "pg=" not in n11.arama_url("x", sayfa=1)


def test_fixture_var():
    assert FIXTURE.exists(), "canlı denemeden n11_ara.html kaydedilmeli"
    assert FIXTURE.stat().st_size > 10_000
    assert FIXTURE.stat().st_size <= 1_500_000


def test_fixture_teklifleri():
    html = FIXTURE.read_text(encoding="utf-8")
    teklifler = n11.teklifler(html)
    assert len(teklifler) >= 5
    for t in teklifler:
        assert isinstance(t, Kart)
        assert t.site == "n11"
        assert t.sepet_fiyati > 0
        assert t.baslik
        assert t.url.startswith("https://www.n11.com/")
    fiyatlar = [t.sepet_fiyati for t in teklifler]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in teklifler}) == len(teklifler)


def test_fixture_liste_fiyati():
    html = FIXTURE.read_text(encoding="utf-8")
    teklifler = n11.teklifler(html)
    listeli = [t for t in teklifler if t.liste_fiyati is not None]
    assert listeli, "en az bir üründe üstü çizili liste fiyatı olmalı"
    assert all(t.liste_fiyati > t.sepet_fiyati for t in listeli)


def test_fixture_sepette_fiyat_okunuyor():
    html = FIXTURE.read_text(encoding="utf-8")
    teklifler = n11.teklifler(html)
    assert any(t.sepet_fiyati > 90000 for t in teklifler)


def test_bos_html():
    assert n11.teklifler("") == []
    assert n11.teklifler("<html><body>yok</body></html>") == []
    assert n11.dogrudan_url_yasak is False
