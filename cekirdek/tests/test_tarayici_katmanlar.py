"""Tarayıcı katmanları K0–K3, Kademe yöneticisi, yerel HTTP sunucusu, kilit ve Brave yolu testleri (AĞ YOK)."""
from __future__ import annotations

import gzip
import http.server
import threading
import time
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kazi.db import VeriTabani
from kazi.modeller import TZ_ISTANBUL, Katman, Site
from kazi.tarayici import (
    BRAVE_PROFIL,
    CDP_PORT,
    DOGRUDAN_URL_YASAK_SITELER,
    KILIT_YOL,
    BotEngeli,
    ChromiumKatmani,
    Kademe,
    KilitZamanAsimi,
    Tarayici,
    blok_mu,
    brave_kilidi,
    brave_komutu,
    brave_yolu_bul,
    hata_turu_belirle,
    site_adi,
)

DUZ_HTML = "<html><body>" + ("<p>Ürün fiyatı 108.499 TL</p>" * 200) + "</body></html>"
CF_403_HTML = (
    "<html><head><title>Just a moment...</title></head>"
    "<body><div class='cf-chl-widget'>Checking your browser</div></body></html>"
)
CAPTCHA_HTML = (
    "<html><body><h1>Olağan dışı trafik tespit edildi</h1>"
    "<p>Lütfen robot olmadığınızı doğrulayın (captcha).</p></body></html>"
)


# ---------------------------------------------------------------- yerel http.server fikstürü
class _YerelHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/normal"):
            govde = DUZ_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(govde)
        elif self.path.startswith("/gzip"):
            govde = gzip.compress(DUZ_HTML.encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            self.wfile.write(govde)
        elif self.path.startswith("/cf403"):
            govde = CF_403_HTML.encode("utf-8")
            self.send_response(403)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(govde)
        elif self.path.startswith("/captcha"):
            govde = CAPTCHA_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(govde)
        elif self.path.startswith("/bos"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"")
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def yerel_sunucu():
    sunucu = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _YerelHandler)
    port = sunucu.server_address[1]
    ip_parcasi = threading.Thread(target=sunucu.serve_forever, daemon=True)
    ip_parcasi.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        sunucu.shutdown()
        sunucu.server_close()


@pytest.fixture
def db(tmp_path):
    v = VeriTabani(tmp_path / "kazi.db")
    try:
        yield v
    finally:
        v.kapat()


# ---------------------------------------------------------------- sahte K1 ve K2 nesneleri
class SahteK1:
    def __init__(self, yanit=(200, DUZ_HTML), kullanilabilir: bool = True):
        self.yanit = yanit
        self.kullanilabilir = kullanilabilir
        self.cagrilar: list[str] = []

    def getir(self, url: str, zaman_asimi: float = 40.0):
        self.cagrilar.append(url)
        if callable(self.yanit):
            return self.yanit(url)
        return self.yanit


class SahteK2:
    def __init__(self, yanit=DUZ_HTML):
        self.yanit = yanit
        self.cagrilar: list[str] = []
        self.arama_cagrilari: list[tuple[str, str, str]] = []
        self._sekmeler: dict[str, object] = {}

    def getir(self, url: str, site: str = ""):
        self.cagrilar.append(url)
        if callable(self.yanit):
            return self.yanit(url)
        return self.yanit

    def site_ici_ara(
        self,
        site: str,
        ana_url: str,
        sorgu: str,
        kutu_secicileri=(),
        sonuc_bekle_secici=None,
        ag_desen=None,
    ):
        self.arama_cagrilari.append((site, ana_url, sorgu))
        html = self.yanit(ana_url) if callable(self.yanit) else self.yanit
        return html, []


# ---------------------------------------------------------------- 1. blok_mu ve site_adi
def test_blok_mu_olumlu_eski_ornekler():
    assert blok_mu("<html><title>Just a moment...</title></html>")
    assert blok_mu('<div class="cf-chl-widget"></div>')
    assert blok_mu("<script src='/cdn-cgi/challenge-platform/x.js'></script>")
    assert blok_mu("<p>Lütfen robot olmadığınızı doğrulayın</p>")
    assert blok_mu("<h1>Access Denied</h1>")
    assert blok_mu("<div id='px-captcha'></div>")
    assert blok_mu("<h2>Olağan dışı trafik tespit edildi</h2>")
    assert blok_mu("<html>Verify you are human</html>")
    assert blok_mu("")
    assert blok_mu(None)
    assert blok_mu("<html><body>kısa</body></html>")


def test_blok_mu_olumsuz_gercek_sayfalar():
    assert not blok_mu(DUZ_HTML)
    assert not blok_mu("<html><body><span>13.499 TL</span>" + "x" * 3000 + "</body></html>")
    buyuk_cf_sayfa = (
        "<script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script>"
        + "".join(f"<div>Ürün {i} <span>{100 + i}.999 TL</span></div>" for i in range(20))
        + "x" * 160_000
    )
    assert blok_mu(buyuk_cf_sayfa) is False
    assert blok_mu("<html><title>Just a moment...</title>" + "x" * 5000 + "</html>") is True


def test_site_adi():
    assert site_adi("https://www.akakce.com/arama/?q=rtx") == "akakce.com"
    assert site_adi("https://n11.com/urun") == "n11.com"


# ---------------------------------------------------------------- 2. yerel http.server + hata türleri
def test_yerel_sunucu_k0_normal_ve_gzip(yerel_sunucu, db, tmp_path):
    olaylar = []
    kademe = Kademe(
        db,
        olaylar.append,
        k1=SahteK1(),
        k2=SahteK2(),
        durum_dosya=tmp_path / "durum.json",
        min_aralik=0,
        jitter=0,
    )
    y1 = kademe.getir(f"{yerel_sunucu}/normal", site="akakce")
    assert y1.yontem == "http"
    assert y1.katman == Katman.K0
    assert y1.durum == 200
    assert "108.499 TL" in y1.html

    y2 = kademe.getir(f"{yerel_sunucu}/gzip", site="n11")
    assert y2.yontem == "http"
    assert "108.499 TL" in y2.html

    siteler = {s.ad: s for s in db.siteler_listele()}
    assert siteler["akakce"].katman_tercihi == Katman.K0
    assert siteler["akakce"].son_basari is not None
    assert olaylar == []


def test_yerel_sunucu_hata_turleri_ve_kademe_yukselmesi(yerel_sunucu, db, tmp_path):
    olaylar = []
    k1 = SahteK1(yanit=(200, DUZ_HTML))
    k2 = SahteK2(yanit=DUZ_HTML)
    kademe = Kademe(
        db,
        olaylar.append,
        k1=k1,
        k2=k2,
        durum_dosya=tmp_path / "durum.json",
        min_aralik=0,
        jitter=0,
    )

    # 1) 403 Cloudflare -> http_403 yazılıp K1'e yükselir
    y = kademe.getir(f"{yerel_sunucu}/cf403", site="site_403")
    assert y.yontem == "chromium"
    assert y.katman == Katman.K1
    s403 = {s.ad: s for s in db.siteler_listele()}["site_403"]
    assert s403.katman_tercihi == Katman.K1
    assert s403.hata_turu == "http_403"
    assert s403.son_hata is not None
    assert s403.son_basari is not None
    assert {"tur": "katman_degisti", "site": "site_403", "eski": "K0", "yeni": "K1"} in olaylar

    # 2) 200 Captcha -> captcha yazılıp K1 de captcha dönerse K2'ye yükselir
    k1.yanit = (200, CAPTCHA_HTML)
    y_cap = kademe.getir(f"{yerel_sunucu}/captcha", site="site_captcha")
    assert y_cap.yontem == "brave"
    assert y_cap.katman == Katman.K2
    scap = {s.ad: s for s in db.siteler_listele()}["site_captcha"]
    assert scap.katman_tercihi == Katman.K2
    assert scap.hata_turu == "captcha"
    assert {"tur": "katman_degisti", "site": "site_captcha", "eski": "K0", "yeni": "K2"} in olaylar

    # 3) 200 Boş gövde -> bos_sonuc
    k1.yanit = (200, "")
    y_bos = kademe.getir(f"{yerel_sunucu}/bos", site="site_bos")
    assert y_bos.yontem == "brave"
    sbos = {s.ad: s for s in db.siteler_listele()}["site_bos"]
    assert sbos.hata_turu == "bos_sonuc"
    assert sbos.katman_tercihi == Katman.K2


def test_hata_turu_zaman_asimi_ve_ag_hatasi(db, tmp_path):
    assert hata_turu_belirle(istisna=TimeoutError("istek zaman aşımı")) == "zaman_asimi"
    assert hata_turu_belirle(istisna=urllib.error.URLError(TimeoutError("timed out"))) == "zaman_asimi"
    assert hata_turu_belirle(istisna=urllib.error.URLError("DNS hatası")) == "ag_hatasi"

    def _timeout_k0(url, site, zaman_asimi=40.0):
        raise TimeoutError("Read timed out")

    kademe = Kademe(
        db,
        k0=_timeout_k0,
        k1=SahteK1(yanit=(200, DUZ_HTML)),
        k2=SahteK2(),
        durum_dosya=tmp_path / "durum.json",
        min_aralik=0,
        jitter=0,
    )
    y = kademe.getir("http://127.0.0.1:9/test", site="timeout_site")
    assert y.katman == Katman.K1
    s = {x.ad: x for x in db.siteler_listele()}["timeout_site"]
    assert s.hata_turu == "zaman_asimi"
    assert s.katman_tercihi == Katman.K1


# ---------------------------------------------------------------- 3. tercih öğrenme ve K1 atlama
def test_tercih_ogrenme_sonraki_istek_dogrudan_tercihten_baslar(yerel_sunucu, db, tmp_path):
    k0_cagri = []

    def sahte_k0(url, site, zaman_asimi=40.0):
        k0_cagri.append(url)
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    k1 = SahteK1(yanit=(403, CF_403_HTML))
    k2 = SahteK2(yanit=DUZ_HTML)
    kademe = Kademe(
        db,
        k0=sahte_k0,
        k1=k1,
        k2=k2,
        durum_dosya=tmp_path / "durum.json",
        min_aralik=0,
        jitter=0,
    )

    # İlk istek: K0 -> K1 -> K2
    y1 = kademe.getir("https://ornek.test/urun/1", site="ornek")
    assert y1.katman == Katman.K2
    assert len(k0_cagri) == 1
    assert len(k1.cagrilar) == 1
    assert len(k2.cagrilar) == 1

    # İkinci istek: doğrudan K2'den başlar (K0 ve K1 çağrılmaz)
    y2 = kademe.getir("https://ornek.test/urun/2", site="ornek")
    assert y2.katman == Katman.K2
    assert len(k0_cagri) == 1
    assert len(k1.cagrilar) == 1
    assert len(k2.cagrilar) == 2


def test_k1_kurulu_degilse_atlanir_ve_olay_yayinlanir(yerel_sunucu, db, tmp_path):
    olaylar = []
    k1 = SahteK1(kullanilabilir=False)
    k2 = SahteK2(yanit=DUZ_HTML)
    kademe = Kademe(
        db,
        olaylar.append,
        k1=k1,
        k2=k2,
        durum_dosya=tmp_path / "durum.json",
        min_aralik=0,
        jitter=0,
    )

    y = kademe.getir(f"{yerel_sunucu}/cf403", site="atlama_test")
    assert y.katman == Katman.K2
    assert k1.cagrilar == []
    assert any(o.get("tur") == "k1_atlandi" for o in olaylar)


# ---------------------------------------------------------------- 4. haftalık alt katman denemesi
def test_haftalik_alt_katman_denemesi(db, tmp_path):
    simdi = datetime(2026, 11, 14, 15, 0, 0, tzinfo=TZ_ISTANBUL)
    saat = [simdi]
    olaylar = []

    k0_cagri = []

    def sahte_k0(url, site, zaman_asimi=40.0):
        k0_cagri.append(url)
        return 403, CF_403_HTML

    k1 = SahteK1(yanit=(403, CF_403_HTML))
    k2 = SahteK2(yanit=DUZ_HTML)

    kademe = Kademe(
        db,
        olaylar.append,
        k0=sahte_k0,
        k1=k1,
        k2=k2,
        durum_dosya=tmp_path / "durum.json",
        min_aralik=0,
        jitter=0,
        simdi_fn=lambda: saat[0],
    )

    # Gün 0: K2 öğrenilir
    kademe.getir("https://haftalik.test/u1", site="haftalik")
    assert db.siteler_listele()[0].katman_tercihi == Katman.K2
    assert len(k1.cagrilar) == 1

    # Gün 3 (< 7 gün): alt katman denenmez, doğrudan K2
    saat[0] = simdi + timedelta(days=3)
    kademe.getir("https://haftalik.test/u2", site="haftalik")
    assert len(k1.cagrilar) == 1
    assert len(k2.cagrilar) == 2

    # Gün 8 (> 7 gün): önce bir alt katman (K1) denenir! K1 artık çalışıyor olsun:
    saat[0] = simdi + timedelta(days=8)
    k1.yanit = (200, DUZ_HTML)
    y = kademe.getir("https://haftalik.test/u3", site="haftalik")
    assert len(k1.cagrilar) == 2
    assert y.katman == Katman.K1
    assert db.siteler_listele()[0].katman_tercihi == Katman.K1
    assert {"tur": "katman_degisti", "site": "haftalik", "eski": "K2", "yeni": "K1"} in olaylar


# ---------------------------------------------------------------- 5. K3 bekleme (zaman enjeksiyonlu)
def test_k3_bekleme_kullanici_gecerse_devam_eder(db, tmp_path):
    sahte_zaman = [100.0]
    uykular = []
    olaylar = []

    def zaman_fn():
        return sahte_zaman[0]

    def uyku_fn(sn: float):
        uykular.append(sn)
        sahte_zaman[0] += sn

    # İlk 2 yoklamada (10s, 20s) hâlâ bloklu, 3. yoklamada (30s) kullanıcı doğrulamayı geçti:
    sekme_durumu = {"sayac": 0}

    class _K3Sekme:
        def content(self):
            sekme_durumu["sayac"] += 1
            if sekme_durumu["sayac"] < 3:
                return CAPTCHA_HTML
            return DUZ_HTML

    k2 = SahteK2(yanit=CAPTCHA_HTML)
    k2._sekmeler["k3_site"] = _K3Sekme()

    kademe = Kademe(
        db,
        olaylar.append,
        k0=lambda u, s, z=40: (403, CF_403_HTML),
        k1=SahteK1(yanit=(403, CF_403_HTML)),
        k2=k2,
        durum_dosya=tmp_path / "durum.json",
        bekle_k3=300.0,
        k3_aralik=10.0,
        min_aralik=0,
        jitter=0,
        zaman_fn=zaman_fn,
        uyku_fn=uyku_fn,
    )

    y = kademe.getir("https://k3.test/urun", site="k3_site")
    assert y.yontem == "brave"
    assert y.katman == Katman.K3
    assert y.html == DUZ_HTML
    assert uykular == [10.0, 10.0, 10.0]
    assert any(
        o.get("tur") == "dogrulama_gerekli" and o.get("site") == "k3_site" for o in olaylar
    )
    kayit = {s.ad: s for s in db.siteler_listele()}["k3_site"]
    assert kayit.katman_tercihi == Katman.K2
    assert kayit.son_basari is not None


def test_k3_bekleme_kullanici_gecmezse_bot_engeli(db, tmp_path):
    sahte_zaman = [0.0]
    uykular = []
    olaylar = []

    def zaman_fn():
        return sahte_zaman[0]

    def uyku_fn(sn: float):
        uykular.append(sn)
        sahte_zaman[0] += sn

    k2 = SahteK2(yanit=CAPTCHA_HTML)
    kademe = Kademe(
        db,
        olaylar.append,
        k0=lambda u, s, z=40: (403, CF_403_HTML),
        k1=SahteK1(yanit=(403, CF_403_HTML)),
        k2=k2,
        durum_dosya=tmp_path / "durum.json",
        bekle_k3=300.0,
        k3_aralik=10.0,
        min_aralik=0,
        jitter=0,
        zaman_fn=zaman_fn,
        uyku_fn=uyku_fn,
    )

    with pytest.raises(BotEngeli) as exc:
        kademe.getir("https://k3.test/engel", site="k3_engel")
    assert exc.value.site == "k3_engel"
    assert len(uykular) == 30
    assert sum(uykular) == 300.0
    assert any(o.get("tur") == "dogrulama_gerekli" for o in olaylar)
    kayit = {s.ad: s for s in db.siteler_listele()}["k3_engel"]
    assert kayit.hata_turu == "captcha"


# ---------------------------------------------------------------- 6. kilit yeniden girilebilirliği
def test_brave_kilidi_varsayilan_yol_ve_port():
    assert KILIT_YOL == Path.home() / ".local" / "share" / "kazi" / "brave.lock"
    assert BRAVE_PROFIL == Path.home() / ".local" / "share" / "kazi" / "brave_profil"
    assert CDP_PORT == 9334


def test_brave_kilidi_ayni_is_parcaciginda_yeniden_girilebilir(tmp_path):
    kilit_dosyasi = tmp_path / "brave.lock"
    with brave_kilidi(zaman_asimi=1.0, yol=kilit_dosyasi):
        with brave_kilidi(zaman_asimi=1.0, yol=kilit_dosyasi):
            assert kilit_dosyasi.is_file()

        # İç bloktan çıkıldı ama dış blok hâlâ kilidi tutuyor; başka iş parçacığı alamamalı
        hata_alindi = threading.Event()

        def _baska_ip():
            try:
                with brave_kilidi(zaman_asimi=0.15, yol=kilit_dosyasi):
                    pass
            except KilitZamanAsimi:
                hata_alindi.set()

        t = threading.Thread(target=_baska_ip)
        t.start()
        t.join(timeout=2.0)
        assert hata_alindi.is_set()

    # Dış bloktan da çıkıldı; artık başka iş parçacığı kilidi alabilmeli
    alindi = threading.Event()

    def _serbest_ip():
        with brave_kilidi(zaman_asimi=1.0, yol=kilit_dosyasi):
            alindi.set()

    t2 = threading.Thread(target=_serbest_ip)
    t2.start()
    t2.join(timeout=2.0)
    assert alindi.is_set()


# ---------------------------------------------------------------- 7. Brave yolu bulma (sahte PATH)
def _sahte_ikili(dizin: Path, ad: str, icerik: str = "#!/bin/sh\nexit 0\n") -> Path:
    yol = dizin / ad
    yol.write_text(icerik, encoding="utf-8")
    yol.chmod(0o755)
    return yol


def test_brave_yolu_bulma_oncelik_sirasi(tmp_path, monkeypatch):
    # 1) Boş PATH -> None
    bos = tmp_path / "bos"
    bos.mkdir()
    monkeypatch.setenv("PATH", str(bos))
    assert brave_yolu_bul() is None
    assert brave_komutu() is None

    # 2) Yalnız brave-browser-stable varsa
    d_stable = tmp_path / "sadece_stable"
    d_stable.mkdir()
    ikili_stable = _sahte_ikili(d_stable, "brave-browser-stable")
    assert brave_yolu_bul(path=str(d_stable)) == [str(ikili_stable)]

    # 3) brave-browser + brave-browser-stable varsa -> brave-browser öncelikli
    ikili_bb = _sahte_ikili(d_stable, "brave-browser")
    assert brave_yolu_bul(path=str(d_stable)) == [str(ikili_bb)]

    # 4) brave + brave-browser varsa -> brave öncelikli
    ikili_brave = _sahte_ikili(d_stable, "brave")
    assert brave_yolu_bul(path=str(d_stable)) == [str(ikili_brave)]

    # 5) flatpak (com.brave.Browser kurulu) varsa -> flatpak en öncelikli
    _sahte_ikili(d_stable, "flatpak", "#!/bin/sh\nexit 0\n")
    assert brave_yolu_bul(path=str(d_stable)) == ["flatpak", "run", "com.brave.Browser"]
    komut = brave_komutu(path=str(d_stable))
    assert komut is not None
    assert "--remote-debugging-port=9334" in komut
    # flatpak ~/.local/share/kazi'ye yazamaz → profil uygulamanın kalıcı veri klasöründe (15.11)
    from kazi.tarayici.profil import FLATPAK_PROFIL
    assert f"--user-data-dir={FLATPAK_PROFIL}" in komut

    # 6) flatpak var ama com.brave.Browser kurulu değilse (exit 1) -> brave'e düşer
    _sahte_ikili(d_stable, "flatpak", "#!/bin/sh\nexit 1\n")
    assert brave_yolu_bul(path=str(d_stable)) == [str(ikili_brave)]


def test_brave_bulunamazsa_k2_cokmez_kullanilamiyor_der(tmp_path, monkeypatch):
    bos = tmp_path / "bos_path"
    bos.mkdir()
    monkeypatch.setenv("PATH", str(bos))
    monkeypatch.setattr("kazi.tarayici.cekirdek._cdp_hazir", lambda *a, **kw: False)

    olaylar = []
    t = Tarayici(
        min_aralik=0,
        jitter=0,
        durum_dosya=tmp_path / "durum.json",
        bildirim_fn=olaylar.append,
        bekle_k3=0,
    )
    with pytest.raises(BotEngeli):
        t._brave_baglan()
    assert t.k2_durumu == "kullanılamıyor"
    assert any(o.get("tur") == "k2_kullanilamiyor" for o in olaylar)


# ---------------------------------------------------------------- 8. dogrudan_url_yasak -> site_ici_ara
def test_dogrudan_url_yasak_site_ici_ara_cagirir(db, tmp_path):
    assert "cimri" in DOGRUDAN_URL_YASAK_SITELER
    k0_cagri = []
    k1 = SahteK1()
    k2 = SahteK2(yanit=DUZ_HTML)

    kademe = Kademe(
        db,
        k0=lambda u, s, z=40: k0_cagri.append(u) or (200, DUZ_HTML),
        k1=k1,
        k2=k2,
        durum_dosya=tmp_path / "durum.json",
        min_aralik=0,
        jitter=0,
    )

    # Arama URL'si verildiğinde doğrudan URL'ye (K0/K1/K2.getir) GİDİLMEZ, site_ici_ara çağrılır
    y = kademe.getir("https://www.cimri.com/arama?q=rtx+5080", site="cimri")
    assert y.yontem == "brave"
    assert y.html == DUZ_HTML
    assert k0_cagri == []
    assert k1.cagrilar == []
    assert k2.cagrilar == []
    assert k2.arama_cagrilari == [("cimri", "https://www.cimri.com/", "rtx 5080")]

    # Ürün sayfası URL'si ise doğrudan açılabilir
    y_urun = kademe.getir(
        "https://www.cimri.com/ekran-kartlari/en-ucuz-rtx-5080-fiyatlari,1234",
        site="cimri",
    )
    assert y_urun.html == DUZ_HTML
    assert len(k2.cagrilar) == 1
