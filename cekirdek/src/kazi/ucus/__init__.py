"""Uçuş kaynakları, segment deposu ve tarama orkestrası (SARTNAME §13)."""
from __future__ import annotations

from . import depo, kaynaklar, model, tara
from .depo import Depo, kaydet, kur, son
from .kaynaklar import DESTEKLENMIYOR, HIZLI_KAYNAKLAR, KAYNAKLAR
from .tara import TaramaSonucu, secilen_tarihler, tarihler, tekillestir

__all__ = [
    "DESTEKLENMIYOR",
    "HIZLI_KAYNAKLAR",
    "KAYNAKLAR",
    "Depo",
    "TaramaSonucu",
    "depo",
    "kaydet",
    "kaynaklar",
    "kur",
    "model",
    "secilen_tarihler",
    "son",
    "tara",
    "tarihler",
    "tekillestir",
]
