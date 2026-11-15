"""Uçuş taramasının çok sekmeli yolu (sahte kaynaklar, tarayıcı YOK)."""
from __future__ import annotations

import contextlib
import threading
import time

from kazi.ayar import Ayar
from kazi.db import VeriTabani
from kazi.ucus import tara as tara_mod
from kazi.ucus.model import BotEngeli
from kazi.zamanlayici import calistir

from test_ucus_tara import SahteKaynak, SahteTarayici, _seg

KAYNAKLAR = ["google", "skyscanner", "kiwi"]
PLAN = {"bacaklar": [
    {"id": f"b{i}", "kalkis": "IST", "varis": "NRT", "tarih": f"2026-12-{20 + i}", "yolcu": 1, "esnek_gun": 0}
    for i in range(3)
]}


class Izleyici:
    """Kaynak başına eşzamanlılık sayacı."""
    def __init__(self, bekle=0.03):
        self.kilit = threading.Lock()
        self.aktif: dict[str, int] = {}
        self.toplam = 0
        self.azami_toplam = 0
        self.ayni_kaynak_cakisti = False
        self.bekle = bekle

    def kaynak(self, ad, fiyat, hata=None):
        iz = self

        def uret(bacak, tarih):
            with iz.kilit:
                iz.aktif[ad] = iz.aktif.get(ad, 0) + 1
                iz.toplam += 1
                iz.azami_toplam = max(iz.azami_toplam, iz.toplam)
                if iz.aktif[ad] > 1:
                    iz.ayni_kaynak_cakisti = True
            time.sleep(iz.bekle)
            with iz.kilit:
                iz.aktif[ad] -= 1
                iz.toplam -= 1
            if hata:
                raise hata
            return [_seg(ad, fiyat + len(bacak["id"]), tarih=tarih)]
        return SahteKaynak(uretici=uret)


def _kur(monkeypatch, iz, kiwi_hata=None):
    monkeypatch.setitem(tara_mod.KAYNAKLAR, "google", iz.kaynak("google", 70000.0))
    monkeypatch.setitem(tara_mod.KAYNAKLAR, "skyscanner", iz.kaynak("skyscanner", 60000.0))
    monkeypatch.setitem(tara_mod.KAYNAKLAR, "kiwi", iz.kaynak("kiwi", 65000.0, hata=kiwi_hata))


def _sade(sonuc):
    return (
        {k: (v["sayi"], v["tur"], v["yontem"]) for k, v in sonuc.kapsam.items()},
        {b: sorted(s.id for s in segs) for b, segs in sonuc.segmentler.items()},
    )


def _vt(tmp_path, ad):
    return VeriTabani(Ayar(veri_dizini=tmp_path / ad).db_yolu)


def _paralel(v=None, **kw):
    return tara_mod.tara(v, SahteTarayici(), PLAN, kaynaklar=KAYNAKLAR,
                         tarayici_fabrika=lambda no: SahteTarayici(), sekme_sayisi=3, **kw)


def test_paralel_sirali_ile_ayni_sonuc(monkeypatch, tmp_path):
    _kur(monkeypatch, Izleyici(0))
    sirali = tara_mod.tara(_vt(tmp_path, "a"), SahteTarayici(), PLAN, kaynaklar=KAYNAKLAR)
    par = _paralel(_vt(tmp_path, "b"))
    assert len(par.kapsam) == 9
    assert _sade(par) == _sade(sirali)


def test_kaynak_kendisiyle_cakismaz_ve_kaynaklar_eszamanli(monkeypatch):
    iz = Izleyici(0.05)
    _kur(monkeypatch, iz)
    _paralel()
    assert not iz.ayni_kaynak_cakisti
    assert iz.azami_toplam >= 2


def test_engelli_kaynak_sonraki_islerde_atlanir(monkeypatch):
    _kur(monkeypatch, Izleyici(0), kiwi_hata=BotEngeli("cf"))
    s = _paralel()
    turler = [s.kapsam[k]["tur"] for k in sorted(s.kapsam) if k[2] == "kiwi"]
    assert turler.count("blok") == 1 and turler.count("blok_atlandi") == 2
    assert s.kapsam[("b0", "2026-12-20", "kiwi")]["tur"] == "blok"


def test_yerel_yedek_havuzdan_sonra_cagiran_is_parcaciginda(monkeypatch):
    _kur(monkeypatch, Izleyici(0))
    monkeypatch.setattr("kazi.tarayici.paralel_ayar.paralel_ayarlar", lambda: {"yerel_profil_yedek": True})
    ana = threading.get_ident()
    yerel_cagri: list[int] = []

    class Yerel:
        def hiz_sinirla(self, *_):
            yerel_cagri.append(threading.get_ident())

        def kapat(self):
            pass

    monkeypatch.setitem(tara_mod.KAYNAKLAR, "kiwi",
                        SahteKaynak(uretici=lambda b, t: [_seg("kiwi", 50000.0, tarih=t)]))
    gercek = tara_mod._dene
    sayac = {"n": 0}

    def dene(kaynak, t, bacak, tarih, zaman):
        if kaynak == "kiwi" and not isinstance(t, Yerel):
            sayac["n"] += 1
            return [], "brave", "blok: cf", "blok"
        return gercek(kaynak, t, bacak, tarih, zaman)
    monkeypatch.setattr(tara_mod, "_dene", dene)
    s = _paralel(yerel_fabrika=Yerel)
    assert sayac["n"] == 1
    assert yerel_cagri == [ana]
    k = s.kapsam[("b0", "2026-12-20", "kiwi")]
    assert k["yontem"] == "yerel_brave" and k["tur"] == "ok" and k["sayi"] == 1
    assert s.kapsam[("b1", "2026-12-21", "kiwi")]["tur"] == "blok_atlandi"
    assert [x.kaynak for x in s.segmentler["b0"]] == ["kiwi"]


def test_site_olaylari_yazilir(monkeypatch, tmp_path):
    _kur(monkeypatch, Izleyici(0))
    v = _vt(tmp_path, "o")
    s = _paralel(v)
    ol = [o for o in v.son_olaylar(200) if o.tur == "tarama_site_bitti"]
    assert len(ol) == len(s.kapsam) == 9
    assert {o.veri["katman"] for o in ol} == {"ucus"}
    assert {o.veri["site"] for o in ol} == set(KAYNAKLAR)
    assert {"adet", "sure", "hata", "tur", "bacak_id", "tarih"} <= set(ol[0].veri)


def test_calistir_yeni_kwargleri_yalniz_gercek_fonksiyona(monkeypatch):
    import kazi.tarayici.cekirdek as cek
    import kazi.tarayici.kilit as kilit
    import kazi.tarayici.paralel_ayar as pa
    monkeypatch.setattr(pa, "paralel_ayarlar", lambda: {"sekme_sayisi": 4})
    monkeypatch.setattr(cek, "artik_pencereleri_kapat", lambda: 0)
    monkeypatch.setattr(cek, "sekme_fabrikasi", lambda v, b: "FABRIKA")
    girdi = []

    @contextlib.contextmanager
    def sahte_kilit(*_a, **_k):
        girdi.append(1)
        yield
    monkeypatch.setattr(kilit, "brave_kilidi", sahte_kilit)
    alinan: dict = {}
    monkeypatch.setattr(calistir.ucus_tara_mod, "tara", lambda v, t, **kw: alinan.update(kw) or "SONUC")
    assert calistir._paralel_ucus_tara(None, object(), calistir.ucus_tara_mod.tara, {"plan": 1}) == "SONUC"
    assert alinan == {"plan": 1, "sekme_sayisi": 4, "tarayici_fabrika": "FABRIKA"} and girdi == [1]
    monkeypatch.setattr(pa, "paralel_ayarlar", lambda: {"sekme_sayisi": 1})
    alinan.clear()
    calistir._paralel_ucus_tara(None, object(), calistir.ucus_tara_mod.tara, {"plan": 1})
    assert alinan == {"plan": 1}
