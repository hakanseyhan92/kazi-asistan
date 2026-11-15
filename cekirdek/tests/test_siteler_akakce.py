from __future__ import annotations

from pathlib import Path

from kazi.siteler import SITELER, Kart, akakce

KOK = Path(__file__).resolve().parent / "veri_siteler"
ARAMA_F = KOK / "akakce_ara.html"
URUN_F = KOK / "akakce_urun.html"
MODEL_URL = ("https://www.akakce.com/ekran-karti/en-ucuz-zotac-rtx-5080-gaming-"
             "solid-core-oc-zt-b50800j2-10p-256-bit-gddr7-16-gb-fiyati,982957987.html")


class _Yanit:
    def __init__(self, html: str, url: str = ""):
        self.html = html
        self.yontem = "http"
        self.durum = 200
        self.url = url


class SahteTarayici:
    def __init__(self, html: str):
        self.html = html

    def getir(self, url, site=None, zaman_asimi=40.0):
        return _Yanit(html=self.html, url=url)


def test_arama_url():
    u = akakce.arama_url("rtx 5080")
    assert u == "https://www.akakce.com/arama/?q=rtx+5080"
    assert "&p=2" in akakce.arama_url("rtx 5080", 2)


def test_fixture_var():
    for f in (ARAMA_F, URUN_F):
        assert f.exists(), f"canlı denemeden {f.name} kaydedilmeli"
        assert 10_000 < f.stat().st_size <= 1_500_000


def test_arama_modelleri():
    teklifler = akakce.modeller(ARAMA_F.read_text(encoding="utf-8"))
    assert len(teklifler) >= 5
    for t in teklifler:
        assert isinstance(t, Kart)
        assert t.site == "akakce"
        assert t.satici is None
        assert t.sepet_fiyati > 0
        assert t.baslik
        assert t.url.startswith("https://www.akakce.com/")
        assert t.kaynak is None
    fiyatlar = [t.sepet_fiyati for t in teklifler]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in teklifler}) == len(teklifler)


def test_arama_en_ucuz_gercek():
    teklifler = akakce.modeller(ARAMA_F.read_text(encoding="utf-8"))
    en_ucuz = teklifler[0]
    assert en_ucuz.sepet_fiyati < 200_000
    assert "5080" in en_ucuz.baslik


def test_model_teklifleri_ayristirma():
    teklifler = akakce.teklifleri_ayristir(
        URUN_F.read_text(encoding="utf-8"), model_url=MODEL_URL)
    assert len(teklifler) >= 5
    for t in teklifler:
        assert isinstance(t, Kart)
        assert t.site == "akakce"
        assert t.sepet_fiyati > 0
        assert t.baslik
        assert t.kaynak, "toplayıcı teklifinde kaynak (satış sitesi) dolu olmalı"
        assert t.url.startswith("https://www.akakce.com/r/")
    kaynaklar = {t.kaynak for t in teklifler}
    assert {"n11", "Trendyol", "Hepsiburada"} <= kaynaklar
    assert all(t.satici for t in teklifler)
    assert any(t.pesin_taksit == 6 for t in teklifler)
    assert any(t.stok is True for t in teklifler)
    fiyatlar = [t.sepet_fiyati for t in teklifler]
    assert fiyatlar == sorted(fiyatlar)


def test_teklifler_tarayici_ile():
    sahte = SahteTarayici(URUN_F.read_text(encoding="utf-8"))
    teklifler = akakce.teklifler(sahte, MODEL_URL)
    assert len(teklifler) >= 5
    assert teklifler[0].sepet_fiyati == min(t.sepet_fiyati for t in teklifler)
    assert all(t.url.startswith("http") for t in teklifler)


def test_bos_html():
    assert akakce.modeller("") == []
    assert akakce.modeller("<html><body>yok</body></html>") == []
    assert akakce.teklifleri_ayristir("") == []


def test_siteler_kaydi():
    assert SITELER["akakce"] is akakce
    assert hasattr(SITELER["akakce"], "ara")
    assert hasattr(SITELER["akakce"], "teklifler")
