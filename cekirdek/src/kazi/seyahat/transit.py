"""Yolcu bazında transit ve giriş güvenliği (SARTNAME §13.2).

Her aktarma noktası × her yolcu denetlenir:
  yasak → eler · vize_gerekli → geçerli izin (gerçek ya da varsayım) yoksa eler
  airside + ayrı bilet → vize gerekir (bagaj için ülkeye giriş) · bilinmiyor → uyarı (ayrı biletse eler)
Varsayım izni (ör. henüz alınmamış JP vizesi) eleme yapmaz ama ⚠ uyarı bırakır.
"""
from __future__ import annotations

from pathlib import Path

import os

import json

from dataclasses import dataclass, field
from datetime import date

from .plan import Yolcu
from .segment import Segment, havayolu_ulkeleri, nokta_ulke, veri

ULKE_ADI = {"TR": "Türkiye", "JP": "Japonya", "KR": "Kore", "AE": "BAE", "CN": "Çin", "HK": "Hong Kong",
            "QA": "Katar", "KZ": "Kazakistan", "TM": "Türkmenistan", "IR": "İran", "SA": "Suudi Arabistan",
            "UZ": "Özbekistan", "AZ": "Azerbaycan", "GE": "Gürcistan", "TW": "Tayvan", "TH": "Tayland",
            "SG": "Singapur", "MY": "Malezya", "IN": "Hindistan", "DE": "Almanya", "FR": "Fransa", "NL": "Hollanda",
            "GB": "Birleşik Krallık", "FI": "Finlandiya", "PL": "Polonya", "AT": "Avusturya", "CH": "İsviçre"}
ONCELIK = {"serbest": 0, "airside": 1, "bilinmiyor": 2, "vize_gerekli": 3, "yasak": 4}


def ulke_adi(k: str | None) -> str:
    if k and k not in ULKE_ADI:
        try:
            from .segment import veri
            return veri("ulkeler.json").get(k, k)
        except FileNotFoundError:
            pass
    return ULKE_ADI.get(k or "", k or "?")


@dataclass
class TransitSonuc:
    gecer: bool
    gerekceler: list[str] = field(default_factory=list)
    uyarilar: list[str] = field(default_factory=list)


def kullanici_kural_yolu() -> Path:
    """Kişisel ek kurallar (depoya girmez): $KAZI_TRANSIT_KURALLARI ya da ~/.config/kazi/transit_kurallari.json."""
    ozel = os.environ.get("KAZI_TRANSIT_KURALLARI")
    if ozel:
        return Path(ozel).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kazi" / "transit_kurallari.json"


def kurallar_yukle() -> list[dict]:
    """Paket kuralları + kullanıcı kuralları; aynı (uyruk, ülke) için kullanıcı kuralı geçerlidir."""
    paket = list(veri("transit_kurallari.json")["kurallar"])
    try:
        kul = json.loads(kullanici_kural_yolu().read_text(encoding="utf-8")).get("kurallar") or []
    except (FileNotFoundError, ValueError, AttributeError):
        kul = []
    anahtar = {(k["uyruk"], k["ulke"]) for k in kul}
    return [k for k in paket if (k["uyruk"], k["ulke"]) not in anahtar] + list(kul)


def kural(yolcu: Yolcu, ulke: str, kurallar: list[dict]) -> dict:
    """Yolcunun uyruklarından en serbest olanının kuralı (çifte vatandaşlık)."""
    adaylar = [k for k in kurallar if k["ulke"] == ulke and k["uyruk"] in yolcu.uyruklar]
    if not adaylar:
        return {"durum": "bilinmiyor"}
    return min(adaylar, key=lambda k: ONCELIK.get(k["durum"], 2))


def gecerli_izin(yolcu: Yolcu, ulke: str, tarih: date) -> tuple[dict | None, dict | None]:
    """(geçerli izin, süresi dolmuş izin)."""
    dolmus = None
    for iz in yolcu.izinler:
        if iz["ulke"] != ulke:
            continue
        if iz.get("bitis") and date.fromisoformat(iz["bitis"]) < tarih:
            dolmus = iz
            continue
        return iz, None
    return None, dolmus


def _varsayim_uyarisi(iz: dict) -> str:
    return f"⚠ {ulke_adi(iz['ulke'])} {iz.get('tur', 'izni')} varsayım (henüz alınmadı)"


def _aktarma_ulkeleri(seg: Segment) -> tuple[list[tuple[str | None, str | None, bool]], list[str]]:
    """[(nokta, ülke, ayrı_bilet)], uyarılar. Kaynak noktayı vermemişse havayolunun merkez ülkesi varsayılır."""
    noktalar = [(a.nokta, a.ulke, a.ayri_bilet) for a in seg.aktarmalar]
    uyarilar = []
    if seg.aktarma_sayisi > len(noktalar):
        uyarilar.append("⚠ aktarma noktası kaynakta yok — satın almadan önce rotayı kontrol et")
        uc = {nokta_ulke(seg.kalkis_nokta), nokta_ulke(seg.varis_nokta)}
        for u in sorted(havayolu_ulkeleri(seg.firma) - uc):
            noktalar.append((None, u, False))
    return noktalar, uyarilar


def denetle(seg: Segment, yolcular: list[Yolcu], kurallar: list[dict] | None = None) -> TransitSonuc:
    kurallar = kurallar if kurallar is not None else kurallar_yukle()
    tarih = seg.kalkis_dt.date()
    noktalar, uyarilar = _aktarma_ulkeleri(seg)
    sonuc = TransitSonuc(True, [], list(uyarilar))

    for sira, y in enumerate(yolcular, 1):
        etiket = f"{sira}. yolcu ({y.ad})"
        for nokta, ulke, ayri in noktalar:
            if not ulke:
                # nokta tabloda yok: ülkesi bilinmiyor → kural da bilinmiyor
                kisitli = any(k["uyruk"] in y.uyruklar and k["durum"] in ("yasak", "vize_gerekli") for k in kurallar)
                if ayri:
                    sonuc.gecer = False
                    sonuc.gerekceler.append(f"{etiket}: {nokta} aktarması — ülkesi bilinmiyor, ayrı bilet: bagaj için giriş gerekebilir")
                elif kisitli:
                    # C1 #4: yasaklı ülke listesi olan uyruk için bilinmeyen ülke = güvensiz → ELE
                    sonuc.gecer = False
                    sonuc.gerekceler.append(f"{etiket}: {nokta or 'aktarma'} noktasının ülkesi bilinmiyor — "
                                            f"{'/'.join(y.uyruklar)} uyruğu için transit güvenliği doğrulanamadı")
                else:
                    sonuc.uyarilar.append(f"⚠ {nokta} aktarma noktasının ülkesi bilinmiyor — rotayı kontrol et")
                continue
            if ulke in y.uyruklar:
                continue
            yer = f"{nokta} aktarması" if nokta else f"{ulke_adi(ulke)} aktarması (havayolu merkezi)"
            k = kural(y, ulke, kurallar)
            durum = k["durum"]
            iz, dolmus = gecerli_izin(y, ulke, tarih)
            uyruk = "/".join(y.uyruklar)
            if durum == "yasak":
                sonuc.gecer = False
                sonuc.gerekceler.append(f"{etiket}: {yer} — {uyruk} uyruğu için {ulke_adi(ulke)} yasak")
                continue
            izin_gerekli = durum == "vize_gerekli" or (ayri and durum in ("airside", "bilinmiyor"))
            if izin_gerekli and not iz:
                sonuc.gecer = False
                neden = ("ayrı bilet: bagaj için ülkeye giriş gerekir" if ayri and durum != "vize_gerekli"
                         else f"{ulke_adi(ulke)} vizesi gerekir")
                if dolmus:
                    neden += f" (izin {dolmus['bitis']} tarihinde bitiyor)"
                sonuc.gerekceler.append(f"{etiket}: {yer} — {neden}")
                continue
            if iz and iz.get("durum") == "varsayim":
                sonuc.uyarilar.append(f"{etiket}: {_varsayim_uyarisi(iz)}")
            if durum == "bilinmiyor" and not iz:
                sonuc.uyarilar.append(f"⚠ {etiket}: {ulke_adi(ulke)} transit kuralını teyit et"
                                      + (f" — {k['not']}" if k.get("not") else ""))
            elif durum == "airside" and k.get("not"):
                sonuc.uyarilar.append(f"{etiket}: {k['not']}")

        # varış ülkesi: kural yasak/vize_gerekli ve geçerli izin yoksa ELER (ör. yolcunun Kore vizesi yoksa);
        # kural bilinmiyorsa yalnız uyarı
        varis = nokta_ulke(seg.varis_nokta)
        if varis and varis not in y.uyruklar:
            iz, dolmus = gecerli_izin(y, varis, seg.varis_dt.date())
            vdurum = kural(y, varis, kurallar)["durum"]
            ek = f" (izin {dolmus['bitis']} tarihinde bitiyor)" if dolmus else ""
            if iz and iz.get("durum") == "varsayim":
                sonuc.uyarilar.append(f"{etiket}: {_varsayim_uyarisi(iz)}")
            elif not iz and vdurum in ("yasak", "vize_gerekli"):
                sonuc.gecer = False
                sonuc.gerekceler.append(f"{etiket}: {ulke_adi(varis)} girişi — vize/izin yok{ek}")
            elif not iz and vdurum != "serbest":
                sonuc.uyarilar.append(f"⚠ {etiket}: {ulke_adi(varis)} girişi için izin kaydı yok{ek}")

    sonuc.uyarilar = list(dict.fromkeys(sonuc.uyarilar))
    return sonuc
