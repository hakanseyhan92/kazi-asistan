from __future__ import annotations

from pathlib import Path

from kazi.siteler import SITELER, Kart, cimri

KOK = Path(__file__).resolve().parent / "veri_siteler"
ARAMA_F = KOK / "cimri_ara.html"
URUN_F = KOK / "cimri_urun.html"
URUN_URL = ("https://www.cimri.com/ekran-kartlari/en-ucuz-gigabyte-aorus-geforce-"
            "rtx-5080-master-gv-n5080aorus-m-16gd-16gb-gddr7-16gb-ekran-karti-"
            "fiyatlari,2431946447")


class SahteTarayici:
    def __init__(self, html: str):
        self.html = html
        self.hiz = []

    def hiz_sinirla(self, site):
        self.hiz.append(site)

    def http_getir(self, url, site, zaman_asimi=40.0):
        return 200, self.html

    def brave_fetch(self, *a, **k):
        raise AssertionError("HTTP çalışırken brave_fetch çağrılmamalı")


def test_arama_url():
    assert cimri.arama_url("rtx 5080") == "https://www.cimri.com/arama?q=rtx+5080"


def test_fixture_var():
    for f in (ARAMA_F, URUN_F):
        assert f.exists(), f"canlı denemeden {f.name} kaydedilmeli"
        assert 10_000 < f.stat().st_size <= 1_500_000


def test_arama_modelleri():
    teklifler = cimri.modeller(ARAMA_F.read_text(encoding="utf-8"))
    assert len(teklifler) >= 5
    for t in teklifler:
        assert isinstance(t, Kart)
        assert t.site == "cimri"
        assert t.sepet_fiyati > 0
        assert t.baslik
        assert t.satici is None
        assert t.url.startswith("https://www.cimri.com/")
        assert t.kaynak, "en düşük teklifin mağazası kaynak olarak yazılmalı"
    fiyatlar = [t.sepet_fiyati for t in teklifler]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in teklifler}) == len(teklifler)


def test_urun_jsonld_teklifleri():
    teklifler = cimri.teklifleri_ayristir(
        URUN_F.read_text(encoding="utf-8"), urun_url=URUN_URL)
    assert len(teklifler) >= 3
    for t in teklifler:
        assert isinstance(t, Kart)
        assert t.site == "cimri"
        assert t.sepet_fiyati > 0
        assert t.url.startswith("https://") and "cimri.com" not in t.url
        assert t.kaynak and t.kaynak.lower() != "cimri"
        assert t.stok is True
    fiyatlar = [t.sepet_fiyati for t in teklifler]
    assert fiyatlar == sorted(fiyatlar)
    assert {t.kaynak for t in teklifler} >= {"n11", "PttAVM", "Hepsiburada"}
    n11 = next(t for t in teklifler if t.kaynak == "n11")
    assert n11.satici == "falconbilgisayar"
    assert n11.url.startswith("https://www.n11.com/urun/")


def test_urun_tarayici_ile():
    sahte = SahteTarayici(URUN_F.read_text(encoding="utf-8"))
    teklif = cimri.urun(sahte, URUN_URL)
    assert teklif is not None
    assert teklif.sepet_fiyati == 171580.0
    assert teklif.kaynak == "n11"
    assert teklif.satici == "falconbilgisayar"
    assert "Gigabyte Aorus" in teklif.baslik


def test_site_ici_arama_fixture():
    f = KOK / "cimri_siteici_ara.html"
    assert f.exists() and 10_000 < f.stat().st_size <= 1_500_000
    teklifler = cimri.modeller(f.read_text(encoding="utf-8"))
    assert len(teklifler) >= 3
    assert all("5080" in t.baslik for t in teklifler)
    assert all(t.sepet_fiyati > 0 and t.kaynak for t in teklifler)


def test_bos_html():
    assert cimri.modeller("") == []
    assert cimri.teklifleri_ayristir("") == []
    assert cimri.modeller("<html><body>yok</body></html>") == []


def test_siteler_kaydi():
    assert SITELER["cimri"] is cimri
    assert hasattr(SITELER["cimri"], "ara")
    assert hasattr(SITELER["cimri"], "urun")
