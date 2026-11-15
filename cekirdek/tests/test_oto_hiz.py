"""OtoHiz (siteye göre öğrenen bekleme) testleri — sahte saat, gerçek uyku yok."""
from __future__ import annotations

import threading

import pytest

from kazi.tarayici.oto_hiz import OtoHiz


class SahteSaat:
    """Denetlenebilir monoton saat."""

    def __init__(self) -> None:
        self.an = 0.0

    def __call__(self) -> float:
        return self.an

    def ilerlet(self, sn: float) -> None:
        self.an += sn


def kur(**kw):
    """Varsayılanları sahte saat/uyku ve sabit (1,0) rastgelelikle doldur."""
    saat = SahteSaat()
    uyunan: list[float] = []

    def uyku(sn: float) -> None:
        uyunan.append(sn)
        saat.ilerlet(sn)

    kw.setdefault("zaman_fn", saat)
    kw.setdefault("uyku_fn", uyku)
    kw.setdefault("rastgele_fn", lambda a, b: 1.0)
    return OtoHiz(**kw), saat, uyunan


def test_baslangic_degeri_ve_bilinmeyen_site():
    oto, _, _ = kur()
    assert oto.bekleme("a.com") == pytest.approx(2.5)
    assert oto.durum() == {}
    # Özel başlangıç sınırların dışındaysa kırpılır.
    yuksek, _, _ = kur(baslangic_sn=999)
    dusuk, _, _ = kur(baslangic_sn=0.1)
    assert yuksek.bekleme("a") == 60.0
    assert dusuk.bekleme("a") == 2.0


def test_hizli_yanitta_alt_sinira_inis():
    oto, _, _ = kur()
    onceki = oto.bekleme("a")
    for _ in range(40):
        oto.yanit("a", sure_sn=0.1)
        yeni = oto.bekleme("a")
        assert yeni <= onceki + 1e-9
        onceki = yeni
    assert oto.bekleme("a") == pytest.approx(2.0)
    assert oto.durum()["a"]["son_gecikme"] == pytest.approx(0.1)


def test_hizli_yanitta_alt_sinir_altina_inmez():
    oto, _, _ = kur()
    for _ in range(50):
        oto.yanit("a", sure_sn=0.0)
    assert oto.bekleme("a") == pytest.approx(2.0)


def test_429_beklemeyi_iki_katina_cikarir():
    oto, _, _ = kur()
    oto.yanit("a", sure_sn=0.4, durum=429)
    assert oto.bekleme("a") == pytest.approx(5.0)
    oto.yanit("a", sure_sn=0.4, durum=429)
    assert oto.bekleme("a") == pytest.approx(10.0)
    assert oto.durum()["a"]["engel_sayisi"] == 2


def test_503_ve_engel_bayragi_da_yavaslatir():
    oto, _, _ = kur()
    oto.yanit("a", sure_sn=0.2, durum=503)
    assert oto.bekleme("a") == pytest.approx(5.0)
    oto.yanit("b", sure_sn=0.2, durum=200, engel=True)
    assert oto.bekleme("b") == pytest.approx(5.0)


def test_retry_after_iki_kat_yerine_kullanilir():
    oto, _, _ = kur()
    oto.yanit("a", sure_sn=0.3, durum=429, retry_after=30.0)
    assert oto.bekleme("a") == pytest.approx(30.0)
    # İki katından kısa Retry-After yok sayılır.
    oto.yanit("b", sure_sn=0.3, durum=429, retry_after=1.0)
    assert oto.bekleme("b") == pytest.approx(5.0)


def test_ust_sinir_asilmaz():
    oto, _, _ = kur(ust_sn=60.0)
    for _ in range(20):
        oto.yanit("a", sure_sn=0.3, durum=429, retry_after=999.0)
    assert oto.bekleme("a") == pytest.approx(60.0)


def test_engelden_sonra_kademeli_donus():
    oto, _, _ = kur()
    for _ in range(4):  # 2.5 -> 5 -> 10 -> 20 -> 40
        oto.yanit("a", sure_sn=0.3, durum=429)
    tepe = oto.bekleme("a")
    assert tepe == pytest.approx(40.0)

    gorulen = [tepe]
    for _ in range(60):
        oto.yanit("a", sure_sn=0.2)
        gorulen.append(oto.bekleme("a"))

    assert all(2.0 <= d <= 60.0 for d in gorulen)
    assert all(y <= x + 1e-9 for x, y in zip(gorulen, gorulen[1:]))  # hep azalır/aynı kalır
    assert gorulen[-1] == pytest.approx(2.0)
    assert oto.durum()["a"]["engel_sayisi"] == 4


def test_basarisiz_yanit_beklemeyi_dusurmez():
    oto, _, _ = kur()
    oto.yanit("a", sure_sn=0.1, durum=500)
    assert oto.bekleme("a") == pytest.approx(5.0)
    oto.yanit("a", sure_sn=0.1, durum=None)  # ağ hatası
    assert oto.bekleme("a") == pytest.approx(10.0)


def test_siteler_bagimsiz():
    oto, _, _ = kur()
    oto.yanit("hizli.com", sure_sn=0.1)
    oto.yanit("engelli.com", sure_sn=0.1, durum=429)
    for _ in range(10):
        oto.yanit("hizli.com", sure_sn=0.1)
    assert oto.bekleme("hizli.com") == pytest.approx(2.0)
    assert oto.bekleme("engelli.com") == pytest.approx(5.0)
    assert set(oto.durum()) == {"hizli.com", "engelli.com"}


def test_bekle_ilk_istekte_uyumaz_sonra_gereken_kadar_uyur():
    oto, saat, uyunan = kur()
    assert oto.bekle("a") == pytest.approx(0.0)
    assert uyunan == []

    assert oto.bekle("a") == pytest.approx(2.5)
    assert uyunan == [2.5]
    assert saat() == pytest.approx(2.5)

    # Hemen gelen istek yine tam aralığı bekler.
    assert oto.bekle("a") == pytest.approx(2.5)
    assert uyunan == [2.5, 2.5]


def test_bekle_gecen_sureyi_duser():
    oto, saat, uyunan = kur()
    oto.bekle("a")           # ilk istek
    oto.bekle("a")           # 2.5 sn uyku
    saat.ilerlet(1.0)        # istekler arasında 1 sn zaten geçti
    assert oto.bekle("a") == pytest.approx(1.5)
    assert uyunan[-1] == pytest.approx(1.5)


def test_bekle_jitter_uygular():
    oto, _, _ = kur()
    oto.bekle("a")           # ilk istek: uyku yok
    degerler = iter([0.9, 1.1])
    oto._rastgele_fn = lambda a, b: next(degerler)  # noqa: SLF001 — test enjeksiyonu
    assert oto.bekle("a") == pytest.approx(2.5 * 0.9)
    assert oto.bekle("a") == pytest.approx(2.5 * 1.1)


def test_hedef_eszamanlilik_beklemeyi_etkiler():
    oto, _, _ = kur(hedef_eszamanlilik=2.0)
    oto.yanit("a", sure_sn=10.0)  # hedef = 5.0, yeni = (2.5 + 5.0) / 2
    assert oto.bekleme("a") == pytest.approx(3.75)


def test_durum_kopya_dondurur():
    oto, _, _ = kur()
    oto.yanit("a", sure_sn=0.2)
    d = oto.durum()
    d["a"]["bekleme"] = 999.0
    d["b"] = {"bekleme": 1.0}
    assert oto.bekleme("a") != 999.0
    assert "b" not in oto.durum()


def test_eszamanli_yanit_cagrilari_bozulmaz():
    oto, _, _ = kur()
    hatalar: list[BaseException] = []
    bariyer = threading.Barrier(2)

    def calisan(durum: int, adet: int) -> None:
        try:
            bariyer.wait()
            for _ in range(adet):
                oto.yanit("a", sure_sn=0.2, durum=durum)
        except BaseException as e:  # pragma: no cover - yalnız hatada
            hatalar.append(e)

    t1 = threading.Thread(target=calisan, args=(429, 60))
    t2 = threading.Thread(target=calisan, args=(429, 60))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert hatalar == []
    kayit = oto.durum()["a"]
    assert kayit["engel_sayisi"] == 120
    assert 0.0 <= kayit["bekleme"] <= 60.0


def test_gecersiz_argumanlar():
    with pytest.raises(ValueError):
        OtoHiz(hedef_eszamanlilik=0)
    with pytest.raises(ValueError):
        OtoHiz(alt_sn=10, ust_sn=5)
    with pytest.raises(ValueError):
        OtoHiz(alt_sn=-1)
