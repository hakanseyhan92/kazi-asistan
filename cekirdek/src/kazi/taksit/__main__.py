"""`python -m kazi.taksit` komut satırı arayüzü."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from kazi.ayar import ayar_yukle, hazirla
from kazi.db import VeriTabani
from kazi.modeller import Taksit
from kazi.tarayici import Tarayici

from . import urun_taksitlerini_cek


def tl_bicimle(tutar: float | None) -> str:
    """Tutarı '108.499 TL' / '108.499,50 TL' biçiminde döndürür."""
    if tutar is None:
        return "-"
    tam = int(tutar)
    kusur = round((tutar - tam) * 100)
    if kusur == 100:
        tam += 1
        kusur = 0
    tam_yazi = f"{tam:,}".replace(",", ".")
    if kusur == 0:
        return f"{tam_yazi} TL"
    return f"{tam_yazi},{kusur:02d} TL"


def _en_iyi_pesin_ozet(satirlar: list[Taksit]) -> str:
    if not satirlar:
        return "tablo yok"
    bankali = [s for s in satirlar if s.banka]
    pesinler = [s for s in satirlar if (s.vade_farki_yuzde or 0.0) == 0.0]
    en_uzun_pesin = max((s.taksit_sayisi for s in pesinler), default=None)
    bankalar = sorted({s.banka for s in bankali if s.banka})
    parcalar: list[str] = [f"{len(satirlar)} satır"]
    if bankalar:
        parcalar.append(f"{len(bankalar)} kart ({', '.join(bankalar[:3])}{'...' if len(bankalar) > 3 else ''})")
    if en_uzun_pesin:
        parcalar.append(f"peşin {en_uzun_pesin}x")
    ornek_vadeli = next((s for s in bankali if (s.vade_farki_yuzde or 0.0) > 0), None)
    if ornek_vadeli:
        parcalar.append(
            f"örn. {ornek_vadeli.banka} {ornek_vadeli.taksit_sayisi}x "
            f"{tl_bicimle(ornek_vadeli.aylik)} (+%{ornek_vadeli.vade_farki_yuzde:.2f})"
        )
    return " · ".join(parcalar)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m kazi.taksit",
        description="Ürünün en ucuz teklifleri için kredi kartı taksit tablolarını çeker.",
    )
    parser.add_argument("--urun", required=True, help="Ürün kimliği (ör. gpu_rtx5080)")
    parser.add_argument("--azami", type=int, default=5, help="Bakılacak en ucuz teklif sayısı")
    parser.add_argument("--zorla", action="store_true", help="12 saatlik önbelleği yok say")
    parser.add_argument("--yalniz-k0", action="store_true", help="Brave açmadan yalnız HTTP K0 kullan")
    parser.add_argument("--db", type=Path, default=None, help="Özel SQLite veritabanı yolu")
    args = parser.parse_args(argv)

    ayar = ayar_yukle()
    if args.db is not None:
        db_yolu = args.db
        db_yolu.parent.mkdir(parents=True, exist_ok=True)
    else:
        hazirla(ayar)
        db_yolu = ayar.db_yolu

    v = VeriTabani(db_yolu)
    tarayici = Tarayici(veritabani=v, durum_dosya=db_yolu.parent / "durum.json")
    if args.yalniz_k0:
        setattr(tarayici, "yalniz_k0", True)

    try:
        rapor = urun_taksitlerini_cek(
            v,
            tarayici,
            args.urun,
            azami=args.azami,
            zorla=args.zorla,
        )
    finally:
        tarayici.kapat()
        v.kapat()

    if not rapor:
        print(f"{args.urun}: geçerli teklif bulunamadı.")
        return 0

    for idx, oge in enumerate(rapor, 1):
        t = oge["teklif"]
        ozet = oge["neden"] or _en_iyi_pesin_ozet(oge["satirlar"])
        print(
            f"[{idx}] {t.site} | {t.satici or '-'} | {tl_bicimle(t.sepet_fiyati)} "
            f"| {oge['durum']} | {ozet}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
