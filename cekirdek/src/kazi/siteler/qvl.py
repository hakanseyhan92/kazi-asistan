"""Gigabyte X870 AORUS ELITE WIFI7 resmî uyumluluk listeleri (QVL) — 14.11 orkestra.

Kullanıcı kuralı: "uyumsuz çıkıp iade etmek zorunda kalmayayım". Bu yüzden RAM ve SSD
teklifleri yalnız Gigabyte'ın kendi listesinde DOĞRULANMIŞ bir parçaya eşlenebiliyorsa
uygun sayılır (`"qvl": "bellek"` / `"qvl": "ssd"`).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

QVL_KLASOR = Path(__file__).resolve().parent / "veri" / "qvl"
BELLEK_DOSYA = QVL_KLASOR / "x870_aorus_elite_wifi7_bellek_ryzen9000.json"
SSD_DOSYA = QVL_KLASOR / "x870_aorus_elite_wifi7_ssd.json"
META_DOSYA = QVL_KLASOR / "meta.json"


def _norm(s: str) -> str:
    return re.sub(r"[\s\-/._()]+", "", str(s or "").upper())


def _tablo(yol: Path) -> tuple[dict, list[dict]]:
    d = json.loads(yol.read_text(encoding="utf-8"))
    kol: dict[str, str] = {}
    for c in d.get("columnsTitle", []):
        if "children" in c:
            for ch in c["children"]:
                kol[ch["key"]] = "slot" + str(ch["label"])
        else:
            kol[c["key"]] = c["label"]
    satirlar = [{kol.get(k, k): v for k, v in it.items()} for it in d.get("items", [])]
    return kol, satirlar


@lru_cache(maxsize=1)
def bellek_satirlari() -> list[dict]:
    return _tablo(BELLEK_DOSYA)[1]


@lru_cache(maxsize=1)
def ssd_satirlari() -> list[dict]:
    return _tablo(SSD_DOSYA)[1]


def _bellek_uygun_satir(s: dict) -> bool:
    """2×32 kit için: 32GB modül, EXPO doğrulanmış, 2 DIMM doğrulanmış."""
    return (str(s.get("Capacity", "")).upper() == "32GB"
            and str(s.get("EXPO", "")).strip().lower() == "v"
            and str(s.get("slot2", "")).strip().lower() == "v")


@lru_cache(maxsize=1)
def _bellek_pn() -> dict[str, dict]:
    out = {}
    for s in bellek_satirlari():
        if not _bellek_uygun_satir(s):
            continue
        pn = str(s.get("Module P/N.", "")).split(" ver")[0].strip()
        if len(_norm(pn)) >= 8:
            out[_norm(pn)] = s
    return out


@lru_cache(maxsize=1)
def _ssd_pn() -> dict[str, dict]:
    out = {}
    for s in ssd_satirlari():
        # Sistem SSD'si Gen5 (CPU'ya bağlı M2A); Gen4 satırlar bu amaçla sayılmaz.
        if str(s.get("PCIe Gen", "")).replace(" ", "").lower() != "gen5x4":
            continue
        model = str(s.get("Model \\ Module P/N.", ""))
        for parca in model.split():
            if len(parca) >= 8 and re.search(r"\d", parca) and re.search(r"[A-Z]", parca.upper()):
                out[_norm(parca)] = s
    return out


_BELLEK_MODEL = [
    ("G.Skill Trident Z5 Neo RGB 6000 CL30",
     [r"z5\s*neo\s*rgb", r"6000", r"(cl\s*30|j3040)", r"64\s*gb"], "F56000J3040G32GX2TZ5NR"),
    ("XPG Lancer 6000 CL30",
     [r"lancer", r"6000", r"(cl\s*30|c30)", r"64\s*gb"], "AX5U6000C3032G"),
    ("XPG Lancer 6400 CL32",
     [r"lancer", r"6400", r"(cl\s*32|c32)", r"64\s*gb"], "AX5U6400C3232G"),
    ("Kingston FURY Beast EXPO 6000 CL30",
     [r"beast", r"expo", r"6000", r"(cl\s*30|c30)", r"64\s*gb"], "KF560C30BBE"),
    ("Kingston FURY Beast EXPO 6400 CL32",
     [r"beast", r"expo", r"6400", r"(cl\s*32|c32)", r"64\s*gb"], "KF564C32BBE"),
]
_SSD_MODEL = [
    ("Samsung 9100 PRO 1TB", [r"9100\s*pro", r"1\s*tb"], "MZVAP1T0"),
    ("Crucial T705 1TB", [r"t705", r"1\s*tb"], "CT1000T705"),
]


def _pn_ara(ad: str, tablo: dict[str, dict]) -> tuple[str, dict] | None:
    n = _norm(ad)
    for pn in sorted(tablo, key=len, reverse=True):
        if pn in n:
            return pn, tablo[pn]
    return None


def _model_ara(ad: str, kurallar, tablo: dict[str, dict]) -> tuple[str, dict] | None:
    for etiket_adi, desenler, onek in kurallar:
        if all(re.search(d, ad, re.IGNORECASE) for d in desenler):
            satir = next((s for pn, s in tablo.items() if pn.startswith(onek)), None)
            if satir is not None:
                return etiket_adi, satir
    return None


def eslestir(tur: str, ad: str) -> dict | None:
    """QVL eşleşmesi: {"yol": "pn"|"model", "pn": ..., "satir": {...}} ya da None."""
    tablo = _bellek_pn() if tur == "bellek" else _ssd_pn() if tur == "ssd" else None
    if tablo is None:
        return None
    r = _pn_ara(ad, tablo)
    if r:
        return {"yol": "pn", "pn": r[0], "satir": r[1]}
    r = _model_ara(ad, _BELLEK_MODEL if tur == "bellek" else _SSD_MODEL, tablo)
    if r:
        return {"yol": "model", "pn": r[0], "satir": r[1]}
    return None


def etiket(tur: str | None, ad: str) -> str:
    """Rapor etiketi: '✅ QVL' (P/N), '✅ QVL(model)' ya da '' (QVL dışı/uygulanmaz)."""
    if not tur:
        return ""
    e = eslestir(tur, ad)
    if not e:
        return "⚠️ QVL dışı"
    return "✅ QVL" if e["yol"] == "pn" else "✅ QVL(model)"


def meta() -> dict:
    try:
        return json.loads(META_DOSYA.read_text(encoding="utf-8"))
    except Exception:
        return {}
