"""reklam_engel modülü testleri — Playwright gerekmez, sahte route/request nesneleri kullanılır."""

from __future__ import annotations

import pytest

from kazi.tarayici import reklam_engel as re


class SahteIstek:
    def __init__(self, url: str, resource_type: str = "script"):
        self.url = url
        self.resource_type = resource_type


class SahteRoute:
    def __init__(self, istek):
        self.request = istek
        self.cagrilar: list[str] = []

    def abort(self, *args, **kwargs):
        self.cagrilar.append("abort")

    def continue_(self, *args, **kwargs):
        self.cagrilar.append("continue_")


class BozukIstek:
    @property
    def url(self):
        raise RuntimeError("istek okunamadı")

    @property
    def resource_type(self):
        raise RuntimeError("tür okunamadı")


class SahteBaglam:
    def __init__(self):
        self.pattern = None
        self.isleyici = None

    def route(self, pattern, isleyici):
        self.pattern = pattern
        self.isleyici = isleyici


@pytest.fixture(autouse=True)
def _sayac_sifirla():
    re.sayac["reklam"] = 0
    re.sayac["gorsel"] = 0
    re.sayac["widget"] = 0
    yield
    re.sayac["reklam"] = 0
    re.sayac["gorsel"] = 0
    re.sayac["widget"] = 0


def islet(istek, **kur_kwargs) -> SahteRoute:
    """Engelleyiciyi sahte bağlama kurar ve tek bir isteği işleyiciden geçirir."""
    baglam = SahteBaglam()
    re.kur(baglam, **kur_kwargs)
    assert baglam.pattern == "**/*"
    route = SahteRoute(istek)
    baglam.isleyici(route)
    return route


def test_liste_bos_degil_ve_yorumlar_ayiklanmis():
    alanlar = re.alanlar()
    assert len(alanlar) > 1000
    assert "doubleclick.net" in alanlar
    assert all(a == a.lower() and not a.startswith("#") for a in alanlar)
    assert "" not in alanlar


def test_liste_benzersiz_ve_sirali():
    satirlar = [
        s.strip()
        for s in re.VERI_YOLU.read_text(encoding="utf-8").splitlines()
        if s.strip() and not s.strip().startswith("#")
    ]
    assert satirlar == sorted(satirlar)
    assert len(satirlar) == len(set(satirlar))


def test_alanlar_onbelleklenir():
    assert re.alanlar() is re.alanlar()


def test_kok_alan_eslesir():
    assert re.engellenecek_mi("https://doubleclick.net/ads.js") is True


def test_alt_alan_eslesir():
    assert re.engellenecek_mi("https://a.doubleclick.net/pixel.gif") is True
    assert re.engellenecek_mi("https://x.y.google-analytics.com/collect") is True


def test_benzer_ad_eslesmez():
    assert re.engellenecek_mi("https://notdoubleclick.net/ads.js") is False
    assert re.engellenecek_mi("https://doubleclick.net.example.com/a.js") is False


def test_gecersiz_url_eslesmez():
    assert re.engellenecek_mi("bu bir url değil") is False
    assert re.engellenecek_mi("") is False
    assert re.engellenecek_mi("https://") is False


def test_ek_alanlar_eslesir():
    url = "https://reklam.ornekalan.test/x.js"
    assert re.engellenecek_mi(url) is False
    assert re.engellenecek_mi(url, ["ornekalan.test"]) is True
    assert re.engellenecek_mi(url, {"  ORNEKALAN.TEST "}) is True


def test_document_istegi_asla_engellenmez():
    # Hem reklam hem görsel açık; ana belge yine de geçer.
    route = islet(SahteIstek("https://doubleclick.net/sayfa", "document"), reklam=True, gorsel=True)
    assert route.cagrilar == ["continue_"]
    assert re.sayac == {"reklam": 0, "gorsel": 0, "widget": 0, "gomulu": 0, "gif": 0}


def test_gorsel_kapaliyken_gorsel_gecer():
    route = islet(SahteIstek("https://cdn.ornek.test/a.png", "image"), gorsel=False)
    assert route.cagrilar == ["continue_"]
    assert re.sayac["gorsel"] == 0


def test_gorsel_acikken_image_abort():
    route = islet(SahteIstek("https://cdn.ornek.test/a.png", "image"), reklam=False, gorsel=True)
    assert route.cagrilar == ["abort"]
    assert re.sayac == {"reklam": 0, "gorsel": 1, "widget": 0, "gomulu": 0, "gif": 0}


def test_gorsel_turleri_media_ve_font():
    for tur in re.GORSEL_TURLERI:
        route = islet(SahteIstek("https://cdn.ornek.test/k", tur), reklam=False, gorsel=True)
        assert route.cagrilar == ["abort"], tur


def test_reklam_kapaliyken_reklam_gecer():
    route = islet(SahteIstek("https://a.doubleclick.net/a.js", "script"), reklam=False, gorsel=False)
    assert route.cagrilar == ["continue_"]
    assert re.sayac["reklam"] == 0


def test_reklam_acikken_abort_ve_sayac():
    route = islet(SahteIstek("https://a.doubleclick.net/a.js", "script"))
    assert route.cagrilar == ["abort"]
    assert re.sayac["reklam"] == 1


def test_sayac_kur_cagrilarinda_korunur():
    islet(SahteIstek("https://a.doubleclick.net/a.js", "script"))
    islet(SahteIstek("https://b.doubleclick.net/b.js", "script"))
    assert re.sayac["reklam"] == 2


def test_isleyici_istisnada_continue_cagirir():
    route = islet(BozukIstek(), reklam=True, gorsel=True)
    assert route.cagrilar == ["continue_"]


def test_temiz_istek_gecer():
    route = islet(SahteIstek("https://www.ornek.test/urun", "fetch"))
    assert route.cagrilar == ["continue_"]


def test_tarayici_sekmesine_ayara_gore_bir_kez_kurulur(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from kazi.tarayici.cekirdek import Tarayici
    from kazi.tarayici.gorunurluk import ayar_yaz

    class Sayfa:
        def __init__(self):
            self.rotalar = []

        def route(self, desen, isleyici):
            self.rotalar.append(desen)

    t = Tarayici(durum_dosya=tmp_path / "d.json")
    pg = Sayfa()
    t._sayfa_hazirla(pg)
    t._sayfa_hazirla(pg)
    assert pg.rotalar == ["**/*"]                      # varsayılan: reklam engeli açık, bir kez
    ayar_yaz(reklam_engelle=False, gorsel_engelle=False, medya_engelle=False, yazi_tipi_engelle=False,
             widget_engelle=False, gif_engelle=False, gomulu_engelle=False)
    pg2 = Sayfa()
    t._sayfa_hazirla(pg2)
    assert pg2.rotalar == []


def test_ayri_turler_ve_widget_engeli():
    """15.11: görsel / video / yazı tipi ayrı anahtarlar; canlı destek-bildirim betikleri ayrı engellenir."""
    from kazi.tarayici import reklam_engel as re_

    class _I:
        def __init__(self, url, tur):
            self.url, self.resource_type = url, tur

    class _R:
        def __init__(self, url, tur):
            self.request, self.sonuc = _I(url, tur), None
        def abort(self, *a, **k): self.sonuc = "abort"
        def continue_(self, *a, **k): self.sonuc = "devam"

    class _B:
        def route(self, desen, fn): self.fn = fn

    b = _B()
    re_.kur(b, reklam=False, gorsel=True, gorsel_turleri=re_.TUR_GORSEL, medya=False, yazi_tipi=True, widget=True)
    def dene(url, tur):
        r = _R(url, tur); b.fn(r); return r.sonuc
    assert dene("https://x.com/a.png", "image") == "abort"
    assert dene("https://x.com/a.mp4", "media") == "devam"
    assert dene("https://x.com/a.woff2", "font") == "abort"
    assert dene("https://api.useinsider.com/ins.js", "script") == "abort"
    assert dene("https://widget.intercom.io/w.js", "script") == "abort"
    assert dene("https://www.trendyol.com/app.js", "script") == "devam"
    assert dene("https://useinsider.com.evil.org/x.js", "script") == "devam"
    assert dene("https://api.useinsider.com/", "document") == "devam"


def test_dogrulama_bekle_ayari_tarayici_varsayilani(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from kazi.tarayici.gorunurluk import ayar_yaz
    from kazi.tarayici.paralel_ayar import dogrula, paralel_ayarlar
    from kazi.tarayici.cekirdek import Tarayici
    assert paralel_ayarlar()["dogrulama_bekle_sn"] == 0
    assert Tarayici(durum_dosya=tmp_path / "d.json").kademe.bekle_k3 == 0
    ayar_yaz(dogrulama_bekle_sn=60)
    assert Tarayici(durum_dosya=tmp_path / "d.json").kademe.bekle_k3 == 60
    assert Tarayici(durum_dosya=tmp_path / "d.json", bekle_k3=5).kademe.bekle_k3 == 5
    ayar_yaz(dogrulama_bekle_sn=45)                        # geçersiz → varsayılan
    assert paralel_ayarlar()["dogrulama_bekle_sn"] == 0
    assert dogrula("dogrulama_bekle_sn", 45) and dogrula("dogrulama_bekle_sn", True)
    assert dogrula("dogrulama_bekle_sn", 300) is None


def test_bilinmeyen_ayar_anahtari_reddedilir():
    from kazi.tarayici.paralel_ayar import dogrula
    assert dogrula("sekme_sayisii", 3) == "sekme_sayisii: bilinmeyen ayar"
    assert dogrula("widget_engelle", True) is None



def test_gif_ve_gomulu_engeli():
    """15.11: GIF'ler (görseller açıkken bile) ve YouTube/harita/sosyal medya gömüleri engellenir; belge asla."""
    from kazi.tarayici import reklam_engel as re_
    assert re_._gif_mi("https://x.com/a/b.GIF?v=1") and not re_._gif_mi("https://x.com/a.gifler/b.png")
    assert re_._gomulu_mi("https://www.youtube.com/embed/abc") and re_._gomulu_mi("https://maps.googleapis.com/x")
    assert not re_._gomulu_mi("https://www.hepsiburada.com/") and not re_._gomulu_mi("https://notyoutube.com/")

    class _I:
        def __init__(self, url, tur):
            self.url, self.resource_type = url, tur

    class _R:
        def __init__(self, url, tur):
            self.request, self.sonuc = _I(url, tur), None

        def abort(self):
            self.sonuc = "abort"

        def continue_(self):
            self.sonuc = "devam"

    class _B:
        def route(self, desen, f):
            self.f = f

    b = _B()
    re_.kur(b, reklam=False, gorsel=False, gif=True, gomulu=True)
    for url, tur, beklenen in [("https://cdn.x.com/kampanya.gif", "image", "abort"),
                               ("https://cdn.x.com/urun.jpg", "image", "devam"),
                               ("https://www.youtube.com/embed/x", "sub_frame", "abort"),
                               ("https://www.youtube.com/watch?v=x", "document", "devam")]:
        r = _R(url, tur)
        b.f(r)
        assert r.sonuc == beklenen, url


def test_bot_muafiyeti_ve_animasyon(tmp_path, monkeypatch):
    """15.11: son 24 sa'te doğrulama çıkan sitede yalnız reklam engeli; animasyon kapatma CSS + reduced motion."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from kazi import gunluk
    from kazi.tarayici import cekirdek as C
    from kazi.tarayici import reklam_engel as re_

    kurulan = {}
    monkeypatch.setattr(re_, "kur", lambda pg, **k: kurulan.update(k))

    class Sayfa:
        def __init__(self):
            self.medya, self.betik = None, []

        def emulate_media(self, **k):
            self.medya = k

        def add_init_script(self, js):
            self.betik.append(js)

    t = C.Tarayici(durum_dosya=tmp_path / "d.json")
    pg = Sayfa()
    t._sayfa_hazirla(pg, "trendyol.com")
    assert kurulan["gorsel"] is not None and kurulan["gif"] is True and kurulan["gomulu"] is True
    assert pg.medya == {"reduced_motion": "reduce"} and "animation-duration:0s" in pg.betik[0]
    gunluk.yaz("uyari", "tarayici", "doğrulama", olay="dogrulama_gerekli", site="www.epey.com")
    assert C.bot_gecmisi_var("epey") and not C.bot_gecmisi_var("trendyol")
    pg2 = Sayfa()
    t._sayfa_hazirla(pg2, "epey")
    assert kurulan["reklam"] is True and not any(kurulan[k] for k in ("medya", "yazi_tipi", "widget", "gif", "gomulu"))
    assert pg2.medya is None and pg2.betik == []
