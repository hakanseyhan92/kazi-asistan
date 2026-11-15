"""Arama kutusu seçici hafızası ve `_kutu_bul` sıralaması (Playwright açmadan)."""
from __future__ import annotations

import json
import threading

from kazi.tarayici import secici_hafiza
from kazi.tarayici.cekirdek import Tarayici


def test_oku_yazilmamissa_none():
    assert secici_hafiza.oku("idefix") is None


def test_kaydet_oku_unut_ve_basari_sayaci():
    secici_hafiza.kaydet("idefix", "input#a")
    assert secici_hafiza.oku("idefix") == "input#a"
    secici_hafiza.kaydet("idefix", "input#a")
    veri = json.loads(secici_hafiza.yol().read_text(encoding="utf-8"))
    assert veri["idefix"]["basari"] == 2 and veri["idefix"]["zaman"]
    secici_hafiza.kaydet("idefix", "input#b")
    assert json.loads(secici_hafiza.yol().read_text())["idefix"]["basari"] == 1
    secici_hafiza.kaydet("amazon", "input#x")
    secici_hafiza.unut("idefix")
    assert secici_hafiza.oku("idefix") is None
    assert secici_hafiza.oku("amazon") == "input#x"
    secici_hafiza.unut("olmayan")      # hata vermez


def test_bozuk_dosya_bos_sayilir_ve_onarilir():
    secici_hafiza.yol().write_text("{bozuk", encoding="utf-8")
    assert secici_hafiza.oku("x") is None
    secici_hafiza.kaydet("x", "input")
    assert secici_hafiza.oku("x") == "input"
    secici_hafiza.yol().write_text("[1,2]", encoding="utf-8")
    assert secici_hafiza.oku("x") is None


def test_is_parcacigi_guvenligi():
    def calis(i):
        for j in range(15):
            secici_hafiza.kaydet(f"site{i}", f"input#{j}")

    ts = [threading.Thread(target=calis, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    veri = json.loads(secici_hafiza.yol().read_text(encoding="utf-8"))
    assert sorted(veri) == sorted(f"site{i}" for i in range(8))
    assert all(v["secici"] == "input#14" for v in veri.values())


# ------------------------------------------------------------ _kutu_bul sıralaması
class Loc:
    def __init__(self, sayfa, secici):
        self.sayfa, self.secici = sayfa, secici

    @property
    def first(self):
        return self

    def _var(self):
        return self.secici in self.sayfa.gorunur

    def count(self):
        return 1 if self._var() else 0

    def is_visible(self):
        return self._var()

    def is_enabled(self):
        return self.secici not in self.sayfa.pasif

    def click(self, timeout=None):
        self.sayfa.tiklar.append(self.secici)


class Sayfa:
    def __init__(self, gorunur, pasif=()):
        self.gorunur, self.pasif, self.tiklar, self.sorulan = set(gorunur), set(pasif), [], []

    def locator(self, secici):
        self.sorulan.append(secici)
        return Loc(self, secici)

    def get_by_placeholder(self, ph, exact=False):
        return Loc(self, f"ph:{ph}")

    def get_by_role(self, rol):
        return Loc(self, f"rol:{rol}")

    def wait_for_timeout(self, ms):
        pass


def _t(tmp_path):
    return Tarayici(min_aralik=0, jitter=0, durum_dosya=tmp_path / "durum.json")


def test_hafiza_once_denenir(tmp_path):
    secici_hafiza.kaydet("s", "input#hafiza")
    pg = Sayfa({"input#hafiza", "input#kutu"})
    etiket, loc = _t(tmp_path)._kutu_bul(pg, ("input#kutu",), "s")
    assert etiket == "hafiza:input#hafiza" and loc.secici == "input#hafiza"
    assert pg.sorulan[0] == "input#hafiza"
    assert secici_hafiza.oku("s") == "input#hafiza"


def test_basarisiz_hafiza_unutulur_ve_site_kutusuna_dusulur(tmp_path):
    secici_hafiza.kaydet("s", "input#eski")
    pg = Sayfa({"input#kutu"})
    etiket, loc = _t(tmp_path)._kutu_bul(pg, ("input#kutu",), "s")
    assert etiket == "secici:input#kutu"
    assert secici_hafiza.oku("s") == "input#kutu"      # eski unutuldu, yenisi yazıldı


def test_pasif_kutu_kabul_edilmez(tmp_path):
    pg = Sayfa({"input#kutu"}, pasif={"input#kutu"})
    assert _t(tmp_path)._kutu_bul(pg, ("input#kutu",), "s") == (None, None)
    assert secici_hafiza.oku("s") is None


def test_sezgisel_aday_bulunursa_kaydedilir(tmp_path):
    pg = Sayfa({'input[placeholder*="ara" i]'})
    etiket, loc = _t(tmp_path)._kutu_bul(pg, (), "s")
    assert etiket == 'sezgisel:input[placeholder*="ara" i]'
    assert secici_hafiza.oku("s") == 'input[placeholder*="ara" i]'


def test_bulunamazsa_hafiza_bos_kalir_ve_sitesiz_cagri_yazmaz(tmp_path):
    assert _t(tmp_path)._kutu_bul(Sayfa(set()), (), "s") == (None, None)
    assert secici_hafiza.oku("s") is None
    _t(tmp_path)._kutu_bul(Sayfa({"input#kutu"}), ("input#kutu",))
    assert not secici_hafiza.yol().exists()


def test_arama_dugmesi_tiklanip_kutu_aranir(tmp_path):
    class Acilir(Sayfa):
        def locator(self, secici):
            if self.tiklar:
                self.gorunur.add("input[type=\"search\"]")
            return super().locator(secici)

    pg = Acilir({'button[aria-label*="ara" i]'})
    etiket, loc = _t(tmp_path)._kutu_bul(pg, (), "s")
    assert pg.tiklar == ['button[aria-label*="ara" i]']
    assert loc is not None and "input[type=\"search\"]" in etiket
