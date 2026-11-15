"""Teyit motoru ve kovalama (`kazi.tarama.teyit`) birim testleri — AĞ YOK."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kazi import pano
from kazi.ayar import Ayar
from kazi.db import VeriTabani
from kazi.modeller import TZ_ISTANBUL, Katman, Teklif, TeyitDurumu, UrunKimligi
from kazi.siteler._kart import Kart
from kazi.tarama import depo, teyit
from kazi.tarayici import BotEngeli

GERCEK_URL = "https://www.n11.com/urun/inno3d-rtx-5080-x3-p-101"


class SahteSayfa:
    def __init__(self, icerik: bytes = b"\x89PNG\r\n\x1a\nsahte-png"):
        self.icerik = icerik
        self.goruntuler: list[str] = []

    def screenshot(self, path: str | None = None) -> bytes:
        if path:
            self.goruntuler.append(str(path))
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(self.icerik)
        return self.icerik


class SahteTarayici:
    def __init__(self, sayfa: SahteSayfa | None = None):
        self.page = sayfa
        self.birak_sayisi = 0

    def brave_birak(self) -> None:
        self.birak_sayisi += 1


@pytest.fixture
def v(tmp_path: Path):
    vt = VeriTabani(Ayar(veri_dizini=tmp_path).db_yolu)
    vt.urun_ekle(
        UrunKimligi(
            id="gpu_rtx5080",
            kanonik_ad="Inno3D RTX 5080 X3",
            eslesme_kurallari={
                "kategori": "gpu",
                "sistemde": True,
                "sorgular": ["rtx 5080"],
                "dahil": ["5080"],
                "haric": [r"laptop", r"notebook", r"5080\s*ti"],
                "min_fiyat": 90000,
                "max_fiyat": 260000,
            },
        )
    )
    vt.baglanti.execute(
        "INSERT INTO izleme (urun_id, hedef_fiyat, etkin) VALUES ('gpu_rtx5080', 110000.0, 1)"
    )
    vt.baglanti.commit()
    depo.kur(vt)
    yield vt
    vt.kapat()


def _teklif_ekle(
    v: VeriTabani,
    *,
    site: str = "n11",
    satici: str = "GarajOnline",
    fiyat: float = 108000.0,
    url: str = "https://www.akakce.com/r/?pr=101",
    katman: Katman = Katman.K0,
    durum: TeyitDurumu = TeyitDurumu.bekliyor,
) -> Teklif:
    tk = Teklif(
        urun_id="gpu_rtx5080",
        site=site,
        satici=satici,
        baslik="Inno3D RTX 5080 X3 16GB GDDR7",
        url=url,
        sepet_fiyati=fiyat,
        stok=True,
        kaynak_katmani=katman,
        teyit_durumu=durum,
    )
    tk.id = depo.teklif_ekle(v, tk)
    return tk


def _okunan_kart(
    *,
    fiyat: float = 108000.0,
    baslik: str = "Inno3D RTX 5080 X3 16GB GDDR7",
    stok: bool | None = True,
    satici: str = "GarajOnline",
    taksit: int | None = 9,
    yontem: str = "http",
) -> Kart:
    return Kart(
        site="n11",
        baslik=baslik,
        url=GERCEK_URL,
        sepet_fiyati=fiyat,
        stok=stok,
        satici=satici,
        pesin_taksit=taksit,
        yontem=yontem,
    )


def test_depo_goc_idempotent(v: VeriTabani):
    # İki kez çalıştırıldığında hata vermez ve teyit_fiyati sütunu mevcuttur
    depo.goc(v.baglanti)
    depo.goc(v.baglanti)
    sutunlar = {r["name"] for r in v.baglanti.execute("PRAGMA table_info(teklif)")}
    assert "teyit_fiyati" in sutunlar
    assert "teyit_url" in sutunlar


def test_teyit_fiyat_yuzde_bir_ici_ve_disi(v: VeriTabani):
    urun = depo.urun_getir(v, "gpu_rtx5080")

    # 1) ±%1 içinde (%0,5 fark: 100.000 -> 100.500) -> teyitli
    tk_ic = _teklif_ekle(v, fiyat=100000.0, url="https://www.akakce.com/r/?pr=1")
    bekle_cagrilari: list[float] = []
    durum_ic = teyit.teyit_et(
        tk_ic,
        urun,
        SahteTarayici(),
        v=v,
        bekle_fn=bekle_cagrilari.append,
        cozucu=lambda _t, _u: GERCEK_URL,
        bagdastirici=lambda _t, _u: _okunan_kart(fiyat=100500.0),
    )
    assert durum_ic == TeyitDurumu.teyitli
    assert bekle_cagrilari == [40.0]
    assert tk_ic.teyit_zamani is not None
    assert getattr(tk_ic, "teyit_fiyati", None) == 100500.0

    # 2) ±%1 dışında (%5 fark: 100.000 -> 105.000) -> fiyat_degisti
    tk_dis = _teklif_ekle(v, fiyat=100000.0, url="https://www.akakce.com/r/?pr=2")
    durum_dis = teyit.teyit_et(
        tk_dis,
        urun,
        SahteTarayici(),
        v=v,
        bekle_fn=lambda _s: None,
        cozucu=lambda _t, _u: GERCEK_URL,
        bagdastirici=lambda _t, _u: _okunan_kart(fiyat=105000.0),
    )
    assert durum_dis == TeyitDurumu.fiyat_degisti


def test_teyit_baslik_farkli_stok_yok_ve_supheli(v: VeriTabani):
    urun = depo.urun_getir(v, "gpu_rtx5080")

    # Başlık farklı (5070 Ti) -> urun_farkli
    tk1 = _teklif_ekle(v, url="https://www.akakce.com/r/?pr=10")
    d1 = teyit.teyit_et(
        tk1,
        urun,
        SahteTarayici(),
        v=v,
        bekle_fn=lambda _s: None,
        cozucu=lambda _t, _u: GERCEK_URL,
        bagdastirici=lambda _t, _u: _okunan_kart(baslik="MSI RTX 5070 Ti 16GB"),
    )
    assert d1 == TeyitDurumu.urun_farkli

    # Stok yok -> stok_yok
    tk2 = _teklif_ekle(v, url="https://www.akakce.com/r/?pr=11")
    d2 = teyit.teyit_et(
        tk2,
        urun,
        SahteTarayici(),
        v=v,
        bekle_fn=lambda _s: None,
        cozucu=lambda _t, _u: GERCEK_URL,
        bagdastirici=lambda _t, _u: _okunan_kart(stok=False),
    )
    assert d2 == TeyitDurumu.stok_yok

    # Sayfa açılamadı (BotEngeli) -> supheli
    tk3 = _teklif_ekle(v, url="https://www.akakce.com/r/?pr=12")

    def _patla(_t, u):
        raise BotEngeli("n11", u)

    d3 = teyit.teyit_et(
        tk3,
        urun,
        SahteTarayici(),
        v=v,
        bekle_fn=lambda _s: None,
        cozucu=lambda _t, _u: GERCEK_URL,
        bagdastirici=_patla,
    )
    assert d3 == TeyitDurumu.supheli


def test_ikinci_okuma_40sn_sonra_tutarsiz_ise_fiyat_degisti(v: VeriTabani):
    urun = depo.urun_getir(v, "gpu_rtx5080")
    tk = _teklif_ekle(v, fiyat=108000.0, url="https://www.akakce.com/r/?pr=20")

    okumalar = iter(
        [
            _okunan_kart(fiyat=108000.0),  # 1. okuma: eşleşiyor
            _okunan_kart(fiyat=119000.0),  # 40 sn sonra 2. okuma: satıcı fiyatı değiştirdi
        ]
    )
    beklemeler: list[float] = []
    durum = teyit.teyit_et(
        tk,
        urun,
        SahteTarayici(),
        v=v,
        bekle_fn=beklemeler.append,
        cozucu=lambda _t, _u: GERCEK_URL,
        bagdastirici=lambda _t, _u: next(okumalar),
    )
    assert durum == TeyitDurumu.fiyat_degisti
    assert beklemeler == [40.0]
    assert getattr(tk, "teyit_fiyati", None) == 119000.0


def test_iki_saat_onbellegi_sayfayi_yeniden_acmaz(v: VeriTabani):
    urun = depo.urun_getir(v, "gpu_rtx5080")
    simdi = datetime(2026, 11, 14, 15, 0, tzinfo=TZ_ISTANBUL)
    url = "https://www.akakce.com/r/?pr=30"

    tk1 = _teklif_ekle(v, fiyat=108000.0, url=url)
    cagri_sayisi = 0

    def _oku(_t, _u):
        nonlocal cagri_sayisi
        cagri_sayisi += 1
        return _okunan_kart(fiyat=108000.0)

    d1 = teyit.teyit_et(
        tk1,
        urun,
        SahteTarayici(),
        v=v,
        bekle_fn=lambda _s: None,
        cozucu=lambda _t, _u: GERCEK_URL,
        bagdastirici=_oku,
        simdi=simdi,
    )
    assert d1 == TeyitDurumu.teyitli
    assert cagri_sayisi == 2  # ilk okuma + 40sn ikinci okuma

    # 30 dakika sonra aynı URL ve aynı fiyatla yeni gözlem -> önbellekten döner, sayfa açılmaz
    tk2 = _teklif_ekle(v, fiyat=108000.0, url=url)

    def _yasak(*_a):
        raise AssertionError("2 saatlik önbellek varken sayfa yeniden açılmamalı")

    d2 = teyit.teyit_et(
        tk2,
        urun,
        SahteTarayici(),
        v=v,
        bekle_fn=lambda _s: None,
        cozucu=_yasak,
        bagdastirici=_yasak,
        simdi=simdi + timedelta(minutes=30),
    )
    assert d2 == TeyitDurumu.teyitli
    assert getattr(tk2, "teyit_fiyati", None) == 108000.0

    # 3 saat sonra önbellek süresi dolmuştur -> yeniden okunur
    tk3 = _teklif_ekle(v, fiyat=108000.0, url=url)
    d3 = teyit.teyit_et(
        tk3,
        urun,
        SahteTarayici(),
        v=v,
        bekle_fn=lambda _s: None,
        cozucu=lambda _t, _u: GERCEK_URL,
        bagdastirici=_oku,
        simdi=simdi + timedelta(hours=3),
    )
    assert d3 == TeyitDurumu.teyitli
    assert cagri_sayisi == 4


def test_kanit_goruntusu_k1_k2_yazilir_http_null_kalir(v: VeriTabani, tmp_path: Path):
    urun = depo.urun_getir(v, "gpu_rtx5080")
    kanit_dir = tmp_path / "kanit"

    # 1) K2 (Brave) ile teyit -> page.screenshot çağrılır ve <teklif_id>.png yazılır
    tk_k2 = _teklif_ekle(
        v,
        site="trendyol",
        fiyat=108000.0,
        url="https://www.trendyol.com/inno3d/rtx-5080-p-1",
        katman=Katman.K2,
    )
    sayfa = SahteSayfa()
    tarayici_k2 = SahteTarayici(sayfa=sayfa)
    d_k2 = teyit.teyit_et(
        tk_k2,
        urun,
        tarayici_k2,
        v=v,
        bekle_fn=lambda _s: None,
        cozucu=lambda _t, u: u,
        bagdastirici=lambda _t, _u: _okunan_kart(fiyat=108000.0, yontem="brave"),
        kanit_dizini=kanit_dir,
    )
    assert d_k2 == TeyitDurumu.teyitli
    beklenen_png = kanit_dir / f"{tk_k2.id}.png"
    assert tk_k2.kanit_goruntusu == str(beklenen_png)
    assert beklenen_png.is_file()
    assert len(sayfa.goruntuler) >= 1

    # 2) HTTP (K0) ile teyit -> kanıt görüntüsü yok (None)
    tk_http = _teklif_ekle(
        v,
        site="n11",
        fiyat=108500.0,
        url="https://www.n11.com/urun/inno3d-5080-p-2",
        katman=Katman.K0,
    )
    d_http = teyit.teyit_et(
        tk_http,
        urun,
        SahteTarayici(sayfa=None),
        v=v,
        bekle_fn=lambda _s: None,
        cozucu=lambda _t, u: u,
        bagdastirici=lambda _t, _u: _okunan_kart(fiyat=108500.0, yontem="http"),
        kanit_dizini=kanit_dir,
    )
    assert d_http == TeyitDurumu.teyitli
    assert tk_http.kanit_goruntusu is None


def test_kovala_ve_hedef_yakalandi_bildirimi(v: VeriTabani, tmp_path: Path):
    # Piyasa ortalamasını ~122.000 TL yapacak teklifler + 108.499 TL hedef altı teklif
    _teklif_ekle(v, site="vatan", satici="Vatan", fiyat=124000.0, url="https://vatan.com/1")
    _teklif_ekle(v, site="itopya", satici="Itopya", fiyat=122000.0, url="https://itopya.com/2")
    _teklif_ekle(v, site="sinerji", satici="Sinerji", fiyat=120000.0, url="https://sinerji.com/3")
    ucuz = _teklif_ekle(
        v,
        site="trendyol",
        satici="GarajOnline",
        fiyat=108499.0,
        url="https://www.trendyol.com/inno3d-5080-p-99",
        katman=Katman.K2,
    )

    bildirimler: list[dict] = []
    sayfa = SahteSayfa()
    sonuclar = teyit.kovala(
        v,
        SahteTarayici(sayfa=sayfa),
        "gpu_rtx5080",
        azami=5,
        bildirim_fn=bildirimler.append,
        bekle_fn=lambda _s: None,
        cozucu=lambda _t, u: u,
        bagdastirici=lambda _t, _u: _okunan_kart(
            fiyat=108499.0, satici="GarajOnline", taksit=9, yontem="brave"
        ),
        kanit_dizini=tmp_path / "kanit",
    )

    assert len(sonuclar) == 1
    assert sonuclar[0].id == ucuz.id
    assert sonuclar[0].teyit_durumu == TeyitDurumu.teyitli
    assert sonuclar[0].hedef_altinda is True

    # hedef_yakalandi bildirimi yayınlandı
    assert len(bildirimler) == 1
    assert bildirimler[0]["tur"] == "hedef_yakalandi"
    assert bildirimler[0]["urun_id"] == "gpu_rtx5080"
    assert bildirimler[0]["fiyat"] == 108499.0
    assert bildirimler[0]["hedef_fiyat"] == 110000.0

    # Pano: en iyi teklif teyitli ve hedefin altında işaretli
    p = pano.pano(v)
    gpu_pano = next(u for u in p["urunler"] if u["id"] == "gpu_rtx5080")
    assert gpu_pano["en_iyi"]["hedef_altinda"] is True
    assert gpu_pano["en_iyi"]["taksit"]["pesin_fiyatina"] == 9


def test_yonlendirme_coz_toplayici(monkeypatch: pytest.MonkeyPatch):
    # 1) HTTP 30x Location başlığı ile çözüm
    monkeypatch.setattr(
        teyit,
        "_http_hedef",
        lambda u: GERCEK_URL if "akakce.com" in u else None,
    )
    assert teyit.yonlendirme_coz(object(), "https://www.akakce.com/r/?pr=55") == GERCEK_URL

    # 2) Model sayfasından en ucuz satıcı teklifi ile çözüm
    monkeypatch.setattr(teyit, "_http_hedef", lambda _u: None)
    monkeypatch.setattr(teyit, "_model_satıcı_url", lambda _t, _u: GERCEK_URL)
    assert teyit.yonlendirme_coz(object(), "https://www.epey.com/ekran-karti/rtx5080.html") == GERCEK_URL

    # 3) Çözülemezse RuntimeError
    monkeypatch.setattr(teyit, "_model_satıcı_url", lambda _t, _u: None)
    monkeypatch.setattr(teyit, "_brave_son_url", lambda _t, u: (u, ""))
    with pytest.raises(RuntimeError):
        teyit.yonlendirme_coz(object(), "https://www.akakce.com/r/?pr=cozulemez")



def test_meta_yenileme_hedefi_akakce():
    html = '<head><meta http-equiv="refresh" content="1; url=https://www.hepsiburada.com/x-p-HBC1?magaza=Beray&amp;a=1"></head>'
    assert teyit.meta_yenileme_hedefi(html, "https://www.akakce.com/r/?pr=1") == "https://www.hepsiburada.com/x-p-HBC1?magaza=Beray&a=1"
    assert teyit.meta_yenileme_hedefi("<html></html>", "https://a") is None
