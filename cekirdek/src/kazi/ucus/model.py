"""Uçuş kaynakları için ortak yardımcılar ve Eski Ucus → Segment dönüşümü.

Saat dilimi ve ayrı bilet mantığı `kazi.eski._segment_satiri` ile birebir aynıdır:
- Havalimanı ülkesine göre saat dilimi: JP/KR +9, TR +3 (varsayılan +3).
- Aktarma noktaları `kazi.seyahat.segment.Aktarma` nesnelerine dönüştürülür.
- `ayri_bilet` bayrağı kaynak verisi veya `ayri_bilet_tahmini(firma, aktarma)` ile belirlenir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ..seyahat.segment import Aktarma, Segment, ayri_bilet_tahmini, nokta_ulke, noktalar

try:
    from ..tarayici import BotEngeli  # type: ignore[import-not-found]
except Exception:  # pragma: no cover — A1 paralel yazılırken yedek
    class BotEngeli(Exception):  # type: ignore[no-redef]
        def __init__(self, site: str, url: str = ""):
            self.site = site
            self.url = url
            super().__init__(f"{site}: bot doğrulaması/erişim engeli ({url})")

TR = timezone(timedelta(hours=3))

SEHIR_IATA = {
    "İstanbul": "IST",
    "Istanbul": "IST",
    "Tokyo": "TYO",
    "Seul": "SEL",
    "Seoul": "SEL",
    "Osaka": "OSA",
    "Ankara": "ESB",
    "İzmir": "ADB",
    "Izmir": "ADB",
}


def _ofset(kod: str) -> timezone | None:
    """Havalimanı kodundan saat dilimi — tek kaynak `eski._ofset` (bilinmeyen ülke → None, segment atlanır)."""
    from ..eski import _ofset as ofset
    return ofset(kod)


def _tz(z: str | None) -> str:
    """Saat dilimsiz yerel zaman → +03:00 ISO-8601."""
    if not z:
        return datetime.now(TR).isoformat(timespec="seconds")
    d = datetime.fromisoformat(z)
    return (d if d.tzinfo else d.replace(tzinfo=TR)).isoformat(timespec="seconds")


@dataclass
class Ucus:
    """Kaynak ayrıştırıcılarının ara çıktısı; `ucus_segment` ile `Segment`'e çevrilir."""

    kaynak: str
    bacak_id: str
    tarih: str           # YYYY-MM-DD
    fiyat_toplam: float  # TÜM yolcular için toplam (TL)
    havayolu: str
    rota: list[str]      # ["IST","DOH","NRT"]
    aktarma: int
    sure_dk: int | None = None
    kalkis_saat: str | None = None
    varis_saat: str | None = None
    ayri_bilet: bool = False
    link: str | None = None
    para: str = "TRY"
    yontem: str = "http"
    zaman: str = ""
    yolcu: int = 1
    uyarilar: list[str] = field(default_factory=list)
    kaynaklar: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.kaynak = str(self.kaynak or "").lower()
        self.bacak_id = str(self.bacak_id or "")
        self.rota = [str(k).upper() for k in (self.rota or []) if k]
        self.uyarilar = list(self.uyarilar or [])
        self.kaynaklar = list(self.kaynaklar or []) or [self.kaynak]
        if self.fiyat_toplam is not None:
            self.fiyat_toplam = float(self.fiyat_toplam)
        if self.aktarma is None:
            self.aktarma = max(len(self.rota) - 2, 0)
        self.aktarma = int(self.aktarma)
        if self.yolcu is None or int(self.yolcu) < 1:
            self.yolcu = 1
        self.yolcu = int(self.yolcu)
        self.para = (self.para or "TRY").upper()

    @property
    def kisi_basi(self) -> float:
        return self.fiyat_toplam / self.yolcu if self.yolcu else self.fiyat_toplam

    def to_segment(self, seg_id: str | None = None) -> Segment | None:
        return ucus_segment(self, seg_id=seg_id)



# Türkiye şehirlerarası otobüs firmaları (Kayak ESB↔IST'de "Metro Turizm" gibi gerçek firma adı da veriyor, 15.11)
OTOBUS_FIRMALARI = ("metro turizm", "kamil koç", "kamil koc", "pamukkale", "ulusoy", "varan", "nilüfer", "süha",
                    "lüks karadeniz", "has turizm", "efe tur", "anadolu ulaşım", "flixbus", "obilet", "kontur",
                    "uludağ turizm", "radar turizm", "ali osman ulusoy", "sözer turizm", "tokat seyahat")
_OTOBUS_RX = re.compile(r"\b(otob[üu]s|bus|coach|turizm|seyahat)\b", re.I)
_TREN_RX = re.compile(r"\b(tren|train|yht|tcdd|hızlı tren|rail)\b", re.I)


def arac_tahmini(firma: str | None) -> str:
    """Kaynak 'havayolu' alanına taşıma türünü yazıyorsa (Kayak ESB↔IST'de otobüs seferine firma yerine "Otobüs" yazar,
    15.11) aracı ona göre ata: otobus | yht | ucak."""
    f = str(firma or "")
    if _OTOBUS_RX.search(f) or any(o in f.lower() for o in OTOBUS_FIRMALARI):
        return "otobus"
    if _TREN_RX.search(f):
        return "yht"
    return "ucak"

def ucus_segment(u: Ucus, seg_id: str | None = None) -> Segment | None:
    """Eski `Ucus` kaydını `kazi.seyahat.segment.Segment` nesnesine dönüştür (`eski._segment_satiri` mantığı)."""
    rota = [str(x).upper() for x in (u.rota or []) if x]
    if len(rota) < 2:
        return None
    k_tz, v_tz = _ofset(rota[0]), _ofset(rota[-1])
    if k_tz is None or v_tz is None:
        return None
    gun = date.fromisoformat(u.tarih)
    kh, km = (int(x) for x in (u.kalkis_saat or "12:00").split(":")[:2])
    kalkis = datetime(gun.year, gun.month, gun.day, kh, km, tzinfo=k_tz)
    if u.sure_dk:
        varis = (kalkis + timedelta(minutes=int(u.sure_dk))).astimezone(v_tz)
    elif u.varis_saat:
        vh, vm = (int(x) for x in u.varis_saat.split(":")[:2])
        varis = datetime(gun.year, gun.month, gun.day, vh, vm, tzinfo=v_tz)
        if varis <= kalkis:
            varis += timedelta(days=1)
    else:
        varis = kalkis + timedelta(hours=3)
    akt = int(u.aktarma if u.aktarma is not None else max(len(rota) - 2, 0))
    ab = bool(u.ayri_bilet) or bool(ayri_bilet_tahmini(u.havayolu, akt))
    yolcu = max(int(u.yolcu or 1), 1)
    toplam = float(u.fiyat_toplam)
    aktarmalar = [Aktarma(x, ayri_bilet=ab) for x in rota[1:-1]]
    if not aktarmalar and akt > 0 and ab:
        aktarmalar = [Aktarma(None, ayri_bilet=True)]
    sid = seg_id if seg_id is not None else f"{u.kaynak}:{'>'.join(rota)}:{u.tarih}:{u.kalkis_saat or ''}"
    return Segment(
        id=sid,
        arac=arac_tahmini(u.havayolu),
        kalkis_nokta=rota[0],
        varis_nokta=rota[-1],
        kalkis=kalkis.isoformat(),
        varis=varis.isoformat(),
        kisi_basi=toplam / yolcu,
        toplam=toplam,
        aktarmalar=aktarmalar,
        aktarma_sayisi=akt,
        firma=u.havayolu,
        link=u.link,
        kaynak=u.kaynak,
        gozlem_zamani=_tz(u.zaman) if u.zaman else None,
    )


def ucuslar_segment(ucuslar: list[Ucus]) -> list[Segment]:
    """`Ucus` listesini `Segment` listesine çevirir (kişi başı fiyata göre artan)."""
    out: list[Segment] = []
    for u in ucuslar:
        s = ucus_segment(u)
        if s is not None:
            out.append(s)
    out.sort(key=lambda s: s.kisi_basi)
    return out


_SAAT_RX = re.compile(r"^\s*(\d{1,2}):(\d{2})")


def saat_metni(deger) -> str | None:
    """[16,20] / '16:20' / '2026-12-27T14:25:00' -> '16:20'."""
    if deger is None:
        return None
    if isinstance(deger, str):
        m = _SAAT_RX.search(deger)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
        m = re.search(r"T(\d{2}):(\d{2})", deger)
        if m:
            return f"{m.group(1)}:{m.group(2)}"
        return None
    if isinstance(deger, (list, tuple)) and len(deger) >= 2:
        try:
            return f"{int(deger[0]):02d}:{int(deger[1]):02d}"
        except Exception:
            return None
    return None


_TEMIZ = re.compile(r"₺|\bTL\b|\bTRY\b", re.IGNORECASE)


def tl_sayi(metin) -> float | None:
    """'119.559,02 TL' -> 119559.02 · '₺1.299,90' -> 1299.9 · '13 099 TL' -> 13099.0."""
    if metin is None:
        return None
    if isinstance(metin, (int, float)):
        return float(metin)
    s = str(metin).replace("\xa0", " ").replace(" ", " ").replace(" ", " ")
    s = _TEMIZ.sub("", s)
    s = re.sub(r"[^\d.,]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    virgul, nokta = s.rfind(","), s.rfind(".")
    if virgul >= 0 and nokta >= 0:
        if virgul > nokta:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif virgul >= 0:
        parca = s.split(",")
        if len(parca) == 2 and 1 <= len(parca[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif nokta >= 0:
        parca = s.split(".")
        if len(parca) > 2:
            s = s.replace(".", "")
        elif len(parca) == 2 and len(parca[1]) == 3 and 1 <= len(parca[0]) <= 3:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def sehir_iata(ad: str) -> str:
    if not ad:
        return ""
    s = str(ad).strip()
    if s in SEHIR_IATA:
        return SEHIR_IATA[s]
    if len(s) == 3 and s.isalpha():
        return s.upper()
    for n in noktalar().get(s, []):
        if n.get("tur") == "havalimani":
            return str(n["kod"]).upper()
    return s.upper()


def bacak_bilgi(bacak) -> dict:
    """`dict` ya da `kazi.seyahat.plan.Bacak` nesnesini kaynakların beklediği sözlüğe çevir."""
    if isinstance(bacak, dict):
        kalkis = sehir_iata(bacak.get("kalkis") or bacak.get("kalkis_sehir") or "")
        varis = sehir_iata(bacak.get("varis") or bacak.get("varis_sehir") or "")
        yolcu = bacak.get("yolcu")
        if yolcu is None and bacak.get("yolcular"):
            yolcu = len(bacak["yolcular"])
        yolcu = max(int(yolcu or 1), 1)
        bid = bacak.get("id") or f"{kalkis}-{varis}"
        return {**bacak, "id": bid, "kalkis": kalkis, "varis": varis, "yolcu": yolcu}
    kalkis = sehir_iata(getattr(bacak, "kalkis_sehir", "") or getattr(bacak, "kalkis", ""))
    varis = sehir_iata(getattr(bacak, "varis_sehir", "") or getattr(bacak, "varis", ""))
    yolcular = getattr(bacak, "yolcular", None) or []
    yolcu = max(len(yolcular), 1)
    bid = getattr(bacak, "id", None) or f"{kalkis.lower()}_{varis.lower()}"
    return {
        "id": bid,
        "kalkis": kalkis,
        "varis": varis,
        "yolcu": yolcu,
        "en_erken": getattr(bacak, "en_erken", None),
        "en_gec": getattr(bacak, "en_gec", None),
        "yolcular": yolcular,
    }
