"""Görünür Chromium yedek katmanı (tara._bir_site) — Playwright AÇILMAZ, sahte nesneler."""
from __future__ import annotations

from pathlib import Path

import pytest

from kazi.ayar import Ayar
from kazi.db import VeriTabani
from kazi.modeller import Katman, UrunKimligi
from kazi.siteler._kart import Kart
from kazi.tarama import tara as tara_mod
from kazi.tarayici import BotEngeli
from kazi.tarayici.gorunur_chromium import CHROMIUM_PROFIL, GorunurChromium


class SahteTarayici:
    def __init__(self, sira: list | None = None, ad: str = "brave"):
        self.brave_acik = False
        self.kapandi = 0
        self.birakildi = 0
        self.bildirimler: list[dict] = []
        self._sira = sira if sira is not None else []
        self._ad = ad

    def brave_birak(self) -> None:
        self.birakildi += 1
        self._sira.append(f"{self._ad}_birak")

    def kapat(self) -> None:
        self.kapandi += 1
        self._sira.append(f"{self._ad}_kapat")


class BlokluSite:
    """Brave tarayıcıyla BotEngeli; yedek tarayıcıyla (ad == chromium) kart döner."""

    def __init__(self, yedek_sonuc="kart", sira: list | None = None):
        self.yedek_sonuc = yedek_sonuc
        self.sira = sira if sira is not None else []

    def ara(self, t, sorgu, min_f=None, max_f=None, sayfa=2):
        if getattr(t, "_ad", "") != "chromium":
            self.sira.append("brave_ara")
            raise BotEngeli("n11", "https://www.n11.com")
        self.sira.append("chromium_ara")
        if isinstance(self.yedek_sonuc, BaseException):
            raise self.yedek_sonuc
        return [Kart(site="n11", baslik="Inno3D RTX 5080 X3 16GB", url="https://www.n11.com/u/1",
                     sepet_fiyati=115000.0, satici="X", yontem="brave")]


@pytest.fixture
def v(tmp_path: Path):
    vt = VeriTabani(Ayar(veri_dizini=tmp_path).db_yolu)
    vt.urun_ekle(UrunKimligi(id="gpu", kanonik_ad="RTX 5080", eslesme_kurallari={
        "sorgular": ["rtx 5080"], "dahil": ["5080"], "min_fiyat": 90000, "max_fiyat": 260000}))
    yield vt
    vt.kapat()


def _calistir(v, monkeypatch, site, *, yedek_ayari=True, yedek_var=True, sira=None):
    monkeypatch.setattr(tara_mod, "SITELER", {"n11": site})
    monkeypatch.setattr(tara_mod, "paralel_ayarlar", lambda: {"chromium_yedek": yedek_ayari})
    sira = sira if sira is not None else []
    brave = SahteTarayici(sira, "brave")
    yedekler: list[SahteTarayici] = []

    def fabrika():
        sira.append("fabrika")
        y = SahteTarayici(sira, "chromium")
        yedekler.append(y)
        return y

    olaylar: list[dict] = []
    sonuc = tara_mod.tara(v, brave, urun_idler=["gpu"], bildirim_fn=olaylar.append,
                          yedek_fabrika=fabrika if yedek_var else None)
    return sonuc, brave, yedekler, olaylar


def test_blok_yedek_cagrilir_ve_kaydedilir(v, monkeypatch):
    sira: list = []
    sonuc, brave, yedekler, olaylar = _calistir(v, monkeypatch, BlokluSite(sira=sira), sira=sira)
    assert len(yedekler) == 1 and yedekler[0].kapandi == 1
    assert len(sonuc.teklifler["gpu"]) == 1
    kap = sonuc.kapsam[("gpu", "n11")]
    assert kap["tur"] == "ok" and kap["yontem"] == "chromium_gorunur" and kap["katman"] == str(Katman.K1)
    assert olaylar[-1]["yedek"] == "chromium_gorunur"
    assert len(v.teklifler("gpu")) == 1


def test_brave_birak_yedekten_once(v, monkeypatch):
    sira: list = []
    _calistir(v, monkeypatch, BlokluSite(sira=sira), sira=sira)
    assert sira.index("brave_birak") < sira.index("fabrika") < sira.index("chromium_ara")
    assert sira.index("chromium_kapat") > sira.index("chromium_ara")


def test_ayar_kapaliysa_yedek_yok(v, monkeypatch):
    sonuc, _, yedekler, olaylar = _calistir(v, monkeypatch, BlokluSite(), yedek_ayari=False)
    assert yedekler == []
    assert sonuc.kapsam[("gpu", "n11")]["tur"] == "blok" and "yedek" not in olaylar[-1]


def test_fabrika_yoksa_yedek_yok(v, monkeypatch):
    sonuc, _, yedekler, _ = _calistir(v, monkeypatch, BlokluSite(), yedek_var=False)
    assert yedekler == [] and sonuc.kapsam[("gpu", "n11")]["tur"] == "blok"


def test_yedek_de_hata_verirse_tarama_dusmez(v, monkeypatch):
    sonuc, _, yedekler, olaylar = _calistir(v, monkeypatch, BlokluSite(yedek_sonuc=RuntimeError("patladı")))
    assert len(yedekler) == 1 and yedekler[0].kapandi == 1
    kap = sonuc.kapsam[("gpu", "n11")]
    assert kap["tur"] == "blok" and kap["yontem"] != "chromium_gorunur"
    assert "yedek" not in olaylar[-1]


def test_yedek_fabrikasi_patlarsa_tarama_dusmez(v, monkeypatch):
    monkeypatch.setattr(tara_mod, "SITELER", {"n11": BlokluSite()})
    monkeypatch.setattr(tara_mod, "paralel_ayarlar", lambda: {"chromium_yedek": True})

    def kotu():
        raise OSError("chromium yok")

    sonuc = tara_mod.tara(v, SahteTarayici(), urun_idler=["gpu"], yedek_fabrika=kotu)
    assert sonuc.kapsam[("gpu", "n11")]["tur"] == "blok"


def test_gorunur_chromium_yapilandirma(tmp_path):
    g = GorunurChromium(profil=tmp_path / "p", kilit_dosya=tmp_path / "k.lock", durum_dosya=tmp_path / "d.json")
    assert g.kilit_paylasimli is True and g.kademe.k1 is None
    assert g.kademe._denenecek_katmanlar("epey") == [Katman.K2]
    assert CHROMIUM_PROFIL.name == "chromium_profil"
    g.kapat()          # bağlanmamışken sorunsuz
