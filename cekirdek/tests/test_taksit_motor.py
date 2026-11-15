"""Taksit çekirdek motoru, vade farkı, DB kayıt, yönlendirme, engel ve teyit entegrasyonu testleri."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from kazi.db import VeriTabani
from kazi.modeller import TZ_ISTANBUL, Katman, Taksit, Teklif, TeyitDurumu, UrunKimligi
from kazi.taksit import (
    kaydet,
    site_sec,
    tablo_getir,
    urun_taksitlerini_cek,
)
from kazi.taksit._ortak import vade_farki_yuzde_hesapla
from kazi.tarama import depo, teyit

VERI_KLASORU = Path(__file__).parent / "veri_taksit"


def _db_hazirla(tmp_path: Path) -> tuple[VeriTabani, int, UrunKimligi]:
    v = VeriTabani(tmp_path / "test.db")
    urun = UrunKimligi(
        id="gpu_rtx5080",
        kanonik_ad="RTX 5080",
        eslesme_kurallari={
            "kategori": "gpu",
            "sistemde": True,
            "sorgular": ["rtx 5080"],
            "dahil": ["5080"],
            "haric": [r"laptop", r"notebook", r"5080\s*ti"],
            "min_fiyat": 60000,
            "max_fiyat": 260000,
        },
    )
    v.urun_ekle(urun)
    depo.kur(v)
    teklif_id = depo.teklif_ekle(
        v,
        Teklif(
            urun_id="gpu_rtx5080",
            site="n11",
            satici="TestSatıcı",
            url="https://www.n11.com/urun/test-rtx-5080-p-809759120",
            baslik="Test RTX 5080 16GB",
            sepet_fiyati=122999.0,
            stok=True,
            kaynak_katmani=Katman.K0,
        ),
    )
    return v, teklif_id, urun


def test_vade_farki_hesaplama() -> None:
    assert vade_farki_yuzde_hesapla(100000.0, 100000.0) == 0.0
    assert vade_farki_yuzde_hesapla(100000.01, 100000.0) == 0.0
    assert vade_farki_yuzde_hesapla(112500.0, 100000.0) == pytest.approx(12.5)
    assert vade_farki_yuzde_hesapla(109060.0, 100000.0) == pytest.approx(9.06)
    assert vade_farki_yuzde_hesapla(None, 100000.0, pesin_mi=True) == 0.0


@pytest.mark.parametrize(
    ("url", "beklenen_site"),
    [
        ("https://www.hepsiburada.com/x-p-123", "hepsiburada"),
        ("https://www.trendyol.com/x/y-p-123", "trendyol"),
        ("https://www.n11.com/urun/x-p-123", "n11"),
        ("https://www.amazon.com.tr/dp/B000", "amazon"),
        ("https://www.vatanbilgisayar.com/x.html", "vatan"),
        ("https://www.itopya.com/x_u123", "itopya"),
        ("https://www.incehesap.com/x-fiyati-123/", "incehesap"),
        ("https://www.teknosa.com/x-p-123", "teknosa"),
        ("https://www.mediamarkt.com.tr/tr/product/_x-123.html", "mediamarkt"),
        ("https://www.bilinmeyenmagaza.com/urun/1", None),
    ],
)
def test_site_secimi(url: str, beklenen_site: str | None) -> None:
    site, modul = site_sec(url)
    assert site == beklenen_site
    if beklenen_site is None:
        assert modul is None
    else:
        assert modul is not None
        assert callable(modul.taksit_tablosu)


def test_kaydet_eski_banka_siler_pesin_satiri_korur(tmp_path: Path) -> None:
    v, teklif_id, _ = _db_hazirla(tmp_path)
    try:
        v.taksit_ekle(Taksit(teklif_id=teklif_id, banka=None, taksit_sayisi=6, vade_farki_yuzde=0.0))
        v.taksit_ekle(
            Taksit(
                teklif_id=teklif_id,
                banka="Bonus",
                taksit_sayisi=3,
                aylik=10000.0,
                toplam=30000.0,
                vade_farki_yuzde=5.0,
            )
        )

        yeni_satirlar = [
            Taksit(
                teklif_id=teklif_id,
                banka="World",
                taksit_sayisi=4,
                aylik=30749.75,
                toplam=122999.0,
                vade_farki_yuzde=0.0,
            ),
            Taksit(
                teklif_id=teklif_id,
                banka="Axess",
                taksit_sayisi=6,
                aylik=23000.0,
                toplam=138000.0,
                vade_farki_yuzde=12.2,
            ),
        ]
        kaydet(v, teklif_id, yeni_satirlar)

        kayitlilar = v.taksitler(teklif_id)
        banka_yok = [x for x in kayitlilar if x.banka is None]
        bankali = [x for x in kayitlilar if x.banka is not None]

        assert len(banka_yok) == 1
        assert banka_yok[0].taksit_sayisi == 6
        assert {(x.banka, x.taksit_sayisi) for x in bankali} == {("World", 4), ("Axess", 6)}
    finally:
        v.kapat()


def test_tablo_getir_bilinmeyen_site_ve_bot_engeli_nedeni() -> None:
    bilinmeyen = Teklif(
        urun_id="gpu_rtx5080",
        site="bilinmeyen",
        url="https://www.bilinmeyen.example/rtx5080",
        baslik="RTX 5080",
        sepet_fiyati=80000.0,
        kaynak_katmani=Katman.K0,
    )
    assert tablo_getir(None, bilinmeyen) == []

    class EngelliTarayici:
        yalniz_k0 = True

        def http_getir(self, url: str, site: str = "", zaman_asimi: float = 40.0) -> tuple[int, str]:
            return 403, "cf-turnstile captcha"

    engelli_teklif = Teklif(
        urun_id="gpu_rtx5080",
        site="teknosa",
        url="https://www.teknosa.com/msi-rtx-5080-p-12345",
        baslik="RTX 5080",
        sepet_fiyati=80000.0,
        kaynak_katmani=Katman.K0,
    )
    nedenler: dict[str, str] = {}
    sonuc = tablo_getir(EngelliTarayici(), engelli_teklif, nedenler=nedenler)
    assert sonuc == []
    assert "teknosa" in nedenler
    assert "bot_engeli" in nedenler["teknosa"]


def test_toplayici_yonlendirme_cozme(monkeypatch: pytest.MonkeyPatch) -> None:
    vatan_html = (VERI_KLASORU / "vatan.html").read_text(encoding="utf-8")

    class YonlendirenTarayici:
        def http_getir(self, url: str, site: str = "", zaman_asimi: float = 40.0) -> tuple[int, str]:
            assert "vatanbilgisayar.com" in url
            return 200, vatan_html

    monkeypatch.setattr(
        "kazi.taksit.yonlendirme_coz",
        lambda t, u: "https://www.vatanbilgisayar.com/asus-rtx-5080.html",
    )
    toplayici_teklif = Teklif(
        urun_id="gpu_rtx5080",
        site="akakce",
        url="https://www.akakce.com/r/git/?u=123",
        baslik="RTX 5080",
        sepet_fiyati=129279.0,
        kaynak_katmani=Katman.K0,
    )
    satirlar = tablo_getir(YonlendirenTarayici(), toplayici_teklif)
    assert len(satirlar) > 10
    assert any(s.banka == "Bonus" for s in satirlar)


def test_12_saat_onbellek_ve_cli_cekimi(tmp_path: Path) -> None:
    v, teklif_id, _ = _db_hazirla(tmp_path)
    veri = json.loads((VERI_KLASORU / "n11_taksit.json").read_text(encoding="utf-8"))

    class SayacTarayici:
        def __init__(self) -> None:
            self.cagri = 0

        def http_getir(self, url: str, site: str = "", zaman_asimi: float = 40.0) -> tuple[int, str]:
            self.cagri += 1
            return 200, json.dumps(veri)

    tarayici = SayacTarayici()
    try:
        rapor1 = urun_taksitlerini_cek(v, tarayici, "gpu_rtx5080", azami=5)
        assert len(rapor1) == 1
        assert rapor1[0]["durum"] == "bulundu"
        ilk_cagri = tarayici.cagri
        assert ilk_cagri == 1
        assert len(v.taksitler(teklif_id)) == 40

        rapor2 = urun_taksitlerini_cek(v, tarayici, "gpu_rtx5080", azami=5)
        assert rapor2[0]["durum"] == "onbellek"
        assert tarayici.cagri == ilk_cagri
        assert len(rapor2[0]["satirlar"]) == 40

        eski_zaman = (datetime.now(TZ_ISTANBUL) - timedelta(hours=13)).isoformat()
        v.baglanti.execute("UPDATE taksit_onbellek SET son_zaman = ?", (eski_zaman,))
        v.baglanti.commit()
        rapor3 = urun_taksitlerini_cek(v, tarayici, "gpu_rtx5080", azami=5)
        assert rapor3[0]["durum"] == "bulundu"
        assert tarayici.cagri == ilk_cagri + 1
    finally:
        v.kapat()


def test_teyit_akisi_acik_sekmeden_taksit_kaydeder(tmp_path: Path) -> None:
    v = VeriTabani(tmp_path / "teyit_taksit.db")
    vatan_html = (VERI_KLASORU / "vatan.html").read_text(encoding="utf-8")
    urun = UrunKimligi(
        id="gpu_rtx5080",
        kanonik_ad="ASUS GeForce RTX 5080",
        eslesme_kurallari={
            "kategori": "gpu",
            "sistemde": True,
            "sorgular": ["rtx 5080"],
            "dahil": ["5080"],
            "haric": [r"laptop", r"notebook", r"5080\s*ti"],
            "min_fiyat": 60000,
            "max_fiyat": 260000,
        },
    )
    v.urun_ekle(urun)
    depo.kur(v)
    url = "https://www.vatanbilgisayar.com/ornek-rtx-5080.html"
    teklif = Teklif(
        urun_id="gpu_rtx5080",
        site="vatan",
        url=url,
        baslik="ASUS GeForce RTX 5080 16GB",
        sepet_fiyati=129279.0,
        stok=True,
        kaynak_katmani=Katman.K0,
    )
    teklif.id = depo.teklif_ekle(v, teklif)

    sayfa_govdesi = (
        f"<html><body><h1>ASUS GeForce RTX 5080 16GB</h1>"
        f"<div>129.279,00 TL</div><div>Sepete Ekle</div>{vatan_html}</body></html>"
    )

    class AcikSekme:
        def __init__(self) -> None:
            self.url = url

        def content(self) -> str:
            return sayfa_govdesi

    class SahteTeyitTarayici:
        def __init__(self) -> None:
            self._sekmeler = {"vatan": AcikSekme()}
            self.http_sayisi = 0

        def http_getir(self, u: str, site: str = "", zaman_asimi: float = 40.0) -> tuple[int, str]:
            self.http_sayisi += 1
            return 200, sayfa_govdesi

    tarayici = SahteTeyitTarayici()
    try:
        sonuc = teyit.teyit_et(
            teklif,
            urun,
            tarayici,
            v=v,
            bekle_sn=0,
            kanit_dizini=tmp_path,
            bagdastirici=lambda _t, _u: {
                "sepet_fiyati": 129279.0,
                "baslik": "ASUS GeForce RTX 5080 16GB",
                "stok": True,
                "satici": "Vatan Bilgisayar",
            },
        )
        assert sonuc == TeyitDurumu.teyitli
        kayitli = v.taksitler(int(teklif.id))
        assert any(s.banka == "Bonus" for s in kayitli)
        assert any(s.banka == "Maximum" for s in kayitli)
        assert tarayici.http_sayisi == 0
    finally:
        v.kapat()


def test_cli_ve_tl_bicimleme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from kazi.taksit.__main__ import main, tl_bicimle

    assert tl_bicimle(108499.0) == "108.499 TL"
    assert tl_bicimle(12999.5) == "12.999,50 TL"

    db_yolu = tmp_path / "cli.db"
    v = VeriTabani(db_yolu)
    v.urun_ekle(
        UrunKimligi(
            id="gpu_rtx5080",
            kanonik_ad="RTX 5080",
            eslesme_kurallari={"dahil": ["5080"]},
        )
    )
    depo.kur(v)
    depo.teklif_ekle(
        v,
        Teklif(
            urun_id="gpu_rtx5080",
            site="vatan",
            satici="Vatan",
            url="https://www.vatanbilgisayar.com/ornek-rtx-5080.html",
            baslik="RTX 5080",
            sepet_fiyati=129279.0,
            stok=True,
            kaynak_katmani=Katman.K0,
        ),
    )
    v.kapat()

    vatan_html = (VERI_KLASORU / "vatan.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        "kazi.tarayici.Tarayici.http_getir",
        lambda self, url, site="", zaman_asimi=40.0: (200, vatan_html),
    )

    kod = main(["--urun", "gpu_rtx5080", "--azami", "3", "--yalniz-k0", "--db", str(db_yolu)])
    cikti = capsys.readouterr().out
    assert kod == 0
    assert "vatan | Vatan | 129.279 TL | bulundu" in cikti

