"""Sayfanın kendi JSON verisini okuma: Hepsiburada/Trendyol ayrıştırıcıları + yakalayıcı (AĞ YOK)."""
from __future__ import annotations

import json
from pathlib import Path

from kazi.siteler import hepsiburada as hb
from kazi.siteler import trendyol as ty
from kazi.siteler._json_yakala import JsonYakalayici, gomulu_durum, html_atamasi

VERI = Path(__file__).parent / "veri"


def _yukle(ad):
    return json.loads((VERI / ad).read_text(encoding="utf-8"))


# ------------------------------------------------------------------ Hepsiburada
def test_hb_alan_eslemesi_ve_mutlak_url():
    kartlar = hb.json_kartlari(_yukle("hepsiburada_state.json"))
    assert len(kartlar) == 3
    for k in kartlar:
        assert k.site == "hepsiburada"
        assert k.url.startswith("https://www.hepsiburada.com/")
        assert k.satici
        assert k.pesin_taksit == 7
        assert k.yontem == "brave"
    duz = next(k for k in kartlar if k.sepet_fiyati == 148664.06)
    assert duz.liste_fiyati is None
    assert duz.satici == "HAYATBİLGİSAYAR"
    assert duz.kampanya == "Ek 25 TL Kupon"
    assert duz.stok is None
    assert "Ventus" in duz.baslik


def test_hb_sepete_ozel_ve_premium_fiyati_gorulen_fiyat():
    kartlar = {k.baslik: k for k in hb.json_kartlari(_yukle("hepsiburada_state.json"))}
    sepet = next(k for b, k in kartlar.items() if "SHADOW" in b)
    assert sepet.sepet_fiyati == 135728.27      # campaignPriceInfo.discountedPrice
    assert sepet.liste_fiyati == 135778.27
    prem = next(k for b, k in kartlar.items() if "Kaia" in b)
    assert prem.sepet_fiyati == 247858.8
    assert prem.liste_fiyati == 271953.92


def test_hb_sirali_ve_teklife_donusur():
    kartlar = hb.json_kartlari(_yukle("hepsiburada_state.json"))
    assert [k.sepet_fiyati for k in kartlar] == sorted(k.sepet_fiyati for k in kartlar)
    t = kartlar[0].teklife("gpu_rtx5080")
    assert t.sepet_fiyati == kartlar[0].sepet_fiyati and t.site == "hepsiburada"


def test_hb_turkce_fiyat_dizgesi_ve_stok_yok():
    veri = {"products": [{"variantList": [{
        "name": "Deneme Ürün", "url": "https://www.hepsiburada.com/x-pm-HBC1", "procurable": False,
        "listing": {"priceInfo": {"price": "1.299,90 TL", "originalPrice": "1.499,00 TL"},
                    "merchantName": "Mağaza"}}]}]}
    (k,) = hb.json_kartlari(veri)
    assert k.sepet_fiyati == 1299.9 and k.liste_fiyati == 1499.0
    assert k.stok is False
    assert k.url == "https://www.hepsiburada.com/x-pm-HBC1"


def test_hb_bozuk_veri_bos_liste():
    for v in (None, "", 5, [], {}, {"data": 3}, {"data": {"products": "x"}},
              [{"variantList": "x"}], [{"variantList": [{"name": "a"}]}],
              {"products": [None, 1, {"variantList": [None]}]}):
        assert hb.json_kartlari(v) == []


def test_hb_html_icinden_state_okunur():
    state = _yukle("hepsiburada_state.json")
    html = ("<html><script>window.MORIA = window.MORIA || {};\n"
            "window.MORIA.PRODUCTLIST = Object.assign({}, {'abc': {\n 'STATE': "
            + json.dumps(state) + ",\n 'x': 1}});</script></html>")
    d = hb._sayfa_durumu(None, html)
    assert len(hb.json_kartlari(d)) == 3


def test_hb_bayat_sayfa_kullanilmaz():
    state = _yukle("hepsiburada_state.json")          # currentPage = 1
    html = ("window.MORIA.PRODUCTLIST = {'a': {'STATE': " + json.dumps(state) + "}}")
    assert len(hb._json_sayfa(None, None, 1, html)) == 3
    assert hb._json_sayfa(None, None, 2, html) == []   # 2. sayfa istendi, durum 1. sayfa


# ------------------------------------------------------------------ Trendyol
def test_ty_alan_eslemesi():
    kartlar = ty.json_kartlari(_yukle("trendyol_props.json"))
    assert len(kartlar) == 4
    zotac = next(k for k in kartlar if "Gaming Solid" in k.baslik)
    assert zotac.sepet_fiyati == 130918.5          # discountedPrice
    assert zotac.liste_fiyati == 145465
    assert zotac.pesin_taksit == 9
    assert zotac.url.startswith("https://www.trendyol.com/zotac/")
    assert all(k.site == "trendyol" and k.url.startswith("https://www.trendyol.com/") for k in kartlar)


def test_ty_html_ve_json_ayni_sonuc():
    props = _yukle("trendyol_props.json")
    html = '<script>window["__single-search-result__PROPS"]=' + json.dumps(props) + ";</script>"
    assert [k.url for k in ty.teklifler_ayristir(html)] == [k.url for k in ty.json_kartlari(props)]


def test_ty_bozuk_veri():
    for v in (None, "x", [], {}, {"data": {"products": None}}, {"data": {"products": [None, 3, {"name": "a"}]}}):
        assert ty.json_kartlari(v) == []
    assert ty.teklifler_ayristir("<html>") == []


# ------------------------------------------------------------------ yakalayıcı + gömülü durum
class _Yanit:
    def __init__(self, url, govde, ct="application/json", durum=200, uzunluk=None):
        self.url, self._g, self.status = url, govde, durum
        self.headers = {"content-type": ct}
        if uzunluk is not None:
            self.headers["content-length"] = str(uzunluk)

    def body(self):
        return self._g


class _Sayfa:
    def __init__(self):
        self.dinleyiciler = []
        self.sonuc = {}

    def on(self, olay, fn):
        self.dinleyiciler.append(fn)

    def remove_listener(self, olay, fn):
        self.dinleyiciler.remove(fn)

    def evaluate(self, js, arg=None):
        return self.sonuc.get(arg if arg is not None else "js")


def test_yakalayici_desen_ve_tur_suzgeci():
    pg = _Sayfa()
    y = JsonYakalayici(pg, [r"search/products"])
    f = pg.dinleyiciler[0]
    f(_Yanit("https://x/api/search/products?q=1", b'{"a": 1}'))
    f(_Yanit("https://x/baska", b'{"a": 2}'))
    f(_Yanit("https://x/api/search/products", b"<html>", ct="text/html"))
    f(_Yanit("https://x/api/search/products", b"{bozuk"))
    assert [r["json"] for r in y.yanitlar] == [{"a": 1}]
    y.kapat()
    assert pg.dinleyiciler == []
    y.kapat()                                          # ikinci kapat zararsız


def test_yakalayici_boyut_siniri():
    pg = _Sayfa()
    y = JsonYakalayici(pg, azami_yanit_bayt=50, azami_toplam_bayt=120, azami_adet=3)
    f = pg.dinleyiciler[0]
    f(_Yanit("u1", b'{"k": "' + b"x" * 100 + b'"}'))          # tek yanıt sınırı
    f(_Yanit("u2", b"{}", uzunluk=999))                         # content-length sınırı
    for i in range(5):
        f(_Yanit(f"u3{i}", b'{"k": "' + b"y" * 30 + b'"}'))     # 38 bayt; toplam 120
    assert len(y.yanitlar) == 3
    assert y.toplam_bayt <= 120
    assert y.atlanan >= 4


def test_yakalayici_istisna_firlatmaz():
    class Bozuk:
        url = "u"
        status = 200
        headers = {"content-type": "application/json"}

        def body(self):
            raise RuntimeError("kapandı")

    pg = _Sayfa()
    y = JsonYakalayici(pg)
    pg.dinleyiciler[0](Bozuk())
    pg.dinleyiciler[0](object())
    assert y.yanitlar == []

    class DinleyiciYok:
        def on(self, *a):
            raise RuntimeError("x")

    JsonYakalayici(DinleyiciYok()).kapat()


def test_gomulu_durum_ve_html_atamasi():
    pg = _Sayfa()
    pg.sonuc = {"js": {"a": 1}}
    assert gomulu_durum(pg, ["js:window.X"]) == {"a": 1}
    pg.sonuc = {"script#d": '{"b": [1, 2]}'}
    assert gomulu_durum(pg, ["js:window.X", "script:script#d"]) == {"b": [1, 2]}
    assert gomulu_durum(_Sayfa(), ["js:window.X", "script:script#yok"]) is None
    assert gomulu_durum(None, ["js:window.X"]) is None
    assert html_atamasi('a window["k"] = {"x": [1, {"y": 2}]}; tail', 'window["k"]') == {"x": [1, {"y": 2}]}
    assert html_atamasi("hiç", "window.k") is None
    assert html_atamasi('window.k = {bozuk', "window.k") is None
