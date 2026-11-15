"""Havuz pencere düzeni (15.11): işi biten sitenin penceresi kapanır; işçi kendi açık penceresinin sitesini önce alır."""
import threading
import time

from kazi.tarama import havuz
from kazi.tarayici.cekirdek import Tarayici


class Kaynak:
    def __init__(self, no):
        self.no = no
        self.acik: set[str] = set()
        self.gunluk: list[tuple] = []


def _calistir(isler, isci=2, sure=0.02):
    kaynaklar = []
    kilit = threading.Lock()

    def fabrika(no):
        k = Kaynak(no)
        with kilit:
            kaynaklar.append(k)
        return k

    def is_fn(k, site, yuk):
        k.acik.add(site)
        k.gunluk.append(("is", site, yuk))
        time.sleep(sure)
        return yuk

    def bitti(k, site):
        k.gunluk.append(("kapat", site, None))
        k.acik.discard(site)

    ozet = havuz.calistir(isler, isci, kaynak_fabrika=fabrika, is_fn=is_fn, site_bitti=bitti)
    return ozet, kaynaklar


def test_biten_site_penceresi_tarama_sirasinda_kapanir():
    # a: 1 iş (hemen biter), b: 6 iş (uzun sürer) → a'nın penceresi b sürerken kapanmalı
    isler = [("a", 0)] + [("b", i) for i in range(1, 7)] + [("c", 9)]
    ozet, kaynaklar = _calistir(isler, isci=2)
    assert [r.deger for r in ozet.sonuclar] == [0, 1, 2, 3, 4, 5, 6, 9]
    a_sahibi = next(k for k in kaynaklar if ("is", "a", 0) in k.gunluk)
    g = a_sahibi.gunluk
    assert ("kapat", "a", None) in g
    # a kapatıldıktan SONRA bu işçi başka iş yaptı (tarama sonunu beklemedi) ya da iş kalmadı
    assert g.index(("kapat", "a", None)) < len(g) - 1 or all(x[0] == "kapat" for x in g[g.index(("is", "a", 0)) + 1:])


def test_kendi_penceresinin_sitesini_once_alir():
    # tek işçi: a, b, a, b sırası → aynı siteyi art arda almalı (a, a, b, b) 
    isler = [("a", 1), ("b", 2), ("a", 3), ("b", 4)]
    ozet, kaynaklar = _calistir(isler, isci=1, sure=0.0)
    sira = [x[2] for x in kaynaklar[0].gunluk if x[0] == "is"]
    assert sira == [1, 3, 2, 4]
    kapatma = [x[1] for x in kaynaklar[0].gunluk if x[0] == "kapat"]
    assert kapatma == ["a", "b"]     # her site işi bitince kapanır (son site dahil)


def test_anahtar_eslesme_ve_sekme_kapat():
    E = Tarayici._anahtar_eslesir
    assert E("enuygun", "enuygun.com") and E("kayak", "www.kayak.com.tr") and E("hepsiburada", "hepsiburada")
    assert not E("n11", "trendyol.com") and not E("", "x.com")

    class Sayfa:
        def __init__(self):
            self.kapali = False

        def is_closed(self):
            return self.kapali

        def close(self):
            self.kapali = True

    t = Tarayici(veritabani=None, bildirim_fn=lambda o: None)
    e, k = Sayfa(), Sayfa()
    t._sekmeler.update({"enuygun.com": e, "kayak.com.tr": k})
    assert t.sekme_kapat("enuygun") == 1
    assert e.kapali and not k.kapali and list(t._sekmeler) == ["kayak.com.tr"]
