"""Uçuş tarama orkestrası testleri (SARTNAME §13.5–13.6, AĞ YOK)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from kazi.seyahat.plan import ice_aktar_eski_plan
from kazi.seyahat.segment import Aktarma, Segment
from kazi.ucus import depo
from kazi.ucus import tara as tara_mod
from kazi.ucus.model import BotEngeli

TZ_TR = timezone(timedelta(hours=3))
TZ_JP = timezone(timedelta(hours=9))


def _seg(
    kaynak: str,
    toplam: float,
    firma: str = "MIAT",
    kalkis_nokta: str = "IST",
    varis_nokta: str = "NRT",
    tarih: str = "2026-12-27",
    kalkis_saat: tuple[int, int] = (16, 20),
    sure_dk: int = 770,
    aktarmalar: list[Aktarma] | None = None,
) -> Segment:
    if aktarmalar is None:
        aktarmalar = [Aktarma("UBN")]
    y, m, d = [int(x) for x in tarih.split("-")]
    kalkis = datetime(y, m, d, kalkis_saat[0], kalkis_saat[1], tzinfo=TZ_TR)
    varis = (kalkis + timedelta(minutes=sure_dk)).astimezone(TZ_JP)
    return Segment(
        id=f"{kaynak}:{firma}:{tarih}",
        arac="ucak",
        kalkis_nokta=kalkis_nokta,
        varis_nokta=varis_nokta,
        kalkis=kalkis.isoformat(),
        varis=varis.isoformat(),
        kisi_basi=toplam / 2,
        toplam=toplam,
        aktarmalar=list(aktarmalar),
        aktarma_sayisi=len(aktarmalar),
        firma=firma,
        link=f"https://{kaynak}.test/ucus",
        kaynak=kaynak,
    )


class SahteTarayici:
    def hiz_sinirla(self, *_a, **_k):
        return None

    def kapat(self):
        return None


class SahteKaynak:
    def __init__(self, uretici=None, hata=None):
        self.uretici = uretici
        self.hata = hata
        self.cagrilar: list[tuple[str, str]] = []

    def ara(self, t, bacak, tarih):
        self.cagrilar.append((bacak.get("id", ""), tarih))
        if self.hata:
            raise self.hata
        if callable(self.uretici):
            return self.uretici(bacak, tarih)
        return list(self.uretici or [])


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    depo.kur(c)
    yield c
    c.close()


# ── Tarih Seçimi ve Yan Tur Rotasyonu ───────────────────────────────────────


def test_secilen_tarihler_ve_rotasyon():
    yan = {"id": "tyo_sel", "tarih": "2027-01-01", "esnek_gun": 3, "rotasyon": True}
    ana = {"id": "ist_tyo", "tarih": "2026-12-27", "esnek_gun": 2, "rotasyon": False}

    # Tam tur: tüm esnek pencere
    assert len(tara_mod.secilen_tarihler(yan)) == 7
    assert len(tara_mod.secilen_tarihler(ana)) == 5

    # Rotasyonlu yan tur bacağı 7 günde tüm pencereyi sırayla dolaşır
    secilen = [tara_mod.secilen_tarihler(yan, azami_tarih=1, rotasyon=r)[0] for r in range(7)]
    assert secilen == [
        "2026-12-29",
        "2026-12-30",
        "2026-12-31",
        "2027-01-01",
        "2027-01-02",
        "2027-01-03",
        "2027-01-04",
    ]
    assert tara_mod.secilen_tarihler(yan, azami_tarih=1, rotasyon=7) == ["2026-12-29"]

    # Rotasyonsuz ana bacak sayaç verilse bile merkez tarihte kalır
    assert tara_mod.secilen_tarihler(ana, azami_tarih=1, rotasyon=5) == ["2026-12-27"]
    # Sayaç None ise yan tur da merkez tarihte kalır
    assert tara_mod.secilen_tarihler(yan, azami_tarih=1, rotasyon=None) == ["2027-01-01"]


def test_plan_bacaklari_ana_rota_ve_yan_turlar():
    plan = ice_aktar_eski_plan()
    bacaklar = tara_mod.plan_bacaklari(plan)
    idler = [b["id"] for b in bacaklar]
    assert idler == ["ist_tyo", "tyo_ist", "tyo_sel", "sel_tyo"]

    by_id = {b["id"]: b for b in bacaklar}
    assert by_id["ist_tyo"]["rotasyon"] is False
    assert by_id["tyo_ist"]["rotasyon"] is False
    assert by_id["tyo_sel"]["rotasyon"] is True
    assert by_id["sel_tyo"]["rotasyon"] is True
    assert by_id["tyo_sel"]["tarih"] == "2026-12-31" and by_id["tyo_sel"]["esnek_gun"] == 3
    assert by_id["sel_tyo"]["tarih"] == "2027-01-02" and by_id["sel_tyo"]["esnek_gun"] == 3


# ── Tarama Orkestrası, Tekilleştirme, Hata Yalıtımı, Depo ──────────────────


def test_tara_plan_ve_yan_turlar_rotasyonlu(con, monkeypatch):
    plan = ice_aktar_eski_plan()
    google_k = SahteKaynak(
        uretici=lambda b, t: [
            _seg(
                "google",
                70000.0,
                kalkis_nokta=b["kalkis"],
                varis_nokta=b["varis"],
                tarih=t,
            )
        ]
    )
    monkeypatch.setitem(tara_mod.KAYNAKLAR, "google", google_k)

    sonuc = tara_mod.tara(
        con,
        SahteTarayici(),
        plan,
        kaynaklar=["google"],
        azami_tarih=1,
        rotasyon=0,
    )

    # 4 bacak (ist_tyo, tyo_ist, tyo_sel, sel_tyo) × 1 tarih = 4 çağrı
    assert google_k.cagrilar == [
        ("ist_tyo", "2026-12-27"),
        ("tyo_ist", "2027-01-06"),
        ("tyo_sel", "2026-12-28"),  # rotasyon=0 -> pencerenin ilk günü
        ("sel_tyo", "2026-12-30"),  # rotasyon=0 -> dönüş penceresinin ilk günü
    ]
    assert set(sonuc.segmentler.keys()) == {"ist_tyo", "tyo_ist", "tyo_sel", "sel_tyo"}
    assert len(depo.son("ist_tyo", v=con)) == 1
    assert len(depo.son("tyo_sel", v=con)) == 1


def test_tekillestirme_en_ucuz_kaynak_kalir_ve_depoya_yazilir(con, monkeypatch):
    plan = {
        "bacaklar": [
            {
                "id": "ist_nrt",
                "kalkis": "IST",
                "varis": "NRT",
                "tarih": "2026-12-27",
                "yolcu": 2,
                "esnek_gun": 0,
            }
        ]
    }
    google_k = SahteKaynak(
        [
            _seg("google", 82000.0, "MIAT"),
            _seg("google", 96000.0, "ANA", kalkis_saat=(10, 0), sure_dk=690, aktarmalar=[]),
        ]
    )
    sky_k = SahteKaynak(
        [
            _seg("skyscanner", 74000.0, "MIAT"),
        ]
    )
    monkeypatch.setitem(tara_mod.KAYNAKLAR, "google", google_k)
    monkeypatch.setitem(tara_mod.KAYNAKLAR, "skyscanner", sky_k)

    sonuc = tara_mod.tara(
        con,
        SahteTarayici(),
        plan,
        kaynaklar=["google", "skyscanner"],
    )

    segler = sonuc.segmentler["ist_nrt"]
    assert len(segler) == 2
    miat = next(s for s in segler if s.firma == "MIAT")
    assert miat.toplam == 74000.0
    assert miat.kaynak == "skyscanner"

    # Depodan son() ile okunabiliyor
    depodakiler = depo.son("ist_nrt", saat=36, v=con)
    assert len(depodakiler) == 2
    assert depodakiler[0].firma == "MIAT" and depodakiler[0].toplam == 74000.0


def test_kaynak_hatasi_turu_oldurmez_ve_desteklenmeyen_atlanir(con, monkeypatch):
    plan = {
        "bacaklar": [
            {
                "id": "ist_nrt",
                "kalkis": "IST",
                "varis": "NRT",
                "tarih": "2026-12-27",
                "yolcu": 2,
                "esnek_gun": 0,
            }
        ]
    }
    google_k = SahteKaynak([_seg("google", 78000.0, "MIAT")])
    sky_k = SahteKaynak(hata=RuntimeError("ag zaman asimi"))
    kayak_k = SahteKaynak(hata=BotEngeli("kayak", "captcha cikti"))

    monkeypatch.setitem(tara_mod.KAYNAKLAR, "google", google_k)
    monkeypatch.setitem(tara_mod.KAYNAKLAR, "skyscanner", sky_k)
    monkeypatch.setitem(tara_mod.KAYNAKLAR, "kayak", kayak_k)

    sonuc = tara_mod.tara(
        con,
        SahteTarayici(),
        plan,
        kaynaklar=["google", "skyscanner", "kayak", "thy"],
    )

    # Hatalı kaynaklar ve desteklenmeyen THY turu öldürmedi; google sonucu depoda
    assert len(sonuc.segmentler["ist_nrt"]) == 1
    assert len(depo.son("ist_nrt", v=con)) == 1

    assert sonuc.kapsam[("ist_nrt", "2026-12-27", "google")]["tur"] == "ok"
    assert sonuc.kapsam[("ist_nrt", "2026-12-27", "skyscanner")]["tur"] == "hata"
    assert "RuntimeError: ag zaman asimi" in sonuc.kapsam[("ist_nrt", "2026-12-27", "skyscanner")]["hata"]
    assert sonuc.kapsam[("ist_nrt", "2026-12-27", "kayak")]["tur"] == "blok"
    assert sonuc.kapsam[("ist_nrt", "2026-12-27", "thy")]["tur"] == "desteklenmiyor"

    ozet = sonuc.kapsam_ozeti()
    assert ozet.startswith("kapsam: 1/1 bacak, 1/4 deneme sonuçlu, kaynak 1/3 çalıştı")
