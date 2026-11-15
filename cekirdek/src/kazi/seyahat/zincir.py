"""Bacak seçeneklerini uygulanabilir bağlantılarla bütün yolculuk zincirine çevir (SARTNAME §13.3)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .baglanti import BaglantiAyar, uygun_mu
from .plan import Bacak
from .segment import Segment
from .transit import denetle


@dataclass
class Zincir:
    segmentler: list[Segment]
    kisi_basi_toplam: float
    toplam_sure_dk: int
    toplam_aktarma: int
    uyarilar: list[str] = field(default_factory=list)


@dataclass
class BacakSecenekleri:
    gecen: list[tuple[Segment, list[str]]]          # (segment, uyarılar)
    elenen: list[tuple[Segment, list[str]]]         # (segment, gerekçeler)


def bacak_suz(bacak: Bacak, segmentler: list[Segment], kurallar: list[dict] | None = None) -> BacakSecenekleri:
    """Transit/giriş denetimi + bacağın araç ve tarih aralığı; elenme gerekçeleri saklanır."""
    gecen, elenen = [], []
    for s in segmentler:
        gerekce = []
        if s.arac not in bacak.araclar:
            gerekce.append(f"araç kapalı: {s.arac}")
        g = s.kalkis_dt.date()
        if bacak.en_erken and not (bacak.en_erken <= g <= bacak.en_gec):
            gerekce.append(f"tarih aralık dışı: {g}")
        t = denetle(s, bacak.yolcular, kurallar)
        gerekce += t.gerekceler
        (elenen if gerekce else gecen).append((s, gerekce if gerekce else t.uyarilar))
    gecen.sort(key=lambda x: x[0].kisi_basi)
    return BacakSecenekleri(gecen, elenen)


def en_iyi_zincirler(bacak_secenekleri: list[list[tuple[Segment, list[str]]]], baglantili: list[bool] | None = None,
                     ayar: BaglantiAyar | None = None, tablo: dict[str, int] | None = None,
                     k: int = 10, isin: int = 200) -> list[Zincir]:
    """Işın aramasıyla en iyi k zincir. `baglantili[i]` True ise i. bacak önceki bacağa bağlantı payıyla bağlanmalı
    (geçiş noktası); False ise yalnız kronolojik sıra yeter (şehirde kalış var)."""
    if not bacak_secenekleri or any(not b for b in bacak_secenekleri):
        return []
    baglantili = baglantili or [True] * len(bacak_secenekleri)
    kisiler: list[tuple[list[tuple[Segment, list[str]]], list[str]]] = [([s], []) for s in bacak_secenekleri[0]]

    def anahtar(z):
        segs = [s for s, _ in z[0]]
        return (sum(s.kisi_basi for s in segs), sum(s.sure_dk for s in segs), sum(s.aktarma_sayisi for s in segs))

    for i in range(1, len(bacak_secenekleri)):
        yeni = []
        for zincir, uyar in kisiler:
            onceki = zincir[-1][0]
            for s, su in bacak_secenekleri[i]:
                if baglantili[i]:
                    ok, _g, _b, bu = uygun_mu(onceki, s, tablo, ayar)
                    if not ok:
                        continue
                else:
                    if s.kalkis_dt <= onceki.varis_dt:
                        continue
                    bu = []
                yeni.append((zincir + [(s, su)], uyar + bu))
        yeni.sort(key=anahtar)
        kisiler = yeni[:isin]
        if not kisiler:
            return []

    sonuc = []
    for zincir, uyar in sorted(kisiler, key=anahtar)[:k]:
        segs = [s for s, _ in zincir]
        tum_uyari = list(dict.fromkeys(uyar + [u for _, su in zincir for u in su]))
        sonuc.append(Zincir(segs, sum(s.kisi_basi for s in segs), sum(s.sure_dk for s in segs),
                            sum(s.aktarma_sayisi for s in segs), tum_uyari))
    return sonuc
