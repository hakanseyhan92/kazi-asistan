"""9 öncelikli site bağdaştırıcısı için çevrimdışı (ağsız) taksit ayrıştırma testleri."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from kazi.modeller import Taksit
from kazi.taksit import (
    amazon,
    hepsiburada,
    incehesap,
    itopya,
    mediamarkt,
    n11,
    teknosa,
    trendyol,
    vatan,
)
from kazi.tarayici import BotEngeli

VERI_KLASORU = Path(__file__).parent / "veri_taksit"


class SahteTarayici:
    def __init__(
        self,
        *,
        http_yanitlar: dict[str, tuple[int, str] | Exception] | None = None,
        brave_sonuc: tuple[list[dict], str] | Exception | None = None,
        yalniz_k0: bool = False,
    ) -> None:
        self.http_yanitlar = http_yanitlar or {}
        self.brave_sonuc = brave_sonuc
        self.yalniz_k0 = yalniz_k0
        self.http_cagrilari: list[str] = []
        self.brave_cagrilari: list[str] = []

    def http_getir(self, url: str, site: str = "", zaman_asimi: float = 40.0) -> tuple[int, str]:
        self.http_cagrilari.append(url)
        for desen, deger in self.http_yanitlar.items():
            if desen in url:
                if isinstance(deger, Exception):
                    raise deger
                return deger
        return 404, ""

    def brave_ag_ve_govde(
        self,
        url: str,
        desen: str,
        *,
        site: str | None = None,
        azami_bekle: float = 60.0,
        hazir_desen: str | None = None,
        kaydir: bool = True,
        sayfa: object = None,
    ) -> tuple[list[dict], str]:
        self.brave_cagrilari.append(url)
        if isinstance(self.brave_sonuc, Exception):
            raise self.brave_sonuc
        if self.brave_sonuc is None:
            return [], ""
        return self.brave_sonuc


def test_n11_gercek_json_ve_api_cagrisi() -> None:
    veri = json.loads((VERI_KLASORU / "n11_taksit.json").read_text(encoding="utf-8"))
    satirlar = n11.ayristir(taksit_json=veri, sepet_fiyati=122999.0, teklif_id=7)

    bankalar = {s.banka for s in satirlar}
    assert {"Bonus", "World", "Maximum", "Axess", "CardFinans", "Paraf", "Bankkart", "Advantage", "Kuveyt Türk"} <= bankalar

    axess_3 = next(s for s in satirlar if s.banka == "Axess" and s.taksit_sayisi == 3)
    assert axess_3.aylik == pytest.approx(40999.67)
    assert axess_3.toplam == pytest.approx(122999.0)
    assert axess_3.vade_farki_yuzde == 0.0

    axess_4 = next(s for s in satirlar if s.banka == "Axess" and s.taksit_sayisi == 4)
    assert axess_4.aylik == pytest.approx(33535.33)
    assert axess_4.toplam == pytest.approx(134141.31)
    assert axess_4.vade_farki_yuzde == pytest.approx(9.06)

    tarayici = SahteTarayici(
        http_yanitlar={"getProductInstallmentDetail?productId=809759120": (200, json.dumps(veri))}
    )
    cekilen = n11.taksit_tablosu(
        tarayici,
        "https://www.n11.com/urun/test-rtx-5080-p-809759120",
        122999.0,
        teklif_id=7,
    )
    assert len(cekilen) == len(satirlar)
    assert any("productId=809759120" in u for u in tarayici.http_cagrilari)


def test_n11_html_yedek_rozet() -> None:
    html = (VERI_KLASORU / "n11.html").read_text(encoding="utf-8")
    satirlar = n11.ayristir(html=html, sepet_fiyati=90000.0)
    assert len(satirlar) >= 1
    assert all(isinstance(s, Taksit) for s in satirlar)


def test_vatan_gercek_html() -> None:
    html = (VERI_KLASORU / "vatan.html").read_text(encoding="utf-8")
    satirlar = vatan.ayristir(html, sepet_fiyati=129279.0)

    bankalar = {s.banka for s in satirlar}
    assert {"QNB", "Axess", "Maximum", "Bonus", "World", "Bankkart", "Paraf", "Kuveyt Türk"} == bankalar

    axess_3 = next(s for s in satirlar if s.banka == "Axess" and s.taksit_sayisi == 3)
    assert axess_3.aylik == pytest.approx(43093.0)
    assert axess_3.toplam == pytest.approx(129279.0)
    assert axess_3.vade_farki_yuzde == 0.0

    axess_12 = next(s for s in satirlar if s.banka == "Axess" and s.taksit_sayisi == 12)
    assert axess_12.aylik == pytest.approx(14064.48)
    assert axess_12.toplam == pytest.approx(168773.73)
    assert axess_12.vade_farki_yuzde == pytest.approx(30.55)


def test_itopya_gercek_html_ve_uc_nokta() -> None:
    taksit_html = (VERI_KLASORU / "itopya_taksit.html").read_text(encoding="utf-8")
    satirlar = itopya.ayristir(taksit_html=taksit_html, sepet_fiyati=133797.0)

    bankalar = {s.banka for s in satirlar}
    assert {"Paraf", "Bonus", "Axess", "Bankkart", "World", "CardFinans", "Maximum", "Türkiye Finans"} == bankalar

    # Modal + inline kısmi görünüm yüzünden çift kayıt üretmemeli
    axess_satirlari = [s for s in satirlar if s.banka == "Axess" and s.taksit_sayisi == 5]
    assert len(axess_satirlari) == 1
    assert axess_satirlari[0].toplam == pytest.approx(133797.0)
    assert axess_satirlari[0].vade_farki_yuzde == 0.0

    axess_9 = next(s for s in satirlar if s.banka == "Axess" and s.taksit_sayisi == 9)
    assert axess_9.aylik == pytest.approx(18353.5)
    assert axess_9.toplam == pytest.approx(165181.48)
    assert axess_9.vade_farki_yuzde == pytest.approx(23.46)

    tarayici = SahteTarayici(
        http_yanitlar={"Home/GetTaksitBilgileri?id=35877": (200, taksit_html)}
    )
    cekilen = itopya.taksit_tablosu(
        tarayici,
        "https://www.itopya.com/ornek-rtx-5080_u35877",
        133797.0,
    )
    assert len(cekilen) == len(satirlar)


def test_amazon_gercek_html() -> None:
    html = (VERI_KLASORU / "amazon.html").read_text(encoding="utf-8")
    satirlar = amazon.ayristir(html, sepet_fiyati=133980.73)

    bankalar = {s.banka for s in satirlar}
    assert {
        "Bonus",
        "Maximum",
        "Axess",
        "Bankkart",
        "Paraf",
        "CardFinans",
        "World",
        "Advantage",
        "Sağlam Kart",
    } == bankalar

    bonus_3 = next(s for s in satirlar if s.banka == "Bonus" and s.taksit_sayisi == 3)
    assert bonus_3.aylik == pytest.approx(44660.24)
    assert bonus_3.toplam == pytest.approx(133980.73)
    assert bonus_3.vade_farki_yuzde == 0.0

    adv_12 = next(s for s in satirlar if s.banka == "Advantage" and s.taksit_sayisi == 12)
    assert adv_12.aylik == pytest.approx(13398.07)
    assert adv_12.toplam == pytest.approx(160776.88)
    assert adv_12.vade_farki_yuzde == pytest.approx(20.0)


def test_trendyol_gercek_html_ve_ag_json() -> None:
    html = (VERI_KLASORU / "trendyol.html").read_text(encoding="utf-8")
    satirlar = trendyol.ayristir(html, sepet_fiyati=89999.0)
    assert len(satirlar) == 1
    assert satirlar[0].banka is None
    assert satirlar[0].taksit_sayisi == 9
    assert satirlar[0].toplam == pytest.approx(89999.0)
    assert satirlar[0].vade_farki_yuzde == 0.0

    ag_json = [
        {
            "url": "https://apigw.trendyol.com/installment",
            "durum": 200,
            "json": {
                "installments": [
                    {
                        "bankName": "Maximum",
                        "options": [
                            {"months": 3, "monthlyAmount": 30000.0, "totalAmount": 90000.0, "interestFree": True},
                            {"months": 6, "monthlyAmount": 16500.0, "totalAmount": 99000.0},
                        ],
                    }
                ]
            },
        }
    ]
    ag_satirlar = trendyol.ayristir("", sepet_fiyati=90000.0, yanitlar=ag_json)
    assert len(ag_satirlar) == 2
    assert ag_satirlar[0].banka == "Maximum"
    assert ag_satirlar[1].vade_farki_yuzde == pytest.approx(10.0)


def test_hepsiburada_gercek_html_ve_k2_yedek() -> None:
    html = (VERI_KLASORU / "hepsiburada.html").read_text(encoding="utf-8")
    satirlar = hepsiburada.ayristir(html, sepet_fiyati=250893.60)
    assert len(satirlar) == 1
    assert satirlar[0].banka is None
    assert satirlar[0].taksit_sayisi == 7
    assert satirlar[0].vade_farki_yuzde == 0.0

    k2_json = [
        {
            "url": "https://payment.hepsiburada.com/installments",
            "durum": 200,
            "json": {
                "cards": [
                    {
                        "cardFamilyName": "World",
                        "installments": [
                            {"installmentCount": 6, "monthlyPayment": 15000.0, "totalPayment": 90000.0}
                        ],
                    }
                ]
            },
        }
    ]
    tarayici = SahteTarayici(
        http_yanitlar={"hepsiburada.com": HTTPError("https://www.hepsiburada.com/p", 403, "Forbidden", {}, None)},
        brave_sonuc=(k2_json, "<html></html>"),
    )
    cekilen = hepsiburada.taksit_tablosu(tarayici, "https://www.hepsiburada.com/urun-p-123", 80000.0)
    assert len(cekilen) == 1
    assert cekilen[0].banka == "World"
    assert cekilen[0].vade_farki_yuzde == pytest.approx(12.5)
    assert len(tarayici.brave_cagrilari) == 1


def test_mediamarkt_gercek_html() -> None:
    html = (VERI_KLASORU / "mediamarkt.html").read_text(encoding="utf-8")
    satirlar = mediamarkt.ayristir(html, sepet_fiyati=39060.0)
    assert len(satirlar) == 1
    assert satirlar[0].banka is None
    assert satirlar[0].taksit_sayisi == 4
    assert satirlar[0].aylik == pytest.approx(9765.0)
    assert satirlar[0].toplam == pytest.approx(39060.01)
    assert satirlar[0].vade_farki_yuzde == 0.0


def test_incehesap_tablo_ve_bot_engeli() -> None:
    ornek_html = """
    <div class="taksit-kart">
      <img alt="Bonus" src="/img/bonus.png"/>
      <table>
        <thead><tr><th>Taksit</th><th>Aylık</th><th>Toplam</th></tr></thead>
        <tbody>
          <tr><td>3 Taksit</td><td>33.333,33 TL</td><td>100.000,00 TL</td></tr>
          <tr><td>6 Taksit</td><td>18.333,33 TL</td><td>110.000,00 TL</td></tr>
        </tbody>
      </table>
    </div>
    """
    satirlar = incehesap.ayristir(ornek_html, sepet_fiyati=100000.0)
    assert len(satirlar) == 2
    assert satirlar[0].banka == "Bonus"
    assert satirlar[0].vade_farki_yuzde == 0.0
    assert satirlar[1].vade_farki_yuzde == pytest.approx(10.0)

    k0_engelli = SahteTarayici(
        http_yanitlar={"incehesap.com": (403, "Just a moment... Cloudflare")},
        yalniz_k0=True,
    )
    with pytest.raises(BotEngeli):
        incehesap.taksit_tablosu(k0_engelli, "https://www.incehesap.com/ornek-rtx5080/", 100000.0)


def test_teknosa_tablo_ve_pesin_yedek() -> None:
    ornek_html = """
    <section>
      <h3>Worldcard Taksit Seçenekleri</h3>
      <table>
        <thead><tr><th>Taksit Sayısı</th><th>Aylık Ödeme</th><th>Toplam Tutar</th></tr></thead>
        <tbody>
          <tr><td>4</td><td>25.000,00 TL</td><td>100.000,00 TL</td></tr>
          <tr><td>9</td><td>13.000,00 TL</td><td>117.000,00 TL</td></tr>
        </tbody>
      </table>
    </section>
    """
    satirlar = teknosa.ayristir(ornek_html, sepet_fiyati=100000.0)
    assert len(satirlar) == 2
    assert satirlar[0].banka == "World"
    assert satirlar[0].vade_farki_yuzde == 0.0
    assert satirlar[1].vade_farki_yuzde == pytest.approx(17.0)

    pesin = teknosa.ayristir("<p>Peşin fiyatına 6 taksit imkanı</p>", sepet_fiyati=60000.0)
    assert len(pesin) == 1
    assert pesin[0].banka is None
    assert pesin[0].taksit_sayisi == 6
    assert pesin[0].vade_farki_yuzde == 0.0
