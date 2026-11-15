"""Çok sekmeli iş havuzu: bir site aynı anda tek işçide, eşzamanlılık ≤ N, hata izolasyonu, sıra korunur."""
import threading
import time

from kazi.tarama import havuz


def _izleyici():
    kilit = threading.Lock()
    mesgul, ihlal = set(), []

    def is_fn(kaynak, site, yuk):
        with kilit:
            if site in mesgul:
                ihlal.append(site)
            mesgul.add(site)
        time.sleep(0.02)
        with kilit:
            mesgul.discard(site)
        if yuk == "patla":
            raise ValueError("bozuk")
        return (kaynak, site, yuk)
    return is_fn, ihlal


def test_ayni_site_ayni_anda_tek_iscide_ve_eszamanlilik_siniri():
    is_fn, ihlal = _izleyici()
    isler = [(s, f"{s}-{u}") for u in range(4) for s in ("hb", "ty", "n11", "amz", "vat", "itp")]
    oz = havuz.calistir(isler, 4, kaynak_fabrika=lambda i: f"isci{i}", is_fn=is_fn)
    assert ihlal == []
    assert 2 <= oz.azami_eszamanli <= 4
    assert [s.sira for s in oz.sonuclar] == list(range(len(isler)))
    assert all(s.hata is None and s.deger[2] == s.yuk for s in oz.sonuclar)


def test_tek_site_varsa_paralellik_olmaz():
    is_fn, ihlal = _izleyici()
    oz = havuz.calistir([("hb", i) for i in range(5)], 5, lambda i: i, is_fn)
    assert ihlal == [] and oz.azami_eszamanli == 1


def test_hata_diger_isleri_durdurmaz():
    is_fn, _ = _izleyici()
    oz = havuz.calistir([("a", 1), ("b", "patla"), ("c", 3)], 3, lambda i: i, is_fn)
    assert isinstance(oz.sonuclar[1].hata, ValueError)
    assert oz.sonuclar[0].deger[2] == 1 and oz.sonuclar[2].deger[2] == 3


def test_kaynak_kurulamazsa_isler_diger_isciye_kalir_ve_kapatilir():
    is_fn, _ = _izleyici()
    kapanan = []

    def fabrika(i):
        if i == 0:
            raise RuntimeError("brave yok")
        return f"isci{i}"
    oz = havuz.calistir([(s, s) for s in "abcd"], 2, fabrika, is_fn, kaynak_kapat=kapanan.append)
    tamam = [s for s in oz.sonuclar if s.sira >= 0]
    assert all(s.hata is None for s in tamam) and len(tamam) == 4 and kapanan == ["isci1"]


def test_hic_kaynak_yoksa_isler_hata_doner():
    oz = havuz.calistir([("a", 1)], 2, lambda i: (_ for _ in ()).throw(RuntimeError("yok")), lambda k, s, y: y)
    assert any(s.sira == 0 and s.hata for s in oz.sonuclar)


def test_durdur_yeni_is_almaz():
    dur = threading.Event()

    def is_fn(k, s, y):
        dur.set()
        return y
    oz = havuz.calistir([(s, s) for s in "abcdef"], 1, lambda i: i, is_fn, durdur=dur)
    assert len([s for s in oz.sonuclar if s.hata is None]) == 1
    assert all(str(s.hata) == "durduruldu" for s in oz.sonuclar[1:])


def test_bos_is_listesi():
    assert havuz.calistir([], 3, lambda i: i, lambda k, s, y: y).sonuclar == []


def test_sekme_fabrikasi_ortak_oto_hiz_ve_paylasimli_kilit(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from kazi.tarayici.cekirdek import sekme_fabrikasi
    uret = sekme_fabrikasi()
    a, b = uret(0), uret(1)
    assert a.oto_hiz is b.oto_hiz and a._son_istek is b._son_istek
    assert a.kilit_paylasimli and b.kilit_paylasimli
    a._brave_kilidi_al()               # paylaşımlı: kilit almaz, hata da vermez
    assert a._kilit is None
    assert a.kademe.k1 is None and a.kademe._k1_atla_mi()     # işçide ikinci Playwright (K1) açılmaz
