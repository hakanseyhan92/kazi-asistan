"""`segment_teklifi` tablosu ve uçuş segmenti depolama (SARTNAME §13.6).

- `kur(v)` : `CREATE TABLE IF NOT EXISTS segment_teklifi ...`
- `kaydet(segmentler, bacak_id)` (veya `kaydet(v, segmentler, bacak_id)`): segmentleri tabloya yazar.
- `son(bacak_id, saat=36)` (veya `son(v, bacak_id, saat=36)`): aynı uçuşun en yeni gözlemini döndürür.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from ..seyahat.segment import Aktarma, Segment
from .model import TR, _tz

SEMA_SEGMENT_TEKLIFI = (
    """CREATE TABLE IF NOT EXISTS segment_teklifi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seg_id TEXT,
        bacak_id TEXT NOT NULL,
        arac TEXT NOT NULL DEFAULT 'ucak',
        kalkis_nokta TEXT NOT NULL,
        varis_nokta TEXT NOT NULL,
        kalkis TEXT NOT NULL,
        varis TEXT NOT NULL,
        firma TEXT,
        aktarmalar TEXT NOT NULL DEFAULT '[]',
        aktarma_sayisi INTEGER NOT NULL DEFAULT 0,
        sure_dk INTEGER,
        bagaj TEXT,
        kisi_basi REAL NOT NULL,
        toplam REAL,
        kaynak TEXT,
        link TEXT,
        dis_hat INTEGER,
        gozlem_zamani TEXT NOT NULL,
        elenme_gerekcesi TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_segment_teklifi_bacak_zaman ON segment_teklifi(bacak_id, gozlem_zamani)",
)

_varsayilan_hedef: object | None = None


def _db_nesnesi_mi(x: object) -> bool:
    return (
        isinstance(x, sqlite3.Connection)
        or hasattr(x, "baglanti")
        or isinstance(x, Depo)
        or isinstance(x, Path)
    )


def _hedef_coz(v: object | None = None) -> tuple[sqlite3.Connection, object, bool]:
    """(sqlite3.Connection, kilit_baglami, kapatilmali_mi)."""
    global _varsayilan_hedef
    hedef = v if v is not None else _varsayilan_hedef
    if hedef is None:
        con = sqlite3.connect(":memory:", check_same_thread=False)
        con.row_factory = sqlite3.Row
        _varsayilan_hedef = con
        hedef = con
    elif v is not None:
        _varsayilan_hedef = v

    if isinstance(hedef, Depo):
        return _hedef_coz(hedef.v)
    if hasattr(hedef, "baglanti"):
        kilit = getattr(hedef, "_kilit", None) or nullcontext()
        return hedef.baglanti, kilit, False
    if isinstance(hedef, sqlite3.Connection):
        hedef.row_factory = sqlite3.Row
        return hedef, nullcontext(), False
    if isinstance(hedef, (str, Path)):
        p = Path(hedef)
        p.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(p), check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con, nullcontext(), True
    raise TypeError(f"Geçersiz veritabanı hedefi: {type(hedef)!r}")


def kur(v: object | None = None) -> None:
    """`segment_teklifi` tablosunu oluştur (idempotent)."""
    con, kilit, kapat = _hedef_coz(v)
    try:
        with kilit:
            for sql in SEMA_SEGMENT_TEKLIFI:
                con.execute(sql)
            con.commit()
    finally:
        if kapat:
            con.close()


def _aktarmalar_json(aktarmalar: list[Aktarma]) -> str:
    return json.dumps(
        [
            {
                "nokta": a.nokta,
                "ulke": a.ulke,
                "sure_dk": a.sure_dk,
                "ayri_bilet": bool(a.ayri_bilet),
            }
            for a in (aktarmalar or [])
        ],
        ensure_ascii=False,
    )


def _aktarmalar_oku(metin: str | None) -> list[Aktarma]:
    if not metin:
        return []
    try:
        veri = json.loads(metin)
    except Exception:
        return []
    out: list[Aktarma] = []
    for d in veri or []:
        if isinstance(d, dict):
            out.append(
                Aktarma(
                    nokta=d.get("nokta"),
                    ulke=d.get("ulke"),
                    sure_dk=d.get("sure_dk"),
                    ayri_bilet=bool(d.get("ayri_bilet", False)),
                )
            )
    return out


def _satir_segment(r: sqlite3.Row) -> Segment:
    aktarmalar = _aktarmalar_oku(r["aktarmalar"])
    akt_say = r["aktarma_sayisi"]
    return Segment(
        id=r["seg_id"] or f"u{r['id']}",
        arac=r["arac"] or "ucak",
        kalkis_nokta=r["kalkis_nokta"],
        varis_nokta=r["varis_nokta"],
        kalkis=r["kalkis"],
        varis=r["varis"],
        kisi_basi=float(r["kisi_basi"]),
        toplam=None if r["toplam"] is None else float(r["toplam"]),
        aktarmalar=aktarmalar,
        aktarma_sayisi=len(aktarmalar) if akt_say is None else int(akt_say),
        firma=r["firma"],
        link=r["link"],
        kaynak=r["kaynak"],
        dis_hat=None if r["dis_hat"] is None else bool(r["dis_hat"]),
        bagaj=r["bagaj"],
        gozlem_zamani=r["gozlem_zamani"],
    )


def _ucus_anahtari(s: Segment) -> tuple:
    """Aynı uçuşun farklı gözlemlerini gruplamak için kimlik anahtarı."""
    return (
        s.arac,
        (s.firma or "").strip().lower(),
        s.kalkis_nokta,
        s.varis_nokta,
        tuple(a.nokta for a in s.aktarmalar),
        int(s.aktarma_sayisi or 0),
        s.kalkis,
        s.varis,
    )


def kaydet(*args, **kwargs) -> int:
    """Segment listesini `segment_teklifi` tablosuna kaydet; yazılan satır sayısını döndür.

    Desteklenen çağrı biçimleri:
    - `kaydet(segmentler, bacak_id, v=None)`
    - `kaydet(v, segmentler, bacak_id)`
    """
    v = kwargs.pop("v", None) or kwargs.pop("con", None)
    elenme_haritasi: dict[str, str] = kwargs.pop("elenme_gerekceleri", None) or {}

    if len(args) == 3 and _db_nesnesi_mi(args[0]):
        v, segmentler, bacak_id = args
    elif len(args) == 2:
        segmentler, bacak_id = args
    else:
        raise TypeError("kaydet(segmentler, bacak_id) veya kaydet(v, segmentler, bacak_id) bekleniyor")

    con, kilit, kapat = _hedef_coz(v)
    try:
        with kilit:
            for sql in SEMA_SEGMENT_TEKLIFI:
                con.execute(sql)
            simdi = datetime.now(TR).isoformat(timespec="seconds")
            n = 0
            for s in segmentler:
                gz = _tz(s.gozlem_zamani) if s.gozlem_zamani else simdi
                s.gozlem_zamani = gz
                toplam = s.toplam if s.toplam is not None else s.kisi_basi
                gerekce = elenme_haritasi.get(s.id)
                imlec = con.execute(
                    """INSERT INTO segment_teklifi (
                           seg_id, bacak_id, arac, kalkis_nokta, varis_nokta,
                           kalkis, varis, firma, aktarmalar, aktarma_sayisi,
                           sure_dk, bagaj, kisi_basi, toplam, kaynak, link,
                           dis_hat, gozlem_zamani, elenme_gerekcesi
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        s.id or None,
                        str(bacak_id),
                        s.arac or "ucak",
                        s.kalkis_nokta,
                        s.varis_nokta,
                        s.kalkis,
                        s.varis,
                        s.firma,
                        _aktarmalar_json(s.aktarmalar),
                        int(s.aktarma_sayisi or 0),
                        int(s.sure_dk),
                        s.bagaj,
                        float(s.kisi_basi),
                        float(toplam),
                        s.kaynak,
                        s.link,
                        None if s.dis_hat is None else int(bool(s.dis_hat)),
                        gz,
                        gerekce,
                    ),
                )
                if not s.id:
                    s.id = f"u{imlec.lastrowid}"
                n += 1
            con.commit()
            return n
    finally:
        if kapat:
            con.close()


def son(*args, **kwargs) -> list[Segment]:
    """Verilen `bacak_id` için son `saat` saatteki gözlemlerden her uçuşun EN YENİ gözlemini döndür.

    Desteklenen çağrı biçimleri:
    - `son(bacak_id, saat=36, v=None)`
    - `son(v, bacak_id, saat=36)`
    """
    v = kwargs.pop("v", None) or kwargs.pop("con", None)
    saat = kwargs.pop("saat", 36)

    if len(args) >= 2 and _db_nesnesi_mi(args[0]):
        v = args[0]
        bacak_id = args[1]
        if len(args) >= 3:
            saat = args[2]
    elif len(args) >= 1:
        bacak_id = args[0]
        if len(args) >= 2:
            saat = args[1]
    else:
        raise TypeError("son(bacak_id, saat=36) veya son(v, bacak_id, saat=36) bekleniyor")

    con, kilit, kapat = _hedef_coz(v)
    try:
        with kilit:
            for sql in SEMA_SEGMENT_TEKLIFI:
                con.execute(sql)
            satirlar = con.execute(
                """SELECT * FROM segment_teklifi
                   WHERE bacak_id = ?
                   ORDER BY gozlem_zamani DESC, id DESC""",
                (str(bacak_id),),
            ).fetchall()
    finally:
        if kapat:
            con.close()

    sinir = datetime.now(TR) - timedelta(hours=float(saat))
    tekil: dict[tuple, Segment] = {}
    for r in satirlar:
        try:
            gz_dt = datetime.fromisoformat(_tz(r["gozlem_zamani"]))
        except Exception:
            gz_dt = datetime.now(TR)
        if gz_dt < sinir:
            continue
        s = _satir_segment(r)
        anahtar = _ucus_anahtari(s)
        if anahtar not in tekil:
            tekil[anahtar] = s  # satırlar en yeniden eskiye sıralı → en yeni gözlem kalır
    return sorted(tekil.values(), key=lambda x: x.kisi_basi)


class Depo:
    """Nesne yönelimli sarmalayıcı (`Depo(v).kaydet(...)`, `Depo(v).son(...)`)."""

    def __init__(self, v: object | None = None) -> None:
        self.v = v
        kur(v)

    def kaydet(self, segmentler: Iterable[Segment], bacak_id: str) -> int:
        return kaydet(self.v, list(segmentler), bacak_id)

    def son(self, bacak_id: str, saat: float = 36) -> list[Segment]:
        return son(self.v, bacak_id, saat=saat)
