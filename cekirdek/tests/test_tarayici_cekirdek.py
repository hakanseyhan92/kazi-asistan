"""Eski `Tarayici` arayüz uyumluluğu, site_ici_ara, sekme hijyeni ve K1 Chromium birim testleri (AĞ YOK)."""
from __future__ import annotations

import time
import urllib.error

import pytest

from kazi.tarayici import BotEngeli, ChromiumKatmani, Tarayici

DUZ_HTML = "<html><body>" + ("<p>Ürün fiyatı 119.999 TL</p>" * 200) + "</body></html>"


# ---------------------------------------------------------------- hız sınırı
def test_hiz_siniri_bekletir():
    t = Tarayici(min_aralik=0.20, jitter=0)
    t.hiz_sinirla("test")
    b = time.monotonic()
    t.hiz_sinirla("test")
    gecen = time.monotonic() - b
    assert gecen >= 0.15, gecen


def test_hiz_siniri_siteler_bagimsiz():
    t = Tarayici(min_aralik=0.5, jitter=0)
    t.hiz_sinirla("a")
    b = time.monotonic()
    t.hiz_sinirla("b")
    assert time.monotonic() - b < 0.2


# ---------------------------------------------------------------- eski Tarayici arayüz uyumu
def test_http_403_brave_e_duser(tmp_path):
    t = Tarayici(min_aralik=0, jitter=0, durum_dosya=tmp_path / "durum.json")
    cagrilar = []

    def sahte_http(url, site="", zaman_asimi=40.0):
        cagrilar.append(("http", url))
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    def sahte_brave(url, site="", **kw):
        cagrilar.append(("brave", url))
        return DUZ_HTML

    t.http_getir = sahte_http
    t.brave_getir = sahte_brave

    y = t.getir("https://www.trendyol.com/sr?q=rtx%205080", site="trendyol")
    assert y.yontem == "brave"
    assert y.html == DUZ_HTML
    assert [k for k, _ in cagrilar] == ["http", "brave"]
    assert t.durum("trendyol")["son_yontem"] == "brave"


def test_http_200_blok_varsa_brave(tmp_path):
    t = Tarayici(min_aralik=0, jitter=0, durum_dosya=tmp_path / "durum.json")
    t.http_getir = lambda url, site="", zaman_asimi=40.0: (200, "<html>Just a moment</html>")
    t.brave_getir = lambda url, site="", **kw: DUZ_HTML
    y = t.getir("https://www.hepsiburada.com/ara?q=rtx", site="hepsiburada")
    assert y.yontem == "brave"


def test_brave_de_bloksa_botengeli_ve_bildirim(tmp_path):
    olaylar = []
    t = Tarayici(
        min_aralik=0,
        jitter=0,
        durum_dosya=tmp_path / "durum.json",
        bildirim_fn=olaylar.append,
    )
    t.http_getir = lambda url, site="", zaman_asimi=40.0: (403, "")
    t.brave_getir = lambda url, site="", **kw: "<html>Verify you are human</html>"
    bildirimler = []
    t.bildir_kanca = lambda site, url="": bildirimler.append(site)

    with pytest.raises(BotEngeli) as exc:
        t.getir("https://www.amazon.com.tr/s?k=rtx", site="amazon")
    assert exc.value.site == "amazon"
    assert bildirimler == ["amazon"]
    assert any(
        o.get("tur") == "dogrulama_gerekli" and o.get("site") == "amazon" for o in olaylar
    )


def test_ardisik_http_blogu_brave_zorunlu(tmp_path):
    t = Tarayici(min_aralik=0, jitter=0, durum_dosya=tmp_path / "durum.json")
    t.http_getir = lambda url, site="", zaman_asimi=40.0: (403, "")
    t.brave_getir = lambda url, site="", **kw: DUZ_HTML
    for _ in range(3):
        t.getir("https://www.n11.com/arama?q=x", site="n11")
    assert t._brave_zorunlu("n11")

    cagri = []
    t.http_getir = lambda url, site="", zaman_asimi=40.0: cagri.append(1) or (200, DUZ_HTML)
    y = t.getir("https://www.n11.com/arama?q=x", site="n11")
    assert y.yontem == "brave"
    assert cagri == []


# ---------------------------------------------------------------- site içi arama (§2.7)
class SahteLocator:
    def __init__(self, sayfa, etiket, gorunur=True):
        self.sayfa = sayfa
        self.etiket = etiket
        self._gorunur = gorunur

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self._gorunur else 0

    def is_visible(self):
        return self._gorunur

    def click(self, timeout=None):
        self.sayfa.cagrilar.append(("click", self.etiket))
        self.sayfa.tiklandi.add(self.etiket)

    def fill(self, deger):
        self.sayfa.cagrilar.append(("fill", self.etiket, deger))


class SahteKlavye:
    def __init__(self, sayfa):
        self.sayfa = sayfa

    def type(self, metin, delay=0):
        self.sayfa.cagrilar.append(("type", metin, delay))

    def press(self, tus):
        self.sayfa.cagrilar.append(("press", tus))


class SahteSayfa:
    def __init__(self, kutu_secici="input#search-input"):
        self.cagrilar = []
        self.tiklandi = set()
        self.kutu_secici = kutu_secici
        self.keyboard = SahteKlavye(self)
        self.url = "about:blank"
        self._dinleyici = []

    def goto(self, url, wait_until=None, timeout=None):
        self.cagrilar.append(("goto", url))
        self.url = url

    def wait_for_timeout(self, ms):
        self.cagrilar.append(("wait", ms))

    def evaluate(self, js, arg=None):
        self.cagrilar.append(("eval",))
        return {"durum": 200, "metin": DUZ_HTML}

    def locator(self, secici):
        gorunur = False
        if secici == self.kutu_secici:
            gorunur = True if self.kutu_secici != "input#search-input" else bool(self.tiklandi)
        elif secici == 'div[role="search"] button':
            gorunur = True
        return SahteLocator(self, secici, gorunur)

    def get_by_placeholder(self, ph, exact=False):
        return SahteLocator(self, f"ph:{ph}", False)

    def get_by_role(self, rol):
        return SahteLocator(self, f"rol:{rol}", False)

    def content(self):
        return DUZ_HTML

    def inner_text(self, secici):
        return "Ürün fiyatı 119.999 TL"

    def on(self, olay, fn):
        self._dinleyici.append((olay, fn))

    def remove_listener(self, olay, fn):
        self.cagrilar.append(("remove_listener", olay))


def _sahte_tarayici(sayfa, tmp_path):
    t = Tarayici(min_aralik=0, jitter=0, durum_dosya=tmp_path / "durum.json")
    t._sekme = lambda site: sayfa
    t.hiz_sinirla = lambda site: None
    return t


def test_site_ici_ara_dogrudan_kutu_sirasi(tmp_path):
    sayfa = SahteSayfa(kutu_secici="input#q")
    sayfa.tiklandi.add("tetik")
    t = _sahte_tarayici(sayfa, tmp_path)

    html, yanitlar = t.site_ici_ara(
        "cimri", "https://www.cimri.com/", "rtx 5080", kutu_secicileri=("input#q",)
    )
    assert html == DUZ_HTML
    assert yanitlar == []

    adimlar = [c[0] for c in sayfa.cagrilar]
    assert adimlar.index("goto") < adimlar.index("type") < adimlar.index("press")
    gotolar = [c[1] for c in sayfa.cagrilar if c[0] == "goto"]
    assert gotolar == ["https://www.cimri.com/"]
    tip = next(c for c in sayfa.cagrilar if c[0] == "type")
    assert tip[1] == "rtx 5080" and 60 <= tip[2] <= 120
    assert ("press", "Enter") in sayfa.cagrilar
    assert t.son_arama_secici == "secici:input#q"


def test_site_ici_ara_once_aramaya_tikla_sonra_yaz(tmp_path):
    sayfa = SahteSayfa(kutu_secici="input#search-input")
    t = _sahte_tarayici(sayfa, tmp_path)

    html, _ = t.site_ici_ara(
        "cimri", "https://www.cimri.com/", "rtx 5080", kutu_secicileri=("input#search-input",)
    )
    assert html == DUZ_HTML
    assert ("click", 'div[role="search"] button') in sayfa.cagrilar
    assert ("click", "input#search-input") in sayfa.cagrilar
    assert ("press", "Enter") in sayfa.cagrilar
    assert "-> secici:input#search-input" in str(t.son_arama_secici)


def test_site_ici_ara_kutu_yoksa_runtimeerror(tmp_path):
    sayfa = SahteSayfa(kutu_secici="input#yok")
    t = _sahte_tarayici(sayfa, tmp_path)
    with pytest.raises(RuntimeError) as exc:
        t.site_ici_ara("cimri", "https://www.cimri.com/", "rtx 5080")
    assert "arama kutusu yok: cimri" in str(exc.value)


def test_site_ici_ara_ag_yakalama_dinleyicisi_temizlenir(tmp_path):
    sayfa = SahteSayfa(kutu_secici="input#q")
    sayfa.tiklandi.add("tetik")
    t = _sahte_tarayici(sayfa, tmp_path)
    t.site_ici_ara(
        "cimri",
        "https://www.cimri.com/",
        "rtx 5080",
        kutu_secicileri=("input#q",),
        ag_desen=r"/api/search",
    )
    assert [c[0] for c in sayfa._dinleyici] == ["response"]
    assert ("remove_listener", "response") in sayfa.cagrilar


def test_tarayici_getir_cimri_dogrudan_url_yasak_ile_site_ici_ara(tmp_path):
    sayfa = SahteSayfa(kutu_secici="input#search-input")
    t = _sahte_tarayici(sayfa, tmp_path)
    y = t.getir("https://www.cimri.com/arama?q=rtx+5080", site="cimri")
    assert y.yontem == "brave"
    assert y.html == DUZ_HTML
    gotolar = [c[1] for c in sayfa.cagrilar if c[0] == "goto"]
    assert gotolar == ["https://www.cimri.com/"]


# ---------------------------------------------------------------- sekme hijyeni ve bağlam yöneticisi
class SahteSekme:
    def __init__(self, url="about:blank", kapali=False):
        self.url = url
        self._kapali = kapali
        self.kapandi = False

    def is_closed(self):
        return self._kapali

    def close(self):
        self.kapandi = True
        self._kapali = True


class SahteSekmeCtx:
    def __init__(self, sayfalar):
        self.pages = list(sayfalar)
        self.yeni = 0

    def new_page(self):
        self.yeni += 1
        pg = SahteSekme("about:blank")
        self.pages.append(pg)
        return pg


def _sekme_tarayici(ctx, tmp_path):
    t = Tarayici(min_aralik=0, jitter=0, durum_dosya=tmp_path / "durum.json")
    t._brave_baglan = lambda: None
    t._ctx = ctx
    return t


def test_sekme_ayni_alan_adi_yeniden_kullanilir(tmp_path):
    mevcut = SahteSekme("https://www.skyscanner.com.tr/transport/flights/ist/nrt/")
    ctx = SahteSekmeCtx([mevcut, SahteSekme("https://example.com/")])
    t = _sekme_tarayici(ctx, tmp_path)
    pg = t._sekme("skyscanner.com.tr")
    assert pg is mevcut
    assert ctx.yeni == 0

    cimri = SahteSekme("https://www.cimri.com/")
    ctx.pages.append(cimri)
    assert t._sekme("cimri") is cimri
    assert ctx.yeni == 0


def test_sekme_temizle_ilk_sekme_korunur(tmp_path):
    sayfalar = [SahteSekme(f"https://x{i}.test/") for i in range(10)]
    ctx = SahteSekmeCtx(sayfalar)
    t = _sekme_tarayici(ctx, tmp_path)
    t._sekme_temizle()
    assert not sayfalar[0].kapandi
    assert [s.kapandi for s in sayfalar[1:7]] == [False] * 6
    assert all(s.kapandi for s in sayfalar[7:])


def test_baglam_yoneticisi_ve_brave_birak(tmp_path):
    sekme = SahteSekme("https://www.n11.com/")
    with Tarayici(min_aralik=0, jitter=0, durum_dosya=tmp_path / "durum.json") as t:
        t._sekmeler["n11"] = sekme
        t.brave_birak()
        assert sekme.kapandi is True
        assert t._sekmeler == {}


# ---------------------------------------------------------------- K1 Chromium kurulu değilse ve yerel sunucu
def test_chromium_katmani_kurulu_degilse_olay_yayinlar_ve_bos_doner():
    olaylar = []
    k1 = ChromiumKatmani(bildirim_fn=olaylar.append)
    k1.kullanilabilir = False
    durum, html = k1.getir("https://example.com")
    assert durum is None and html == ""
    assert any(o.get("tur") == "k1_atlandi" for o in olaylar)


def test_chromium_getir_yerel_sunucu():
    import http.server
    import threading
    from kazi.tarayici.chromium import getir as chromium_getir

    class _H(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DUZ_HTML.encode("utf-8"))

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        olaylar = []
        durum, html = chromium_getir(f"http://127.0.0.1:{port}/", bildirim_fn=olaylar.append)
        if durum is None and any(o.get("tur") == "k1_atlandi" for o in olaylar):
            pytest.skip("Playwright Chromium kurulu değil")
        assert durum == 200
        assert "119.999 TL" in html
    finally:
        srv.shutdown()
        srv.server_close()

