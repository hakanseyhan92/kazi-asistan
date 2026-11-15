"""Segment (bir bacağın tek seçeneği: uçuş / YHT / otobüs) ve veri dosyası yükleyicileri."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

VERI = Path(__file__).resolve().with_name("veri")


@lru_cache(maxsize=None)
def veri(ad: str) -> dict | list:
    return json.loads((VERI / ad).read_text(encoding="utf-8"))


def noktalar() -> dict[str, list[dict]]:
    return veri("noktalar.json")


def havalimani_ulke(kod: str) -> str | None:
    return veri("havalimani_ulke.json").get(str(kod).upper())


def nokta_ulke(kod: str) -> str | None:
    """Havalimanı, gar ya da otogar kodunun ülkesi."""
    u = havalimani_ulke(kod)
    if u:
        return u
    for liste in noktalar().values():
        for n in liste:
            if n["kod"] == kod:
                return n["ulke"]
    return None


@dataclass
class Aktarma:
    nokta: str | None           # aktarma noktası kodu (bilinmiyorsa None)
    ulke: str | None = None     # ISO2; None ise havalimanı tablosundan bulunur
    sure_dk: int | None = None
    ayri_bilet: bool = False    # bu noktada farklı bilete geçiliyor (bagaj + pasaport kontrolü)

    def __post_init__(self) -> None:
        if self.ulke is None and self.nokta:
            self.ulke = nokta_ulke(self.nokta)


@dataclass
class Segment:
    id: str
    arac: str                   # ucak | yht | otobus
    kalkis_nokta: str
    varis_nokta: str
    kalkis: str                 # ISO-8601, saat dilimli
    varis: str
    kisi_basi: float
    toplam: float | None = None
    aktarmalar: list[Aktarma] = field(default_factory=list)
    aktarma_sayisi: int | None = None   # kaynak sayıyı verip noktaları vermeyebilir
    firma: str | None = None
    link: str | None = None
    kaynak: str | None = None
    dis_hat: bool | None = None
    bagaj: str | None = None
    gozlem_zamani: str | None = None

    def __post_init__(self) -> None:
        if self.aktarma_sayisi is None:
            self.aktarma_sayisi = len(self.aktarmalar)
        if self.dis_hat is None:
            self.dis_hat = nokta_ulke(self.kalkis_nokta) != nokta_ulke(self.varis_nokta)

    @property
    def kalkis_dt(self) -> datetime:
        return datetime.fromisoformat(self.kalkis)

    @property
    def varis_dt(self) -> datetime:
        return datetime.fromisoformat(self.varis)

    @property
    def sure_dk(self) -> int:
        return int((self.varis_dt - self.kalkis_dt).total_seconds() // 60)

    @property
    def ayri_bilet(self) -> bool:
        return any(a.ayri_bilet for a in self.aktarmalar)


# Ayrı bilet / kendi aktarmasını yapan havayolları (evren-asistan ucus/plan.py, 14.11)
AYRI_BILET_GOSTERGELERI = (
    "flyadeal", "flynas", "jetstar", "airasia", "peach", "zipair", "spring", "scoot", "pegasus", "wizz",
    "vietjet", "cebu", "self transfer", "self-transfer", "birden fazla hava yolu", "birden fazla havayolu",
    "multiple airlines",
)


def ayri_bilet_tahmini(firma: str | None, aktarma_sayisi: int) -> str | None:
    """Aktarmalı uçuşta firma adında ayrı bilet göstergesi varsa onu döndür."""
    if aktarma_sayisi < 1:
        return None
    f = str(firma or "").lower()
    return next((g for g in AYRI_BILET_GOSTERGELERI if g in f), None)


# Havayolunun merkez ülkesi: aktarma noktası kaynakta yoksa merkezden aktarma yaptığı varsayılır.
HAVAYOLU_AD_ULKE = [
    (r"air\s*china|china\s*eastern|china\s*southern|hainan|sichuan|xiamen|shenzhen\s*air|juneyao|"
     r"spring\s*airlines|shandong|tibet\s*air|loong\s*air|chengdu\s*air", "CN"),
    (r"\bcathay\b|hong\s*kong\s*air|hk\s*express", "HK"),
    (r"emirates|fly\s*dubai|etihad|air\s*arabia", "AE"),
    (r"korean\s*air|asiana|jeju\s*air|t'?way|jin\s*air|air\s*busan|air\s*premia|air\s*seoul", "KR"),
    (r"qatar\s*airways", "QA"),
    (r"turkmenistan", "TM"),
]
HAVAYOLU_KOD_ULKE = {
    "CN": {"CA", "MU", "CZ", "HU", "3U", "MF", "ZH", "HO", "9C", "SC", "TV", "GS", "JD", "EU", "8L", "KN", "G5", "QW", "DZ"},
    "HK": {"CX", "UO", "HX"},
    "AE": {"EK", "FZ", "EY", "G9", "3L"},
    "KR": {"KE", "OZ", "7C", "LJ", "TW", "BX", "RS", "ZE", "YP", "RF"},
    "QA": {"QR"},
    "TM": {"T5"},
}


def havayolu_ulkeleri(firma: str | None) -> set[str]:
    f = str(firma or "")
    ulkeler = {u for rx, u in HAVAYOLU_AD_ULKE if re.search(rx, f.lower())}
    kodlar = {k for k in re.split(r"[\s,/+&|·;()]+", f.upper()) if 2 <= len(k) <= 3}
    return ulkeler | {u for u, kume in HAVAYOLU_KOD_ULKE.items() if kume & kodlar}
