"""Uçuş deposu (segment_teklifi tablosu) testleri (SARTNAME §13.6, AĞ YOK)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from kazi.seyahat.segment import Aktarma, Segment
from kazi.ucus import depo

TZ_TR = timezone(timedelta(hours=3))
TZ_JP = timezone(timedelta(hours=9))


def _ornek_segment(
    kaynak: str = "google",
    firma: str = "MIAT",
    kalkis_nokta: str = "IST",
    varis_nokta: str = "NRT",
    kalkis_saat: tuple[int, int] = (16, 20),
    sure_dk: int = 770,
    toplam: float = 79452.0,
    aktarmalar: list[Aktarma] | None = None,
    gozlem_zamani: str | None = None,
) -> Segment:
    if aktarmalar is None:
        aktarmalar = [Aktarma("UBN", sure_dk=120, ayri_bilet=False)]
    kalkis = datetime(2026, 12, 27, kalkis_saat[0], kalkis_saat[1], tzinfo=TZ_TR)
    varis = (kalkis + timedelta(minutes=sure_dk)).astimezone(TZ_JP)
    return Segment(
        id="",
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
        link="https://ornek.test/bilet",
        kaynak=kaynak,
        bagaj="23 kg",
        gozlem_zamani=gozlem_zamani,
    )


@pytest.fixture
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    depo.kur(c)
    yield c
    c.close()


def test_kaydet_ve_son_en_yeni_gozlemi_dondurur(con):
    now = datetime.now(TZ_TR)
    t_eski = (now - timedelta(hours=5)).isoformat(timespec="seconds")
    t_yeni = (now - timedelta(hours=1)).isoformat(timespec="seconds")

    # Aynı uçuşun eski gözlemi (85.000 TL) ve yeni gözlemi (76.000 TL) + farklı bir uçuş (ANA 98.000 TL)
    s_eski = _ornek_segment(toplam=85000.0, gozlem_zamani=t_eski)
    s_yeni = _ornek_segment(toplam=76000.0, gozlem_zamani=t_yeni)
    s_ana = _ornek_segment(
        firma="ANA",
        kalkis_saat=(10, 0),
        sure_dk=690,
        toplam=98000.0,
        aktarmalar=[],
        gozlem_zamani=t_eski,
    )

    depo.kaydet([s_eski, s_ana], "ist_tyo", v=con)
    depo.kaydet([s_yeni], "ist_tyo", v=con)

    sonuc = depo.son("ist_tyo", saat=36, v=con)
    assert len(sonuc) == 2
    assert [s.firma for s in sonuc] == ["MIAT", "ANA"]
    miat = sonuc[0]
    assert miat.toplam == 76000.0
    assert miat.kisi_basi == 38000.0
    assert miat.kalkis_dt.utcoffset() == timedelta(hours=3)
    assert miat.varis_dt.utcoffset() == timedelta(hours=9)
    assert miat.sure_dk == 770
    assert miat.aktarmalar == [Aktarma("UBN", sure_dk=120, ayri_bilet=False)]
    assert miat.bagaj == "23 kg"
    assert miat.link == "https://ornek.test/bilet"


def test_son_saat_filtresi_eski_gozlemleri_dislar(con):
    now = datetime.now(TZ_TR)
    t_48s_once = (now - timedelta(hours=48)).isoformat(timespec="seconds")
    t_2s_once = (now - timedelta(hours=2)).isoformat(timespec="seconds")

    s_cok_eski = _ornek_segment(firma="EskiHava", toplam=50000.0, gozlem_zamani=t_48s_once)
    s_taze = _ornek_segment(firma="TazeHava", kalkis_saat=(18, 0), toplam=60000.0, gozlem_zamani=t_2s_once)

    depo.kaydet([s_cok_eski], "ist_tyo", v=con)
    depo.kaydet([s_taze], "ist_tyo", v=con)

    sonuc_36 = depo.son("ist_tyo", saat=36, v=con)
    assert [s.firma for s in sonuc_36] == ["TazeHava"]

    sonuc_72 = depo.son("ist_tyo", saat=72, v=con)
    assert [s.firma for s in sonuc_72] == ["EskiHava", "TazeHava"]


def test_depo_sarmalayici_sinif(con):
    d = depo.Depo(con)
    s = _ornek_segment(toplam=64000.0)
    yazilan = d.kaydet([s], "ist_tyo")
    assert yazilan == 1
    gelen = d.son("ist_tyo", saat=36)
    assert len(gelen) == 1 and gelen[0].toplam == 64000.0
