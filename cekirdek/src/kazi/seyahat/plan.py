"""Seyahat planı: duraklar → bacaklar, yolcular, ters rota, eski plan.json içe aktarma (SARTNAME §13.1–13.2)."""
from __future__ import annotations

import os

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .segment import noktalar
from .yan_tur import YanTur

ESKI_PLAN = Path(os.environ.get("KAZI_ESKI_KOK") or Path.home() / "evren-asistan") / "ucus" / "plan.json"
ARACLAR = ("ucak", "yht", "otobus")
_NOKTA_TUR = {"ucak": "havalimani", "yht": "yht", "otobus": "otogar"}
# eski plan.json IATA şehir kodları → noktalar.json şehir adı
SEHIR_KODU = {"IST": "İstanbul", "TYO": "Tokyo", "SEL": "Seul", "OSA": "Osaka", "ANK": "Ankara", "IZM": "İzmir"}


@dataclass
class Yolcu:
    id: str
    ad: str
    uyruklar: list[str]
    izinler: list[dict] = field(default_factory=list)   # {ulke, tur, bitis|None, durum: gercek|varsayim, not?}


@dataclass
class Durak:
    sehir: str
    tarih: date | None = None
    esneklik: int = 0            # ±gün: 0 | 1 | 2
    kalis_gece: int | None = None
    gecis_noktasi: bool = False  # kalmadan aktarma (ör. Ankara→İstanbul→Tokyo'daki İstanbul)


@dataclass
class Bacak:
    sira: int
    kalkis_sehir: str
    varis_sehir: str
    en_erken: date | None
    en_gec: date | None
    yolcular: list[Yolcu]
    araclar: list[str]
    gecis_sonrasi: bool = False  # kalkış şehri geçiş noktası → önceki bacağa bağlantı payıyla bağlanır
    id: str | None = None


@dataclass
class Plan:
    duraklar: list[Durak]
    yolcular: list[Yolcu]
    bacak_yolculari: dict[int, list[str]] = field(default_factory=dict)  # bacak sırası → yolcu id'leri
    bacak_araclari: dict[int, list[str]] = field(default_factory=dict)
    bacak_idleri: dict[int, str] = field(default_factory=dict)
    yan_turlar: list[YanTur] = field(default_factory=list)


def sehir_nokta_turleri(sehir: str) -> set[str]:
    return {n["tur"] for n in noktalar().get(sehir, [])}


def sehir_ulkesi(sehir: str) -> str | None:
    n = noktalar().get(sehir)
    return n[0]["ulke"] if n else None


def varsayilan_araclar(sehir_a: str, sehir_b: str) -> list[str]:
    """İki şehir de TR'deyse her iki uçta bulunan araçlar; değilse yalnız uçak."""
    if not (sehir_ulkesi(sehir_a) == sehir_ulkesi(sehir_b) == "TR"):
        return ["ucak"]
    ta, tb = sehir_nokta_turleri(sehir_a), sehir_nokta_turleri(sehir_b)
    return [a for a in ARACLAR if _NOKTA_TUR[a] in ta and _NOKTA_TUR[a] in tb]


def bacaklar(plan: Plan) -> list[Bacak]:
    sonuc: list[Bacak] = []
    yolcu_sozluk = {y.id: y for y in plan.yolcular}
    for i in range(len(plan.duraklar) - 1):
        a, b = plan.duraklar[i], plan.duraklar[i + 1]
        tarih = a.tarih
        esnek = a.esneklik
        if tarih is None and i > 0:
            # tarih verilmemiş ara durak: önceki durağın tarihi + kalış
            onceki = plan.duraklar[i - 1]
            if onceki.tarih is not None:
                tarih = onceki.tarih + timedelta(days=a.kalis_gece or 0)
                esnek = max(esnek, onceki.esneklik)
        ids = plan.bacak_yolculari.get(i) or [y.id for y in plan.yolcular]
        sonuc.append(Bacak(
            sira=i, kalkis_sehir=a.sehir, varis_sehir=b.sehir,
            en_erken=tarih - timedelta(days=esnek) if tarih else None,
            en_gec=tarih + timedelta(days=esnek) if tarih else None,
            yolcular=[yolcu_sozluk[x] for x in ids],
            araclar=plan.bacak_araclari.get(i) or varsayilan_araclar(a.sehir, b.sehir),
            gecis_sonrasi=a.gecis_noktasi and i > 0,
            id=plan.bacak_idleri.get(i),
        ))
    return sonuc


def ters_rota(plan: Plan, donus_tarihi: date, esneklik: int = 0) -> Plan:
    """Rotanın tersi: son durak dönüş tarihiyle başlar; ara duraklar geçiş noktası olarak korunur, yolcular aynalanır."""
    ters = list(reversed(plan.duraklar))
    duraklar = [Durak(ters[0].sehir, donus_tarihi, esneklik)]
    for d in ters[1:]:
        duraklar.append(Durak(d.sehir, None, esneklik, gecis_noktasi=d.gecis_noktasi))
    n = len(plan.duraklar) - 1
    return Plan(
        duraklar=duraklar, yolcular=plan.yolcular,
        bacak_yolculari={n - 1 - k: v for k, v in plan.bacak_yolculari.items()},
        bacak_araclari={n - 1 - k: v for k, v in plan.bacak_araclari.items()},
    )


def varsayilan_yolcular() -> list[Yolcu]:
    """Örnek yolcular (eski plan içe aktarımında kullanılır; arayüzden düzenlenir)."""
    return [Yolcu("ali", "Ali", ["TR"]), Yolcu("ayse", "Ayşe", ["TR"])]


def ice_aktar_eski_plan(yol: Path | str | None = None, kalis_gece: int = 2) -> Plan:
    """evren-asistan ucus/plan.json → Plan. Yolcu sayısı 2 → Ali + Ayşe, 1 → Ali.

    Ardışık iki solo bacak A→B, B→A ise (14.11: Tokyo→Seul→Tokyo, Ali tek başına, Seul'de 2 gece)
    ana rotaya girmez, esnek pencereli YAN TUR olur. Pencere = gidiş tarihi − esneklik … dönüş tarihi + esneklik.
    """
    d = json.loads(Path(yol or ESKI_PLAN).read_text(encoding="utf-8"))   # varsayılan çağrı anında çözülür (testler değiştirebilsin)
    bl = d["bacaklar"]
    yolcular = varsayilan_yolcular()

    def sehir(kod):
        return SEHIR_KODU.get(kod, kod)

    def solo(b):
        return int(b.get("yolcu") or 1) < 2

    ana: list[dict] = []
    yan_turlar: list[YanTur] = []
    i = 0
    while i < len(bl):
        b = bl[i]
        s = bl[i + 1] if i + 1 < len(bl) else None
        if s and solo(b) and solo(s) and b["varis"] == s["kalkis"] and b["kalkis"] == s["varis"]:
            yan_turlar.append(YanTur(
                ana_sehir=sehir(b["kalkis"]), hedef_sehir=sehir(b["varis"]), yolcu_idler=["ali"], kalis_gece=kalis_gece,
                en_erken_gidis=date.fromisoformat(b["tarih"]) - timedelta(days=int(b.get("esnek_gun") or 0)),
                en_gec_donus=date.fromisoformat(s["tarih"]) + timedelta(days=int(s.get("esnek_gun") or 0)),
                gidis_bacak_id=b["id"], donus_bacak_id=s["id"]))
            i += 2
            continue
        ana.append(b)
        i += 1

    duraklar = [Durak(sehir(b["kalkis"]), date.fromisoformat(b["tarih"]), int(b.get("esnek_gun") or 0)) for b in ana]
    duraklar.append(Durak(sehir(ana[-1]["varis"])))
    return Plan(duraklar=duraklar, yolcular=yolcular,
                bacak_yolculari={i: (["ali", "ayse"] if not solo(b) else ["ali"]) for i, b in enumerate(ana)},
                bacak_araclari={i: ["ucak"] for i in range(len(ana))},
                bacak_idleri={i: b["id"] for i, b in enumerate(ana)}, yan_turlar=yan_turlar)
