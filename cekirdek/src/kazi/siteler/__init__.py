"""Site bağdaştırıcı kaydı (18 site modülü + Kart ara nesnesi)."""
from __future__ import annotations

from types import ModuleType

from ._kart import Kart, tl_sayi
from . import (
    akakce,
    amazon,
    cimri,
    epey,
    gamegaraj,
    gaminggentr,
    hepsiburada,
    idefix,
    incehesap,
    itopya,
    mediamarkt,
    n11,
    pazarama,
    pttavm,
    sinerji,
    teknosa,
    trendyol,
    vatan,
)

SITELER: dict[str, ModuleType] = {
    "akakce": akakce,
    "amazon": amazon,
    "cimri": cimri,
    "epey": epey,
    "gamegaraj": gamegaraj,
    "gaminggentr": gaminggentr,
    "hepsiburada": hepsiburada,
    "idefix": idefix,
    "incehesap": incehesap,
    "itopya": itopya,
    "mediamarkt": mediamarkt,
    "n11": n11,
    "pazarama": pazarama,
    "pttavm": pttavm,
    "sinerji": sinerji,
    "teknosa": teknosa,
    "trendyol": trendyol,
    "vatan": vatan,
}

__all__ = ["Kart", "SITELER", "tl_sayi"]
