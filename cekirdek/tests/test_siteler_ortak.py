"""_ortak yardımcıları ve Sinerji autocomplete akışı testleri (ağ yok)."""
from __future__ import annotations

from kazi.siteler import _ortak, sinerji


class SahteTarayici:
    """http_getir davranışını taklit eden, Brave çağrılarını sayan sahte."""

    def __init__(self, http_sonuc):
        self.http_sonuc = http_sonuc
        self.brave_cagri = 0
        self.site_ici_cagri = 0
        self.basarilar = []
        self.hatalar = []

    def hiz_sinirla(self, site):
        pass

    def http_getir(self, url, site, zaman_asimi=40.0):
        d, h = self.http_sonuc
        if d is None:
            raise RuntimeError("bağlantı yok")
        if d >= 400:
            import urllib.error
            raise urllib.error.HTTPError(url, d, "hata", {}, None)
        return d, h

    def site_ici_ara(self, site, ana_url, sorgu, kutu_secicileri=(),
                     sonuc_bekle_secici=None):
        self.site_ici_cagri += 1
        return "<html>brave sonuc</html>", []

    def _basari(self, site, yontem):
        self.basarilar.append((site, yontem))

    def _hata(self, site, sebep, http_blok=False):
        self.hatalar.append((site, sebep, http_blok))


def test_http_basarili_brave_cagirmaz():
    t = SahteTarayici((200, "<html>" + "x" * 3000 + "</html>"))
    html, yontem = _ortak.http_ya_da_site_ici(
        t, "site", "https://a/", "https://a/ara?q=x", "x")
    assert yontem == "http"
    assert t.site_ici_cagri == 0
    assert html.startswith("<html>")


def test_http_403_site_ici_arama():
    t = SahteTarayici((403, ""))
    html, yontem = _ortak.http_ya_da_site_ici(
        t, "site", "https://a/", "https://a/ara?q=x", "x")
    assert yontem == "brave"
    assert t.site_ici_cagri == 1
    assert "brave sonuc" in html
    assert t.hatalar and t.hatalar[0][2] is True   # http_blok


def test_http_blok_govdesi_site_ici_arama():
    t = SahteTarayici((200, "<html>challenge-platform</html>"))
    _, yontem = _ortak.http_ya_da_site_ici(
        t, "site", "https://a/", "https://a/ara?q=x", "x")
    assert yontem == "brave"
    assert t.site_ici_cagri == 1


def test_baglanti_hatasi_site_ici_arama():
    t = SahteTarayici((None, ""))
    _, yontem = _ortak.http_ya_da_site_ici(
        t, "site", "https://a/", "https://a/ara?q=x", "x")
    assert yontem == "brave"
    assert t.site_ici_cagri == 1


def test_taksit_metinden():
    assert _ortak.taksit_metinden("Peşin fiyatına 6 taksit") == 6
    assert _ortak.taksit_metinden("Peşin Fiyatına 9 Taksit!") == 9
    assert _ortak.taksit_metinden("6 x 22.299,50 TL") == 6
    assert _ortak.taksit_metinden("12 x 1.299,00 ₺") == 12
    assert _ortak.taksit_metinden("24 x 999,00 TL") == 24
    assert _ortak.taksit_metinden("1 x 100 TL") is None     # tek çekim
    assert _ortak.taksit_metinden("30 x 100 TL") is None    # aralık dışı
    assert _ortak.taksit_metinden("taksit yok") is None
    assert _ortak.taksit_metinden(None) is None


class _Yanit:
    def __init__(self, html):
        self.html = html
        self.yontem = "http"


class SinerjiTarayici:
    """Autocomplete + ürün sayfası akışını taklit eder."""

    def __init__(self):
        self.urls = []

    def hiz_sinirla(self, site):
        pass

    def http_getir(self, url, site, zaman_asimi=40.0):
        self.urls.append(url)
        if "/Search/Autocomplete" in url:
            return 200, ('{"Products": ['
                         '{"Title": "Corsair RM1000x 1000W",'
                         ' "Url": "/corsair-rm1000x-p-1"},'
                         '{"Title": "Corsair RM1000x SHIFT 1000W",'
                         ' "Url": "/corsair-rm1000x-shift-p-2"},'
                         '{"Title": "Corsair RM1000x 80+ Gold",'
                         ' "Url": "/corsair-rm1000x-gold-p-3"}],'
                         ' "Categories": [], "Query": "x"}')
        raise AssertionError(f"beklenmeyen HTTP: {url}")

    def getir(self, url, site=None, zaman_asimi=40.0):
        self.urls.append(url)
        html = ('<script type="application/ld+json">'
                '{"@type": "Product", "name": "Corsair 1000W RM1000x 80+ Gold",'
                ' "offers": {"price": "9999.00",'
                ' "availability": "https://schema.org/InStock"}}</script>'
                '<div>Peşin fiyatına 6 taksit</div>')
        return _Yanit(html)

    def site_ici_ara(self, *a, **k):
        raise AssertionError("site_ici_ara çağrılmamalı")


def test_sinerji_ara_autocomplete_kullanir():
    t = SinerjiTarayici()
    ts = sinerji.ara(t, "corsair rm1000x", 6000, 20000, sayfa=1)
    assert len(ts) == 3
    assert all(tk.sepet_fiyati == 9999.0 for tk in ts)
    assert all(tk.pesin_taksit == 6 for tk in ts)
    assert "https://www.sinerji.gen.tr/corsair-rm1000x-p-1" in [x.url for x in ts]
    assert all("/Search/Autocomplete" in u or "-p-" in u for u in t.urls)
