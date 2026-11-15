import json
from pathlib import Path

from kazi.siteler import Kart, SITELER, mediamarkt

KOK = Path(__file__).resolve().parent / "veri_siteler"
JSON_F = KOK / "mediamarkt_ara.json"
ARAMA_F = KOK / "mediamarkt_ara.html"
URUN_F = KOK / "mediamarkt_urun.html"
YENI_F = KOK / "mediamarkt_ara_yeni.html"


class SahteTarayici:
    def __init__(self, html):
        self.html = html

    def brave_getir(self, url, site=None, **kw):
        return self.html


def test_fixture_var():
    for f in (JSON_F, ARAMA_F, URUN_F):
        assert f.exists(), f"canlı denemeden {f.name} kaydedilmeli"
    assert 10_000 < JSON_F.stat().st_size <= 1_500_000


def test_arama_graphql():
    yanitlar = json.loads(JSON_F.read_text(encoding="utf-8"))
    ts = mediamarkt.teklifler_json(yanitlar)
    assert len(ts) >= 10
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "mediamarkt"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.mediamarkt.com.tr/tr/product/")
        assert t.stok is True
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    en = next(t for t in ts if "PNY" in t.baslik)
    assert en.sepet_fiyati == 129641.2
    assert en.pesin_taksit == 4


def test_arama_dom_yedegi():
    ts = mediamarkt.teklifler_ayristir(ARAMA_F.read_text(encoding="utf-8"))
    assert len(ts) >= 1
    for t in ts:
        assert t.sepet_fiyati > 0 and t.site == "mediamarkt"


def test_urun():
    tk = mediamarkt.urun(SahteTarayici(URUN_F.read_text(encoding="utf-8")), "https://x/y")
    assert tk is not None
    assert tk.sepet_fiyati == 129641.2
    assert "PNY" in tk.baslik.upper()
    assert tk.stok is True


def test_yeni_arama_fixture():
    assert YENI_F.exists(), f"canlı denemeden {YENI_F.name} kaydedilmeli"
    assert 10_000 < YENI_F.stat().st_size <= 1_500_000
    ts = mediamarkt.teklifler_ayristir(YENI_F.read_text(encoding="utf-8"))
    assert len(ts) >= 2
    pny = next(t for t in ts if "PNY" in t.baslik.upper())
    assert pny.sepet_fiyati == 129641.2
    assert pny.url.startswith("https://www.mediamarkt.com.tr/tr/product/")


def test_bos_html():
    assert mediamarkt.teklifler_json([]) == []
    assert mediamarkt.teklifler_ayristir("") == []
    assert mediamarkt.teklifler_ayristir("<html></html>") == []


def test_siteler_kaydi():
    assert SITELER["mediamarkt"] is mediamarkt
    assert mediamarkt.dogrudan_url_yasak is True
    assert hasattr(SITELER["mediamarkt"], "ara")
    assert hasattr(SITELER["mediamarkt"], "urun")
