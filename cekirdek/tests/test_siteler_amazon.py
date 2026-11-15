from __future__ import annotations

from pathlib import Path

from kazi.siteler import SITELER, Kart, amazon

KOK = Path(__file__).resolve().parent / "veri_siteler"
ARAMA_F = KOK / "amazon_ara.html"
URUN_F = KOK / "amazon_urun.html"


class _Yanit:
    def __init__(self, html: str):
        self.html = html
        self.yontem = "brave"


class SahteTarayici:
    def __init__(self, html: str):
        self.html = html

    def getir(self, url, site=None, zaman_asimi=40.0):
        return _Yanit(self.html)


def test_fixture_var():
    for f in (ARAMA_F, URUN_F):
        assert f.exists(), f"canlı denemeden {f.name} kaydedilmeli"
        assert 10_000 < f.stat().st_size <= 1_500_000


def test_arama_teklifleri():
    ts = amazon.teklifler_ayristir(ARAMA_F.read_text(encoding="utf-8"))
    assert len(ts) >= 20
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "amazon"
        assert t.sepet_fiyati > 0 and t.baslik
        assert "/dp/" in t.url
        assert not t.baslik.lower().startswith("sponsorlu")
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)


def test_5080_ve_uyari_kartlari():
    html = ARAMA_F.read_text(encoding="utf-8")
    ts = amazon.teklifler_ayristir(html)
    kartlar = [t for t in ts if "5080" in t.baslik.upper()
               and "EKRAN KARTI" in t.baslik.upper()
               and not any(x in t.baslik.upper() for x in ("LAPTOP", "NOTEBOOK", "OMEN", "STEALTH", "PAKET"))]
    assert len(kartlar) >= 2
    assert all(90_000 <= t.sepet_fiyati <= 400_000 for t in kartlar)
    assert all("normalden yüksek fiyat" not in t.baslik.lower() for t in ts)
    assert all("pd_rhf" not in t.url for t in ts)


def test_urun():
    tk = amazon.urun(SahteTarayici(URUN_F.read_text(encoding="utf-8")), "https://x/y")
    assert tk is not None
    assert tk.sepet_fiyati == 133980.73
    assert tk.stok is True
    assert "RTX 5080" in tk.baslik.upper()
    assert tk.satici


def test_bos_html():
    assert amazon.teklifler_ayristir("") == []
    assert amazon.teklifler_ayristir("<html></html>") == []


def test_siteler_kaydi():
    assert SITELER["amazon"] is amazon
    assert hasattr(SITELER["amazon"], "ara")
    assert hasattr(SITELER["amazon"], "urun")
