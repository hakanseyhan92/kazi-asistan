from pathlib import Path

from kazi.siteler import Kart, SITELER, hepsiburada

KOK = Path(__file__).resolve().parent / "veri_siteler"
ARAMA_F = KOK / "hepsiburada_ara.html"
URUN_F = KOK / "hepsiburada_urun.html"
YENI_F = KOK / "hepsiburada_ara_yeni.html"

ZOTAC = (
    "Teslimat bilgisi: 16 Kas kargoda Zotac Rtx 5080 Gaming Solid Core Oc "
    "ZT-B50800J2-10P 256 Bit Gddr7 16 GB Ekran Kartı Kampanya: Peşin fiyatına "
    "7 x 18.206 TL Peşin fiyatına 7 x 18.206 TL Sepete özel 134.156,80 TL %5 "
    "127.448,96 TL"
)
PALIT = (
    "Teslimat bilgisi: Yarın kargoda Palit Rtx 5080 Infınıty 3 16GB "
    "NE75080019T2-GB2035S Gddr7 256BIT Gaming Oyuncu Ekran Kartı Kampanya: "
    "Peşin fiyatına 7 x 17.278 TL Peşin fiyatına 7 x 17.278 TL 120.946,50 TL"
)
INNO3D = (
    "Teslimat bilgisi: 17 Kas kargoda Ek 1500 TL Kupon Ek 1500 TL Kupon Inno3d "
    "Geforce RTX5080 X3 16GB GDDR7 256Bit Ekran Kartı 96.500,00 TL"
)


def _kart_html(metin: str, href: str) -> str:
    ad, _, _ = metin.partition("Kampanya:")
    ad = ad.split("kargoda", 1)[-1].strip()
    return (
        '<html><body><ul><li class="productListContent-x">'
        f'<a href="{href}"><h2>{ad}</h2></a><div>{metin}</div>'
        "</li></ul></body></html>"
    )


class _Yanit:
    def __init__(self, html):
        self.html = html
        self.yontem = "brave"


class SahteTarayici:
    def __init__(self, html):
        self.html = html

    def getir(self, url, site=None, zaman_asimi=40.0):
        return _Yanit(self.html)


def test_fixture_var():
    for f in (ARAMA_F, URUN_F):
        assert f.exists(), f"canlı denemeden {f.name} kaydedilmeli"
        assert 10_000 < f.stat().st_size <= 1_500_000


def test_arama_teklifleri():
    ts = hepsiburada.teklifler_ayristir(ARAMA_F.read_text(encoding="utf-8"))
    assert len(ts) >= 10
    for t in ts:
        assert isinstance(t, Kart)
        assert t.site == "hepsiburada"
        assert t.sepet_fiyati > 0 and t.baslik
        assert t.url.startswith("https://www.hepsiburada.com/")
    fiyatlar = [t.sepet_fiyati for t in ts]
    assert fiyatlar == sorted(fiyatlar)
    assert len({t.url for t in ts}) == len(ts)


def test_5080_kartlari_ve_dusuk_fiyat():
    ts = hepsiburada.teklifler_ayristir(ARAMA_F.read_text(encoding="utf-8"))
    kartlar = [
        t
        for t in ts
        if "RTX" in t.baslik.upper()
        and "5080" in t.baslik
        and "EKRAN KARTI" in t.baslik.upper()
        and not any(x in t.baslik.upper() for x in ("LAPTOP", "NOTEBOOK", "PAKET"))
    ]
    assert len(kartlar) >= 1
    assert all(90_000 <= t.sepet_fiyati <= 400_000 for t in kartlar)
    assert any("Ventus 3x Oc White" in t.baslik for t in kartlar)
    listeli = [t for t in ts if t.liste_fiyati is not None]
    assert listeli, "sepete özel / indirimli kart bekleniyor"
    assert all(t.sepet_fiyati < t.liste_fiyati for t in listeli)
    assert any(t.pesin_taksit == 7 for t in kartlar)


def test_urun():
    tk = hepsiburada.urun(SahteTarayici(URUN_F.read_text(encoding="utf-8")), "https://x/y")
    assert tk is not None
    assert tk.sepet_fiyati == 250893.6
    assert tk.satici == "koçlar grup ticaret"
    assert tk.stok is True
    assert tk.pesin_taksit == 7
    assert "5080" in tk.baslik


def test_gercek_kart_metinleri():
    ts = hepsiburada.teklifler_ayristir(_kart_html(ZOTAC, "/zotac-pm-1"))
    assert len(ts) == 1
    z = ts[0]
    assert z.sepet_fiyati == 127448.96
    assert z.liste_fiyati == 134156.80
    assert z.pesin_taksit == 7
    assert z.baslik.startswith("Zotac Rtx 5080")
    assert "18.206" not in z.baslik
    assert z.url == "https://www.hepsiburada.com/zotac-pm-1"

    p = hepsiburada.teklifler_ayristir(_kart_html(PALIT, "/palit-pm-2"))[0]
    assert p.sepet_fiyati == 120946.50
    assert p.liste_fiyati is None
    assert p.pesin_taksit == 7
    assert p.baslik.startswith("Palit Rtx 5080 Infınıty 3")

    i = hepsiburada.teklifler_ayristir(_kart_html(INNO3D, "/inno-pm-3"))[0]
    assert i.sepet_fiyati == 96500.00
    assert i.liste_fiyati is None
    assert i.baslik.startswith("Inno3d")
    assert i.kampanya == "Ek 1500 TL Kupon"


def test_yeni_arama_fixture():
    assert YENI_F.exists(), f"canlı denemeden {YENI_F.name} kaydedilmeli"
    assert 10_000 < YENI_F.stat().st_size <= 1_500_000
    ts = hepsiburada.teklifler_ayristir(YENI_F.read_text(encoding="utf-8"))
    assert len(ts) >= 20
    kartlar = [
        t
        for t in ts
        if "5080" in t.baslik.upper()
        and "EKRAN KARTI" in t.baslik.upper()
        and not any(
            x in t.baslik.upper()
            for x in ("LAPTOP", "NOTEBOOK", "PAKET", "MASAUSTU", "HAZIR")
        )
    ]
    assert len(kartlar) >= 10, f"yalnız {len(kartlar)} standalone 5080 kartı"
    z = next(t for t in ts if "Zotac Rtx 5080 Gaming Solid Core Oc" in t.baslik)
    assert z.sepet_fiyati == 127448.96
    assert z.liste_fiyati == 134156.80
    assert z.pesin_taksit == 7
    listeli = [t for t in ts if t.liste_fiyati is not None]
    assert len(listeli) >= 5
    assert all(t.sepet_fiyati < t.liste_fiyati for t in listeli)


def test_bos_html():
    assert hepsiburada.teklifler_ayristir("") == []
    assert hepsiburada.teklifler_ayristir("<html></html>") == []


def test_siteler_kaydi():
    assert SITELER["hepsiburada"] is hepsiburada
    assert hepsiburada.dogrudan_url_yasak is True
    assert hasattr(SITELER["hepsiburada"], "ara")
    assert hasattr(SITELER["hepsiburada"], "urun")
