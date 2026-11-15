"""Tarama orkestrası (`kazi.tarama.tara`) birim testleri — AĞ YOK."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kazi.ayar import Ayar
from kazi.db import VeriTabani
from kazi.modeller import TZ_ISTANBUL, Katman, UrunKimligi
from kazi.siteler._kart import Kart
from kazi.tarama import tara as tara_mod
from kazi.tarayici import BotEngeli


class SahteTarayici:
    def __init__(self, brave_acik: bool = False):
        self.brave_acik = brave_acik
        self.birak_sayisi = 0
        self.kapandi = False
        self.bildirimler: list[dict] = []

    def brave_birak(self) -> None:
        self.birak_sayisi += 1

    def kapat(self) -> None:
        self.kapandi = True


class SahteSite:
    def __init__(self, sonuc, teklifler_sonuc=None):
        self.sonuc = sonuc
        self.teklifler_sonuc = teklifler_sonuc
        self.cagrilar: list[tuple[str, float | None, float | None, int]] = []

    def ara(self, t, sorgu: str, min_f=None, max_f=None, sayfa: int = 2):
        self.cagrilar.append((sorgu, min_f, max_f, sayfa))
        if isinstance(self.sonuc, BaseException):
            raise self.sonuc
        if callable(self.sonuc):
            return self.sonuc(t, sorgu, min_f, max_f, sayfa)
        return [
            Kart(
                site=k.site,
                baslik=k.baslik,
                url=k.url,
                sepet_fiyati=k.sepet_fiyati,
                liste_fiyati=k.liste_fiyati,
                satici=k.satici,
                kaynak=k.kaynak,
                pesin_taksit=k.pesin_taksit,
                stok=k.stok,
                yontem=k.yontem,
                zaman=k.zaman,
                guncelleme=k.guncelleme,
                durum=k.durum,
                kampanya=k.kampanya,
            )
            for k in self.sonuc
        ]

    def teklifler(self, t, model_url: str):
        if self.teklifler_sonuc is None:
            return []
        if isinstance(self.teklifler_sonuc, BaseException):
            raise self.teklifler_sonuc
        return list(self.teklifler_sonuc)


def _kart(**kw) -> Kart:
    varsayilan = dict(
        site="n11",
        baslik="Inno3D RTX 5080 X3 16GB GDDR7 Ekran Kartı",
        url="https://www.n11.com/urun/inno3d-rtx-5080-x3-p-1",
        sepet_fiyati=118000.0,
        satici="GarajOnline",
        pesin_taksit=6,
        stok=True,
        yontem="http",
    )
    varsayilan.update(kw)
    return Kart(**varsayilan)


@pytest.fixture
def v(tmp_path: Path):
    vt = VeriTabani(Ayar(veri_dizini=tmp_path).db_yolu)
    vt.urun_ekle(
        UrunKimligi(
            id="gpu_rtx5080",
            kanonik_ad="RTX 5080",
            eslesme_kurallari={
                "sorgular": ["rtx 5080"],
                "dahil": ["5080"],
                "haric": [r"laptop", r"notebook", r"5080\s*ti", r"haz[ıi]r\s*sistem"],
                "min_fiyat": 90000,
                "max_fiyat": 260000,
            },
        )
    )
    yield vt
    vt.kapat()


def test_tara_eslesen_ve_elenen_gerekceleri(v: VeriTabani, monkeypatch: pytest.MonkeyPatch):
    kartlar = [
        _kart(baslik="Inno3D RTX 5080 X3 16GB", sepet_fiyati=115000.0, url="https://www.n11.com/u/1"),
        _kart(baslik="MSI RTX 5070 Ti 16GB", sepet_fiyati=95000.0, url="https://www.n11.com/u/2"),
        _kart(baslik="Asus ROG RTX 5080 Gaming Laptop", sepet_fiyati=145000.0, url="https://www.n11.com/u/3"),
        _kart(baslik="RTX 5080 Destek Aparatı", sepet_fiyati=450.0, url="https://www.n11.com/u/4"),
    ]
    n11_site = SahteSite(kartlar)
    monkeypatch.setattr(tara_mod, "SITELER", {"n11": n11_site})

    olaylar: list[dict] = []
    t = SahteTarayici()
    sonuc = tara_mod.tara(v, t, urun_idler=["gpu_rtx5080"], bildirim_fn=olaylar.append)

    # Yalnız 1 kart eşleşti, 3 kart elendi
    assert len(sonuc.teklifler["gpu_rtx5080"]) == 1
    eklenen = sonuc.teklifler["gpu_rtx5080"][0]
    assert eklenen.sepet_fiyati == 115000.0
    assert eklenen.site == "n11"
    assert eklenen.pesin_fiyatina_taksit is True

    # Elenenlerin sayısı ve örnek gerekçeleri sonuçta yer alır
    assert sonuc.elenen_sayisi["gpu_rtx5080"] == 3
    assert sonuc.toplam_elenen == 3
    ornekler = sonuc.elenen_ornekleri["gpu_rtx5080"]
    assert len(ornekler) == 3
    gerekce_metinleri = " | ".join(o["gerekce"] for o in ornekler)
    assert "zorunlu desen eksik" in gerekce_metinleri
    assert "hariç tutulan ifade" in gerekce_metinleri
    assert "fiyat alt sınırın altında" in gerekce_metinleri

    # DB'ye yalnız eşleşen teklif ve taksit yazıldı
    db_teklifler = v.teklifler("gpu_rtx5080")
    assert len(db_teklifler) == 1
    assert db_teklifler[0].sepet_fiyati == 115000.0
    taksitler = v.taksitler(db_teklifler[0].id)
    assert len(taksitler) == 1 and taksitler[0].taksit_sayisi == 6

    # Olay yayını (tarama_site_bitti)
    assert len(olaylar) == 1
    assert olaylar[0]["tur"] == "tarama_site_bitti"
    assert olaylar[0]["site"] == "n11"
    assert olaylar[0]["urun_id"] == "gpu_rtx5080"
    assert olaylar[0]["adet"] == 1
    assert olaylar[0]["katman"] == "K0"
    assert olaylar[0]["sure"] >= 0


def test_site_hatasi_ve_bot_engeli_turu_oldurmez(v: VeriTabani, monkeypatch: pytest.MonkeyPatch):
    siteler = {
        "n11": SahteSite([_kart(site="n11", sepet_fiyati=118000.0)]),
        "trendyol": SahteSite(RuntimeError("JSON ayrıştırma çöktü")),
        "hepsiburada": SahteSite(BotEngeli("hepsiburada", "https://www.hepsiburada.com/ara?q=5080")),
    }
    monkeypatch.setattr(tara_mod, "SITELER", siteler)

    bildirimler: list[dict] = []
    sonuc = tara_mod.tara(
        v,
        SahteTarayici(),
        urun_idler=["gpu_rtx5080"],
        bildirim_fn=bildirimler.append,
    )

    assert sonuc.kapsam[("gpu_rtx5080", "n11")]["tur"] == "ok"
    assert sonuc.kapsam[("gpu_rtx5080", "n11")]["sayi"] == 1
    assert sonuc.kapsam[("gpu_rtx5080", "trendyol")]["tur"] == "hata"
    assert "JSON ayrıştırma çöktü" in sonuc.kapsam[("gpu_rtx5080", "trendyol")]["hata"]
    assert sonuc.kapsam[("gpu_rtx5080", "hepsiburada")]["tur"] == "blok"
    assert sonuc.kapsam[("gpu_rtx5080", "hepsiburada")]["hata"].startswith("blok:")

    # Diğer sitelerdeki hatalar turu öldürmedi; n11 teklifi DB'de
    assert len(v.teklifler("gpu_rtx5080")) == 1
    assert len(bildirimler) == 3
    assert sonuc.site_ok() == ["n11"]
    assert sonuc.urun_ok() == ["gpu_rtx5080"]


def test_tekillestirme_bayatlik_ve_hizli_siteler(v: VeriTabani, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    eski_zaman = (datetime.now(TZ_ISTANBUL) - timedelta(hours=30)).isoformat(timespec="seconds")
    yeni_zaman = datetime.now(TZ_ISTANBUL).isoformat(timespec="seconds")

    n11_site = SahteSite(
        [
            _kart(
                site="n11",
                satici="MagazaA",
                baslik="Asus RTX 5080 16GB",
                sepet_fiyati=120000.0,
                url="https://www.n11.com/urun/asus-5080-p-1",
            )
        ]
    )
    cimri_site = SahteSite(
        [
            _kart(
                site="cimri",
                kaynak="n11",
                satici="MAGAZAA",
                baslik="Asus RTX 5080 16GB",
                sepet_fiyati=120000.0,
                url="https://www.n11.com/urun/asus-5080-p-1/",
                guncelleme=yeni_zaman,
            ),
            _kart(
                site="cimri",
                kaynak="pttavm",
                satici="EskiSatici",
                baslik="MSI RTX 5080 16GB",
                sepet_fiyati=119000.0,
                url="https://www.pttavm.com/urun/msi-5080-p-2",
                guncelleme=eski_zaman,
            ),
        ]
    )
    monkeypatch.setattr(tara_mod, "SITELER", {"n11": n11_site, "cimri": cimri_site})

    t = SahteTarayici(brave_acik=True)
    sonuc = tara_mod.tara(v, t, urun_idler=["gpu_rtx5080"])

    # 3 karttan n11 + cimri tekilleşti -> toplam 2 teklif
    teklifler = sonuc.teklifler["gpu_rtx5080"]
    assert len(teklifler) == 2
    asus = next(x for x in teklifler if "Asus" in (x.baslik or ""))
    assert asus.site == "n11"
    assert "n11" in getattr(asus, "kaynaklar", "")
    assert "cimri" in getattr(asus, "kaynaklar", "")

    msi = next(x for x in teklifler if "MSI" in (x.baslik or ""))
    assert getattr(msi, "durum", "") == "bayat"

    # Her site sonunda brave_birak çağrıldı, ancak dışarıdan verilen tarayıcı kapatılmadı
    assert t.birak_sayisi == 2
    assert t.kapandi is False

    # Hızlı kip: yalnız K0/HTTP siteleri seçilir
    durum_dosyasi = tmp_path / "durum.json"
    durum_dosyasi.write_text(
        '{"n11": {"son_yontem": "http"}, "cimri": {"son_yontem": "brave"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(tara_mod, "DURUM_DOSYA", durum_dosyasi)
    assert tara_mod.http_siteleri(durum_dosya=durum_dosyasi) == ["n11"]


def test_paralel_sekme_sirali_ile_ayni_sonuc_ve_site_basina_tek_sekme(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """15.11: sekme_sayisi>1 → ürün×site işleri havuzda; sonuçlar sıralı taramayla aynı, bir site aynı anda tek sekmede,
    iki ürünün _ele_kaydet'i karışmaz (döngü kapanış tuzağı)."""
    import threading
    import time as _time

    kilit, mesgul, ihlal, iscler = threading.Lock(), set(), [], set()

    def _site(ad, fiyat):
        def sonuc(t, sorgu, *_):
            with kilit:
                if ad in mesgul:
                    ihlal.append(ad)
                mesgul.add(ad)
                iscler.add(id(t))
            _time.sleep(0.03)
            with kilit:
                mesgul.discard(ad)
            if "9800" in sorgu:
                return [_kart(site=ad, baslik="AMD Ryzen 7 9800X3D", sepet_fiyati=fiyat / 5, url=f"https://{ad}.com/c"),
                        _kart(site=ad, baslik="Ryzen 7 9800X3D Fan", sepet_fiyati=10.0, url=f"https://{ad}.com/f")]
            return [_kart(site=ad, baslik="Inno3D RTX 5080 X3", sepet_fiyati=fiyat, url=f"https://{ad}.com/g"),
                    _kart(site=ad, baslik="RTX 5080 Laptop", sepet_fiyati=150000.0, url=f"https://{ad}.com/l")]
        return SahteSite(sonuc)

    def kur(alt):
        vt = VeriTabani(Ayar(veri_dizini=tmp_path / alt).db_yolu)
        vt.urun_ekle(UrunKimligi(id="gpu", kanonik_ad="RTX 5080", eslesme_kurallari={
            "sorgular": ["rtx 5080"], "dahil": ["5080"], "haric": ["laptop"], "min_fiyat": 90000, "max_fiyat": 260000}))
        vt.urun_ekle(UrunKimligi(id="cpu", kanonik_ad="9800X3D", eslesme_kurallari={
            "sorgular": ["ryzen 9800x3d"], "dahil": ["9800x3d"], "haric": ["fan"], "min_fiyat": 15000, "max_fiyat": 40000}))
        return vt

    siteler = {ad: _site(ad, 100000.0 + i * 1000) for i, ad in enumerate(["n11", "vatan", "itopya", "amazon", "pttavm"])}
    monkeypatch.setattr(tara_mod, "SITELER", siteler)

    v1 = kur("sirali")
    s1 = tara_mod.tara(v1, SahteTarayici(), urun_idler=["gpu", "cpu"])
    v2 = kur("paralel")
    s2 = tara_mod.tara(v2, SahteTarayici(), urun_idler=["gpu", "cpu"], sekme_sayisi=3,
                       tarayici_fabrika=lambda no: SahteTarayici())
    ozet = lambda s: {u: sorted((t.site, t.sepet_fiyati) for t in lst) for u, lst in s.teklifler.items()}
    assert ozet(s1) == ozet(s2) and len(s2.teklifler["gpu"]) == 5 and len(s2.teklifler["cpu"]) == 5
    assert s2.elenen_sayisi == {"gpu": 5, "cpu": 5}
    assert all("Laptop" in o["baslik"] for o in s2.elenen_ornekleri["gpu"])
    assert all("Fan" in o["baslik"] for o in s2.elenen_ornekleri["cpu"])
    assert set(s2.kapsam) == set(s1.kapsam) and ihlal == [] and len(iscler) >= 2
    v1.kapat(); v2.kapat()


def test_ek_ciftler_yalniz_istenen_urun_site_islerini_ekler(v: VeriTabani, monkeypatch: pytest.MonkeyPatch):
    """15.11 Tarayıcı turu: siteler=[trendyol] + HTTP'de başarısız (gpu_rtx5080, n11) → n11 yalnız o üründe taranır."""
    siteler = {
        "n11": SahteSite([_kart(site="n11", sepet_fiyati=118000.0)]),
        "trendyol": SahteSite([]),
    }
    monkeypatch.setattr(tara_mod, "SITELER", siteler)
    sonuc = tara_mod.tara(v, SahteTarayici(), urun_idler=["gpu_rtx5080"], siteler=["trendyol"],
                          ek_ciftler={("gpu_rtx5080", "n11"), ("baska_urun", "n11")})
    assert set(sonuc.kapsam) == {("gpu_rtx5080", "trendyol"), ("gpu_rtx5080", "n11")}
    bos = tara_mod.tara(v, SahteTarayici(), urun_idler=["gpu_rtx5080"], siteler=[])
    assert bos.kapsam == {}                                              # boş liste = hiçbir site (hepsi değil)
