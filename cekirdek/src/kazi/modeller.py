"""Pydantic v2 veri modelleri (SARTNAME §6).

Para alanları float TL; zamanlar ISO-8601 metin (Europe/Istanbul).
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

TZ_ISTANBUL = ZoneInfo("Europe/Istanbul")


def simdi_iso() -> str:
    """Şu anki zaman, Europe/Istanbul, ISO-8601."""
    return datetime.now(TZ_ISTANBUL).isoformat(timespec="seconds")


class Tur(StrEnum):
    urun = "urun"
    ucus = "ucus"
    bilet = "bilet"
    diger = "diger"


class TeyitDurumu(StrEnum):
    bekliyor = "bekliyor"
    teyitli = "teyitli"
    supheli = "supheli"
    urun_farkli = "urun_farkli"
    stok_yok = "stok_yok"
    fiyat_degisti = "fiyat_degisti"


class Katman(StrEnum):
    K0 = "K0"
    K1 = "K1"
    K2 = "K2"
    K3 = "K3"


class _Temel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FiyatBandi(_Temel):
    alt: float | None = None
    ust: float | None = None


class UrunKimligi(_Temel):
    id: str | None = None
    tur: Tur = Tur.urun
    kanonik_ad: str
    mpn: str | None = None
    ean: str | None = None
    eslesme_kurallari: dict[str, Any] = Field(default_factory=dict)
    fiyat_bandi: FiyatBandi | None = None
    olusturma: str = Field(default_factory=simdi_iso)
    aktif: bool = True


class Site(_Temel):
    ad: str
    tur: str | None = None
    katman_tercihi: Katman | None = None
    son_basari: str | None = None
    son_hata: str | None = None
    hata_turu: str | None = None
    etkin: bool = True


class Teklif(_Temel):
    id: int | None = None
    urun_id: str
    site: str
    satici: str | None = None
    baslik: str | None = None
    url: str | None = None
    liste_fiyati: float | None = None
    sepet_fiyati: float | None = None
    kupon: str | None = None
    kargo: float | None = None
    stok: bool | None = None
    pesin_fiyatina_taksit: bool | None = None
    gozlem_zamani: str = Field(default_factory=simdi_iso)
    kaynak_katmani: Katman | None = None
    teyit_durumu: TeyitDurumu = TeyitDurumu.bekliyor
    teyit_zamani: str | None = None
    kanit_goruntusu: str | None = None


class Taksit(_Temel):
    id: int | None = None
    teklif_id: int
    banka: str | None = None
    taksit_sayisi: int
    aylik: float | None = None
    toplam: float | None = None
    vade_farki_yuzde: float | None = None


class UcusTeklifi(_Temel):
    id: int | None = None
    bacak: str
    tarih: str
    havayolu: str | None = None
    aktarmalar: list[str] = Field(default_factory=list)
    sure_dk: int | None = None
    bagaj: str | None = None
    fiyat_kisi: float | None = None
    fiyat_toplam: float | None = None
    kaynak: str | None = None
    link: str | None = None
    gozlem_zamani: str = Field(default_factory=simdi_iso)


class Izleme(_Temel):
    id: int | None = None
    urun_id: str
    siklik: str = "6s"
    hedef_fiyat: float | None = None
    alarm_kanallari: list[str] = Field(default_factory=list)
    etkin: bool = True


class Olay(_Temel):
    id: int | None = None
    tur: str
    zaman: str = Field(default_factory=simdi_iso)
    urun_id: str | None = None
    gorev_id: str | None = None
    mesaj: str | None = None
    veri: dict[str, Any] = Field(default_factory=dict)


class Aday(_Temel):
    id: str
    baslik: str
    kaynak: str | None = None
    url: str | None = None
    gorsel: str | None = None
    mpn: str | None = None
    ozellikler: dict[str, Any] = Field(default_factory=dict)
    fiyat_araligi: FiyatBandi | None = None


class TanimlaIstek(_Temel):
    metin: str
    tur: Tur = Tur.urun


class TanimlaYanit(_Temel):
    istek_id: str
    adaylar: list[Aday] = Field(default_factory=list)
    sorgu: dict[str, Any] | None = None      # ayrıştırılmış yapısal sorgu (kategori, model, özellikler, bütçe, güven)


class OnaylaIstek(_Temel):
    aday_idler: list[str] = Field(default_factory=list)
    kurallar: dict[str, Any] | None = None
    kanonik_ad: str | None = None
    mpn: str | None = None
    ean: str | None = None


class TaramaIstek(_Temel):
    urun_id: str


class TaramaYanit(_Temel):
    gorev_id: str
