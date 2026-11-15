"""Mağaza adı birliği (14.11): toplayıcılar (Akakçe/Cimri) aynı mağazayı farklı yazar ("Hepsiburada", "hepsiburada",
"İnce Hesap", "incehesap.com", "Amazon Türkiye"). Kayıtta tek anahtar (site modülü adı), ekranda okunur ad kullanılır."""
from __future__ import annotations

import re

ANAHTARLAR = {
    "hepsiburada": "hepsiburada", "trendyol": "trendyol", "trendyolplus": "trendyol", "pttavm": "pttavm", "amazon": "amazon",
    "amazonturkiye": "amazon", "incehesap": "incehesap", "mediamarkt": "mediamarkt", "teknosa": "teknosa", "pazarama": "pazarama",
    "n11": "n11", "vatan": "vatan", "vatanbilgisayar": "vatan", "itopya": "itopya", "sinerji": "sinerji", "sinerjibilgisayar": "sinerji",
    "gaminggentr": "gaminggentr", "gaminggen": "gaminggentr", "idefix": "idefix", "gamegaraj": "gamegaraj", "akakce": "akakce", "cimri": "cimri", "epey": "epey",
}
OKUNUR = {"hepsiburada": "Hepsiburada", "trendyol": "Trendyol", "pttavm": "PttAVM", "amazon": "Amazon", "incehesap": "İnceHesap",
          "mediamarkt": "MediaMarkt", "teknosa": "Teknosa", "pazarama": "Pazarama", "n11": "n11", "vatan": "Vatan", "itopya": "İtopya",
          "sinerji": "Sinerji", "gaminggentr": "Gaming.gen.tr", "idefix": "idefix", "gamegaraj": "GameGaraj", "akakce": "Akakçe",
          "cimri": "Cimri", "epey": "Epey"}


def _katla(s: str) -> str:
    s = s.replace("İ", "i").replace("I", "ı").lower()
    s = s.translate(str.maketrans("ıöüşçğâ", "iouscga"))
    s = re.sub(r"\.(com|net)(\.tr)?$|\.tr$", "", s.strip())
    return re.sub(r"[^a-z0-9]", "", s)


def site_anahtari(ad: str | None) -> str | None:
    """Bilinen mağaza → modül anahtarı; bilinmeyen → olduğu gibi (kırpılmış)."""
    if not ad:
        return ad
    return ANAHTARLAR.get(_katla(ad), ad.strip())


def okunur_ad(ad: str | None) -> str | None:
    if not ad:
        return ad
    return OKUNUR.get(site_anahtari(ad), ad)
