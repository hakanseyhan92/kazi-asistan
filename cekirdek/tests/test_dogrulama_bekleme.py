"""Bot sayfasında elle doğrulama beklemesi (15.11): tarama doğrulama sayfasında bekler, kullanıcı geçince devam eder."""
from kazi import gunluk
from kazi.tarayici import dogrulama
from kazi.tarayici.cekirdek import Tarayici

BOT = "<html><title>Just a moment...</title>captcha</html>"
NORMAL = "<html>" + "ürün 12.345 TL " * 400 + "</html>"


class SahteSayfa:
    def __init__(self, icerikler):
        self.icerikler = list(icerikler)
        self.bekleme = 0

    def wait_for_timeout(self, ms):
        self.bekleme += 1

    def content(self):
        return self.icerikler.pop(0) if len(self.icerikler) > 1 else self.icerikler[0]


def _t():
    return Tarayici(veritabani=None, bekle_k3=0.0, bildirim_fn=lambda o: None)


def test_kullanici_gecince_true_ve_kayit():
    t = _t()
    dogrulama.istek_yaz("hepsiburada.com", 300)
    pg = SahteSayfa([BOT, BOT, NORMAL])
    assert t._dogrulama_firsati(pg, "hepsiburada.com", "https://www.hepsiburada.com/ara?q=x") is True
    olaylar = [k["olay"] for k in gunluk.oku()["kayitlar"]]
    assert olaylar[:2] == ["dogrulama_gecildi", "dogrulama_bekleniyor"]
    assert dogrulama.istekler() == {}                   # geçilince istek silinir


def test_sure_dolunca_false(monkeypatch):
    t = _t()
    monkeypatch.setattr(dogrulama, "bekleme_bitisi", lambda *a, **k: 0.0)     # süre çoktan dolmuş
    assert t._dogrulama_firsati(SahteSayfa([BOT]), "a.com", "https://a.com") is False
    assert gunluk.oku()["kayitlar"][0]["olay"] == "dogrulama_zaman_asimi"


def test_ic_ice_beklemez():
    t = _t()
    t._dogrulama_icinde = True
    assert t._dogrulama_firsati(SahteSayfa([BOT]), "a.com", "u") is False
    assert t._dogrulama_firsati(None, "a.com", "u") is False if not setattr(t, "_dogrulama_icinde", False) else True
