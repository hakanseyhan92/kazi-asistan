"""Google Flights kaynağı — `fast_flights` 3.x + kendi ayrıştırıcı.

Ayrıştırma mantığı eski `ucus/kaynaklar/google.py` ile birebir aynıdır;
yalnız çıktı `kazi.seyahat.segment.Segment` nesneleridir.
"""
from __future__ import annotations

import json
import re
import urllib.parse

from ...seyahat.segment import Segment
from ..model import Ucus, bacak_bilgi, saat_metni, ucus_segment, ucuslar_segment

try:
    import fast_flights as ff  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — paket yoksa sorgula() çağrısında yükseltilir
    ff = None

METRO_HAVALIMANLARI = {
    "TYO": ("NRT", "HND"),
    "SEL": ("ICN", "GMP"),
    "OSA": ("KIX", "ITM"),
    "BJS": ("PEK", "PKX"),
    "IST": ("IST",),
}

KAYNAK = "google"


def havalimanlari(kod: str) -> tuple[str, ...]:
    """Metro kodunu havalimanı kodlarına aç; havalimanı kodunu olduğu gibi döndür."""
    k = str(kod or "").upper().strip()
    return METRO_HAVALIMANLARI.get(k, (k,))


def link_olustur(kalkis: str, varis: str, tarih: str) -> str:
    q = urllib.parse.quote(f"Flights from {kalkis} to {varis} on {tarih}")
    return f"https://www.google.com/travel/flights?q={q}&curr=TRY&hl=tr"


# ---------------------------------------------------------------- ayrıştırıcı
def _payload(html: str) -> list:
    metin = None
    try:
        from selectolax.lexbor import LexborHTMLParser  # type: ignore[import-not-found]

        p = LexborHTMLParser(html)
        s = p.css_first(r"script.ds\:1")
        if s is not None:
            metin = s.text()
    except ImportError:
        m = re.search(
            r'<script[^>]*\bclass=["\'][^"\']*\bds:1\b[^"\']*["\'][^>]*>(.*?)</script>',
            html or "",
            re.S,
        )
        if m:
            metin = m.group(1)
    if not metin or "data:" not in metin:
        raise RuntimeError("ds:1 yok (blok/boş sonuç)")
    data = metin.split("data:", 1)[1].rsplit(",", 1)[0]
    return json.loads(data)


def _tarih_dizisi(deger) -> str | None:
    if isinstance(deger, (list, tuple)) and len(deger) >= 3:
        try:
            return f"{int(deger[0]):04d}-{int(deger[1]):02d}-{int(deger[2]):02d}"
        except Exception:
            return None
    return None


def ayristir_sozluk(html: str) -> list[dict]:
    """Google Flights HTML'ini uçuş sözlüklerine çevir (fiyata göre artan)."""
    payload = _payload(html)
    adlar = {}
    try:
        adlar = {c: n for c, n in payload[7][1][1]}
    except Exception:
        pass

    try:
        kalemler = payload[3][0]
    except Exception:
        return []
    if not kalemler:
        return []

    out: list[dict] = []
    for k in kalemler:
        try:
            u = k[0]
            fiyat = float(k[1][0][1])
        except Exception:
            continue

        bacaklar = []
        for sf in u[2] or []:
            try:
                bacaklar.append({
                    "kalkis": sf[3], "varis": sf[6],
                    "kalkis_saat": saat_metni(sf[8]), "varis_saat": saat_metni(sf[10]),
                    "sure": sf[11],
                })
            except Exception:
                continue
        if not bacaklar:
            continue

        rota = [bacaklar[0]["kalkis"]] + [b["varis"] for b in bacaklar]
        kodlar = []
        for c in (u[1] or []):
            ad = adlar.get(c, c)
            if ad not in kodlar:
                kodlar.append(ad)

        # üst düzey alanlar: [3]=kalkış, [4]=kalkış tarihi, [5]=kalkış saati,
        # [6]=varış, [7]=varış tarihi, [8]=varış saati, [9]=süre dk, [10]=aktarma
        sure = u[9] if isinstance(u[9], int) else sum(b["sure"] or 0 for b in bacaklar)
        aktarma = len(rota) - 2 if len(rota) >= 2 else 0

        out.append({
            "fiyat": fiyat,
            "havayolu": ", ".join(kodlar),
            "aktarma": aktarma,
            "rota": rota,
            "tarih": _tarih_dizisi(u[4] if len(u) > 4 else None),
            "kalkis_saat": saat_metni(u[5]) or bacaklar[0]["kalkis_saat"],
            "varis_saat": saat_metni(u[8]) or bacaklar[-1]["varis_saat"],
            "sure_dk": sure,
        })

    out.sort(key=lambda f: f["fiyat"])
    return out


def ayristir(html: str, bacak_id: str = "", tarih: str | None = None,
             yolcu: int = 1) -> list[Segment]:
    """Google Flights HTML'ini `Segment` listesine çevir (kişi başı fiyata göre artan)."""
    sozlukler = ayristir_sozluk(html)
    ucuslar: list[Ucus] = []
    for s in sozlukler:
        rota = s["rota"]
        t_str = tarih or s.get("tarih") or "2026-12-27"
        ucuslar.append(Ucus(
            kaynak=KAYNAK,
            bacak_id=bacak_id,
            tarih=t_str,
            fiyat_toplam=s["fiyat"],
            havayolu=s["havayolu"],
            rota=rota,
            aktarma=s["aktarma"],
            sure_dk=s["sure_dk"],
            kalkis_saat=s["kalkis_saat"],
            varis_saat=s["varis_saat"],
            link=link_olustur(rota[0], rota[-1], t_str),
            para="TRY",
            yontem="http",
            yolcu=yolcu,
        ))
    return ucuslar_segment(ucuslar)


# ---------------------------------------------------------------- sorgulama
def sorgula(kalkis: str, varis: str, tarih: str, yolcu: int = 1) -> list[Segment]:
    """Tek havalimanı çifti için Google Flights sorgusu."""
    _ff = ff
    if _ff is None:
        import fast_flights as _ff  # type: ignore[import-not-found]
    q = _ff.create_query(
        flights=[_ff.FlightQuery(date=tarih, from_airport=kalkis, to_airport=varis)],
        trip="one-way", seat="economy",
        passengers=_ff.Passengers(adults=yolcu), currency="TRY",
    )
    html = _ff.fetch_flights_html(q)
    sonuc = ayristir(html, f"{kalkis}-{varis}", tarih, yolcu)
    lnk = link_olustur(kalkis, varis, tarih)
    for s in sonuc:
        s.link = lnk
    return sonuc


def ara(t, bacak, tarih: str) -> list[Segment]:
    """Bacak için tüm havalimanı kombinasyonlarını tara (SARTNAME §3)."""
    b = bacak_bilgi(bacak)
    bacak_id = b["id"]
    yolcu = b["yolcu"]
    segmentler: list[Segment] = []
    gorulen: set[tuple] = set()

    for kalkis in havalimanlari(b.get("kalkis")):
        for varis in havalimanlari(b.get("varis")):
            if t is not None:
                try:
                    t.hiz_sinirla("google.flights")
                except Exception:
                    pass
            for item in sorgula(kalkis, varis, tarih, yolcu):
                if isinstance(item, dict):
                    seg = ucus_segment(Ucus(
                        kaynak=KAYNAK, bacak_id=bacak_id, tarih=tarih,
                        fiyat_toplam=item["fiyat"], havayolu=item["havayolu"],
                        rota=item["rota"], aktarma=item["aktarma"],
                        sure_dk=item.get("sure_dk"), kalkis_saat=item.get("kalkis_saat"),
                        varis_saat=item.get("varis_saat"),
                        link=item.get("link") or link_olustur(kalkis, varis, tarih),
                        para="TRY", yontem="http", yolcu=yolcu,
                    ))
                else:
                    seg = item
                if seg is None:
                    continue
                anahtar = (
                    seg.toplam,
                    seg.kalkis_nokta,
                    seg.varis_nokta,
                    tuple(a.nokta for a in seg.aktarmalar),
                    seg.kalkis,
                    seg.varis,
                    seg.firma,
                )
                if anahtar in gorulen:
                    continue
                gorulen.add(anahtar)
                segmentler.append(seg)

    segmentler.sort(key=lambda s: s.kisi_basi)
    return segmentler
