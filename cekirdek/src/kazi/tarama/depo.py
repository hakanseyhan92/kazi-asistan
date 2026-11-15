"""Tarama ve teyit için SQLite yardımcı katmanı ve şema göçü (`tarama/depo.py`).

`db.py` değiştirilmeden `teklif` tablosuna `teyit_fiyati REAL`, `teyit_url TEXT`,
`kaynaklar TEXT` sütunlarını idempotent `ALTER TABLE` göçüyle ekler ve teyit
güncelleme / 2 saatlik önbellek sorgularını sağlar.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from typing import Any

from .. import db as cekirdek_db
from .. import eslestir
from ..ayar import db_yolu as varsayilan_db_yolu
from ..modeller import (
    TZ_ISTANBUL,
    FiyatBandi,
    Katman,
    Olay,
    Site,
    Taksit,
    Teklif,
    TeyitDurumu,
    UrunKimligi,
    simdi_iso,
)

TEKLIF_EK_SUTUNLAR: tuple[tuple[str, str], ...] = (
    ("teyit_fiyati", "REAL"),
    ("teyit_url", "TEXT"),
    ("kaynaklar", "TEXT"),
)

SEMA_TARAMA = """CREATE TABLE IF NOT EXISTS tarama (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zaman TEXT NOT NULL,
    site TEXT NOT NULL,
    urun_id TEXT,
    sonuc_sayisi INTEGER NOT NULL DEFAULT 0,
    elenen_sayisi INTEGER NOT NULL DEFAULT 0,
    yontem TEXT,
    katman TEXT,
    hata TEXT,
    sure REAL,
    tur TEXT
)"""


def _iso_dt(zaman: str | None, varsayilan_tz=TZ_ISTANBUL) -> datetime | None:
    if not zaman:
        return None
    try:
        dt = datetime.fromisoformat(str(zaman).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=varsayilan_tz)
        else:
            dt = dt.astimezone(varsayilan_tz)
        return dt
    except Exception:
        return None


def _iso_norm(zaman: str | None) -> str:
    dt = _iso_dt(zaman)
    if dt is None:
        return simdi_iso()
    return dt.isoformat(timespec="seconds")


def baglanti_coz(v: Any = None) -> tuple[sqlite3.Connection, Any, bool]:
    """`VeriTabani`, `sqlite3.Connection` ya da dosya yolundan `(con, kilit, kapatilmali)` çöz."""
    if v is None:
        vt = cekirdek_db.VeriTabani(varsayilan_db_yolu())
        goc(vt.baglanti)
        return vt.baglanti, vt._kilit, True
    if hasattr(v, "baglanti"):
        kilit = getattr(v, "_kilit", None) or nullcontext()
        with kilit:
            goc(v.baglanti)
        return v.baglanti, kilit, False
    if isinstance(v, sqlite3.Connection):
        v.row_factory = sqlite3.Row
        cekirdek_db.kur(v)
        goc(v)
        return v, nullcontext(), False
    if isinstance(v, (str, Path)):
        vt = cekirdek_db.VeriTabani(Path(v))
        goc(vt.baglanti)
        return vt.baglanti, vt._kilit, True
    raise TypeError(f"Desteklenmeyen veritabanı nesnesi: {type(v)!r}")


def goc(con: sqlite3.Connection) -> None:
    """`teklif` tablosuna `teyit_fiyati` vb. sütunları ve `tarama` tablosunu ekle (idempotent)."""
    cekirdek_db.kur(con)
    con.execute(SEMA_TARAMA)
    mevcut = {satir["name"] for satir in con.execute("PRAGMA table_info(teklif)")}
    for sutun, tip in TEKLIF_EK_SUTUNLAR:
        if sutun not in mevcut:
            con.execute(f"ALTER TABLE teklif ADD COLUMN {sutun} {tip}")
    con.commit()


def kur(v: Any = None) -> None:
    """Veritabanı şemasını ve `tarama` göçlerini hazırla (idempotent)."""
    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            goc(con)
    finally:
        if kapat:
            con.close()


# ------------------------------------------------------------------ ürün yardımcıları
def _sozlukten_urun_kimligi(u: dict[str, Any]) -> UrunKimligi:
    ic = u.get("eslesme_kurallari") if isinstance(u.get("eslesme_kurallari"), dict) else {}
    kurallar: dict[str, Any] = dict(ic)
    for k in (
        "sorgular",
        "dahil",
        "haric",
        "min_fiyat",
        "max_fiyat",
        "qvl",
        "siteler",
        "sayfa",
        "satici_teyit_esigi",
        "hedef_fiyat",
        "kategori",
        "sistemde",
        "not",
    ):
        if kurallar.get(k) is None and u.get(k) is not None:
            kurallar[k] = u[k]
    alt = kurallar.get("min_fiyat")
    ust = kurallar.get("max_fiyat")
    fiyat_bandi = FiyatBandi(alt=alt, ust=ust) if (alt is not None or ust is not None) else None
    return UrunKimligi(
        id=str(u["id"]),
        kanonik_ad=str(u.get("kanonik_ad") or u.get("ad") or u["id"]),
        mpn=u.get("mpn"),
        ean=u.get("ean"),
        eslesme_kurallari=kurallar,
        fiyat_bandi=fiyat_bandi,
        aktif=bool(u.get("aktif", True)),
    )


def urun_kaydet_yoksa(v: Any, urun: UrunKimligi | dict[str, Any]) -> UrunKimligi:
    """Ürün DB'de yoksa ekler; varsa mevcut kaydı (eksik kuralları tamamlayarak) döndürür."""
    kayit = _sozlukten_urun_kimligi(urun) if isinstance(urun, dict) else urun
    hedef_f = None
    if isinstance(urun, dict):
        hedef_f = urun.get("hedef_fiyat")
    if hedef_f is None and isinstance(kayit.eslesme_kurallari, dict):
        hedef_f = kayit.eslesme_kurallari.get("hedef_fiyat")

    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            satir = con.execute(
                "SELECT * FROM urun_kimligi WHERE id = ?", (kayit.id,)
            ).fetchone()
            if satir is None:
                con.execute(
                    """INSERT INTO urun_kimligi
                       (id, tur, kanonik_ad, mpn, ean, eslesme_kurallari, fiyat_bandi,
                        olusturma, aktif)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    cekirdek_db._urun_satiri(kayit),
                )
                if hedef_f is not None:
                    con.execute(
                        "INSERT INTO izleme (urun_id, hedef_fiyat, etkin) VALUES (?, ?, 1)",
                        (kayit.id, float(hedef_f)),
                    )
                con.commit()
                return kayit
            mevcut = cekirdek_db._urun_nesne(satir)
            return _urun_kurallarini_zenginlestir(mevcut)
    finally:
        if kapat:
            con.close()


def _urun_kurallarini_zenginlestir(urun: UrunKimligi) -> UrunKimligi:
    """`eski.aktar` ile gelmiş ürünlerde eksik `sorgular`/`siteler` alanlarını `urunler.json`'dan tamamla."""
    k = dict(urun.eslesme_kurallari or {})
    if urun.fiyat_bandi is not None:
        if k.get("min_fiyat") is None and urun.fiyat_bandi.alt is not None:
            k["min_fiyat"] = urun.fiyat_bandi.alt
        if k.get("max_fiyat") is None and urun.fiyat_bandi.ust is not None:
            k["max_fiyat"] = urun.fiyat_bandi.ust
    if not k.get("sorgular"):
        try:
            eski_u = eslestir.urun_getir(urun.id or "")
        except Exception:
            eski_u = None
        if isinstance(eski_u, dict):
            for alan in ("sorgular", "siteler", "sayfa", "dahil", "haric", "min_fiyat", "max_fiyat", "qvl"):
                if k.get(alan) is None and eski_u.get(alan) is not None:
                    k[alan] = eski_u[alan]
    if not k.get("sorgular"):
        k["sorgular"] = [urun.kanonik_ad]
    return urun.model_copy(update={"eslesme_kurallari": k})


def urun_getir(v: Any, urun_id: str, otomatik_ekle: bool = True) -> UrunKimligi | None:
    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            satir = con.execute(
                "SELECT * FROM urun_kimligi WHERE id = ?", (urun_id,)
            ).fetchone()
            if satir is not None:
                return _urun_kurallarini_zenginlestir(cekirdek_db._urun_nesne(satir))
    finally:
        if kapat:
            con.close()

    if otomatik_ekle:
        try:
            eski_u = eslestir.urun_getir(urun_id)
        except Exception:
            eski_u = None
        if isinstance(eski_u, dict):
            return urun_kaydet_yoksa(v, eski_u)
    return None


def urunler_listele(
    v: Any,
    urun_idler: list[str] | None = None,
    aktif: bool = True,
    otomatik_ekle: bool = True,
) -> list[UrunKimligi]:
    if urun_idler:
        sonuc: list[UrunKimligi] = []
        for uid in urun_idler:
            u = urun_getir(v, uid, otomatik_ekle=otomatik_ekle)
            if u is not None:
                sonuc.append(u)
        return sonuc

    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            if aktif:
                satirlar = con.execute(
                    "SELECT * FROM urun_kimligi WHERE aktif = 1 ORDER BY olusturma DESC, id"
                ).fetchall()
            else:
                satirlar = con.execute(
                    "SELECT * FROM urun_kimligi ORDER BY olusturma DESC, id"
                ).fetchall()
        mevcut = [_urun_kurallarini_zenginlestir(cekirdek_db._urun_nesne(s)) for s in satirlar]
    finally:
        if kapat:
            con.close()

    if mevcut or not otomatik_ekle:
        return mevcut

    try:
        ham_liste = eslestir.urunler_yukle()
    except Exception:
        ham_liste = []
    return [urun_kaydet_yoksa(v, u) for u in ham_liste if isinstance(u, dict) and u.get("id")]


def hedef_fiyat(v: Any, urun_id: str) -> float | None:
    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            r = con.execute(
                "SELECT hedef_fiyat FROM izleme WHERE urun_id=? AND etkin=1 ORDER BY id DESC LIMIT 1",
                (urun_id,),
            ).fetchone()
            if r is not None and r[0] is not None:
                return float(r[0])
            u_satir = con.execute(
                "SELECT eslesme_kurallari FROM urun_kimligi WHERE id=?", (urun_id,)
            ).fetchone()
            if u_satir is not None:
                k = cekirdek_db._json_oku(u_satir["eslesme_kurallari"], {})
                if isinstance(k, dict) and k.get("hedef_fiyat") is not None:
                    return float(k["hedef_fiyat"])
    finally:
        if kapat:
            con.close()
    try:
        eski_u = eslestir.urun_getir(urun_id)
        if isinstance(eski_u, dict) and eski_u.get("hedef_fiyat") is not None:
            return float(eski_u["hedef_fiyat"])
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ teklif ve teyit
def _satirdan_teklif(s: sqlite3.Row) -> Teklif:
    tk = cekirdek_db._teklif_nesne(s)
    anahtarlar = set(s.keys())
    object.__setattr__(tk, "teyit_fiyati", s["teyit_fiyati"] if "teyit_fiyati" in anahtarlar else None)
    object.__setattr__(tk, "teyit_url", s["teyit_url"] if "teyit_url" in anahtarlar else None)
    object.__setattr__(tk, "kaynaklar", s["kaynaklar"] if "kaynaklar" in anahtarlar else None)
    return tk


def teklif_ekle(
    v: Any,
    teklif: Teklif,
    *,
    kaynaklar: str | None = None,
    pesin_taksit: int | None = None,
) -> int:
    """Teklifi DB'ye ekler (ürün kimliği yoksa FK hatası olmaması için oluşturur)."""
    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            u_var = con.execute(
                "SELECT 1 FROM urun_kimligi WHERE id = ?", (teklif.urun_id,)
            ).fetchone()
            if u_var is None:
                eski_u = None
                try:
                    eski_u = eslestir.urun_getir(teklif.urun_id)
                except Exception:
                    eski_u = None
                if isinstance(eski_u, dict):
                    kayit_u = _sozlukten_urun_kimligi(eski_u)
                else:
                    kayit_u = UrunKimligi(id=teklif.urun_id, kanonik_ad=teklif.urun_id)
                con.execute(
                    """INSERT INTO urun_kimligi
                       (id, tur, kanonik_ad, mpn, ean, eslesme_kurallari, fiyat_bandi,
                        olusturma, aktif)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    cekirdek_db._urun_satiri(kayit_u),
                )

            imlec = con.execute(
                """INSERT INTO teklif
                   (urun_id, site, satici, baslik, url, liste_fiyati, sepet_fiyati,
                    kupon, kargo, stok, pesin_fiyatina_taksit, gozlem_zamani,
                    kaynak_katmani, teyit_durumu, teyit_zamani, kanit_goruntusu,
                    kaynaklar)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (*cekirdek_db._teklif_satiri(teklif), kaynaklar),
            )
            tid = int(imlec.lastrowid)
            if pesin_taksit and int(pesin_taksit) > 0:
                con.execute(
                    """INSERT INTO taksit
                       (teklif_id, banka, taksit_sayisi, aylik, toplam, vade_farki_yuzde)
                       VALUES (?,?,?,?,?,?)""",
                    (tid, None, int(pesin_taksit), None, teklif.sepet_fiyati, 0.0),
                )
            con.commit()
            return tid
    finally:
        if kapat:
            con.close()


def son_teklifler(
    v: Any,
    urun_id: str,
    *,
    yalniz_son: bool = True,
    simdi: datetime | None = None,
    pencere_saat: float | None = 24.0,
) -> list[Teklif]:
    """Ürün için teklifleri (`teyit_fiyati`, `teyit_url` eklenmiş olarak) döndürür."""
    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            if yalniz_son:
                satirlar = con.execute(
                    """SELECT * FROM (
                         SELECT t.*, ROW_NUMBER() OVER (
                           PARTITION BY t.site, COALESCE(t.satici, '')
                           ORDER BY t.gozlem_zamani DESC, t.id DESC
                         ) AS _sira
                         FROM teklif t WHERE t.urun_id = ? AND t.site NOT IN (SELECT ad FROM site WHERE etkin = 0)
                       ) WHERE _sira = 1
                       ORDER BY sepet_fiyati IS NULL, sepet_fiyati ASC, site ASC, satici ASC""",
                    (urun_id,),
                ).fetchall()
            else:
                satirlar = con.execute(
                    """SELECT * FROM teklif t WHERE t.urun_id = ? AND t.site NOT IN (SELECT ad FROM site WHERE etkin = 0)
                       ORDER BY gozlem_zamani DESC, id DESC""",
                    (urun_id,),
                ).fetchall()
    finally:
        if kapat:
            con.close()

    teklifler = [_satirdan_teklif(s) for s in satirlar]
    if pencere_saat is None:
        return teklifler
    ref = _iso_dt(simdi.isoformat() if isinstance(simdi, datetime) else None) or datetime.now(TZ_ISTANBUL)
    sinir = ref - timedelta(hours=float(pencere_saat))
    sonuc: list[Teklif] = []
    for tk in teklifler:
        gz = _iso_dt(tk.gozlem_zamani, ref.tzinfo)
        if gz is not None and gz >= sinir:
            sonuc.append(tk)
    return sonuc


def teklif_teyit_guncelle(
    v: Any,
    teklif_id: int | None = None,
    *,
    urun_id: str | None = None,
    url: str | None = None,
    teyit_durumu: str | TeyitDurumu,
    teyit_zamani: str | None = None,
    teyit_fiyati: float | None = None,
    kanit_goruntusu: str | None = None,
    teyit_url: str | None = None,
    satici: str | None = None,
    stok: bool | None = None,
    pesin_taksit: int | None = None,
) -> int:
    """Bir teklif satırının teyit sonucunu, fiyatını ve kanıt görüntüsünü günceller."""
    zaman_str = _iso_norm(teyit_zamani) if teyit_zamani else simdi_iso()
    durum_str = str(teyit_durumu)

    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            hedef_id = teklif_id
            if hedef_id is None and urun_id and url:
                r = con.execute(
                    """SELECT id FROM teklif
                       WHERE urun_id = ? AND url = ?
                       ORDER BY gozlem_zamani DESC, id DESC LIMIT 1""",
                    (urun_id, url),
                ).fetchone()
                if r is not None:
                    hedef_id = int(r["id"])
            if hedef_id is None:
                return 0

            mevcut = con.execute("SELECT * FROM teklif WHERE id = ?", (hedef_id,)).fetchone()
            if mevcut is None:
                return 0

            yeni_satici = satici if satici is not None else mevcut["satici"]
            yeni_stok = (
                int(bool(stok))
                if stok is not None
                else mevcut["stok"]
            )
            yeni_pesin_bool = (
                1
                if (pesin_taksit is not None and int(pesin_taksit) > 0)
                else mevcut["pesin_fiyatina_taksit"]
            )

            imlec = con.execute(
                """UPDATE teklif
                   SET teyit_durumu = ?,
                       teyit_zamani = ?,
                       teyit_fiyati = ?,
                       kanit_goruntusu = ?,
                       teyit_url = COALESCE(?, teyit_url),
                       satici = ?,
                       stok = ?,
                       pesin_fiyatina_taksit = ?
                   WHERE id = ?""",
                (
                    durum_str,
                    zaman_str,
                    teyit_fiyati,
                    kanit_goruntusu,
                    teyit_url,
                    yeni_satici,
                    yeni_stok,
                    yeni_pesin_bool,
                    hedef_id,
                ),
            )
            if pesin_taksit and int(pesin_taksit) > 0:
                taksit_var = con.execute(
                    """SELECT 1 FROM taksit
                       WHERE teklif_id = ? AND taksit_sayisi = ? AND COALESCE(vade_farki_yuzde, 0) = 0""",
                    (hedef_id, int(pesin_taksit)),
                ).fetchone()
                if taksit_var is None:
                    con.execute(
                        """INSERT INTO taksit
                           (teklif_id, banka, taksit_sayisi, aylik, toplam, vade_farki_yuzde)
                           VALUES (?,?,?,?,?,?)""",
                        (
                            hedef_id,
                            None,
                            int(pesin_taksit),
                            None,
                            teyit_fiyati or mevcut["sepet_fiyati"],
                            0.0,
                        ),
                    )
            con.commit()
            return int(imlec.rowcount)
    finally:
        if kapat:
            con.close()


def teyit_onbellek(
    v: Any,
    urun_id: str,
    url: str,
    *,
    saat: float = 2.0,
    simdi: datetime | None = None,
    beklenen_fiyat: float | None = None,
) -> dict[str, Any] | None:
    """Aynı `(urun_id, url)` son `saat` içinde teyit edildiyse önbellek kaydını döndürür."""
    if not url:
        return None
    ref = _iso_dt(simdi.isoformat() if isinstance(simdi, datetime) else None) or datetime.now(TZ_ISTANBUL)
    sinir = ref - timedelta(hours=float(saat))

    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            satirlar = con.execute(
                """SELECT * FROM teklif
                   WHERE urun_id = ?
                     AND (url = ? OR teyit_url = ?)
                     AND teyit_zamani IS NOT NULL
                     AND teyit_durumu != 'bekliyor'
                   ORDER BY teyit_zamani DESC, id DESC
                   LIMIT 10""",
                (urun_id, url, url),
            ).fetchall()
    finally:
        if kapat:
            con.close()

    for s in satirlar:
        tz_dt = _iso_dt(s["teyit_zamani"], ref.tzinfo)
        if tz_dt is None or tz_dt < sinir:
            continue
        anahtarlar = set(s.keys())
        tf = s["teyit_fiyati"] if "teyit_fiyati" in anahtarlar else None
        sf = s["sepet_fiyati"]
        # Fiyat değiştiyse yeniden teyit gerekir (teyit teklif+fiyat çiftine aittir)
        if beklenen_fiyat is not None:
            karsilastir = tf if tf is not None else sf
            if (
                karsilastir is not None
                and s["teyit_durumu"] == str(TeyitDurumu.teyitli)
                and abs(float(beklenen_fiyat) - float(karsilastir)) >= 0.5
            ):
                continue
        return {
            "id": s["id"],
            "teyit_durumu": s["teyit_durumu"],
            "durum": s["teyit_durumu"],
            "teyit_zamani": s["teyit_zamani"],
            "teyit_fiyati": tf,
            "teyit_fiyat": tf,
            "teyit_url": s["teyit_url"] if "teyit_url" in anahtarlar else None,
            "kanit_goruntusu": s["kanit_goruntusu"],
            "satici": s["satici"],
            "stok": None if s["stok"] is None else bool(s["stok"]),
            "pesin_fiyatina_taksit": (
                None if s["pesin_fiyatina_taksit"] is None else bool(s["pesin_fiyatina_taksit"])
            ),
        }
    return None


# ------------------------------------------------------------------ site ve olay
def tarama_kaydet(
    v: Any,
    *,
    site: str,
    urun_id: str | None,
    sayi: int,
    elenen_sayisi: int = 0,
    yontem: str = "http",
    katman: str | Katman = Katman.K0,
    hata: str | None = None,
    sure: float = 0.0,
    tur: str = "ok",
    zaman: str | None = None,
) -> int:
    """Tarama denemesini `tarama` tablosuna yazar ve `site` tablosunu günceller."""
    zaman_str = _iso_norm(zaman)
    katman_enum = Katman(str(katman)) if str(katman) in ("K0", "K1", "K2", "K3") else Katman.K0
    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            imlec = con.execute(
                """INSERT INTO tarama
                   (zaman, site, urun_id, sonuc_sayisi, elenen_sayisi, yontem, katman, hata, sure, tur)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    zaman_str,
                    site,
                    urun_id,
                    int(sayi),
                    int(elenen_sayisi),
                    yontem,
                    str(katman_enum),
                    hata,
                    float(sure),
                    tur,
                ),
            )
            tid = int(imlec.lastrowid)

            mevcut_s = con.execute("SELECT * FROM site WHERE ad = ?", (site,)).fetchone()
            son_basari = mevcut_s["son_basari"] if mevcut_s else None
            son_hata = mevcut_s["son_hata"] if mevcut_s else None
            hata_turu = mevcut_s["hata_turu"] if mevcut_s else None
            etkin = bool(mevcut_s["etkin"]) if mevcut_s else True
            tercih = (
                Katman(mevcut_s["katman_tercihi"])
                if (mevcut_s and mevcut_s["katman_tercihi"] in ("K0", "K1", "K2", "K3"))
                else katman_enum
            )
            if hata is None:
                son_basari = zaman_str
                tercih = katman_enum
                hata_turu = None
            else:
                son_hata = zaman_str
                hata_turu = "captcha" if tur == "blok" else str(hata)[:120]

            site_kaydi = Site(
                ad=site,
                tur="urun",
                katman_tercihi=tercih,
                son_basari=son_basari,
                son_hata=son_hata,
                hata_turu=hata_turu,
                etkin=etkin,
            )
            con.execute(
                """INSERT INTO site
                   (ad, tur, katman_tercihi, son_basari, son_hata, hata_turu, etkin)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(ad) DO UPDATE SET
                     tur = excluded.tur,
                     katman_tercihi = excluded.katman_tercihi,
                     son_basari = excluded.son_basari,
                     son_hata = excluded.son_hata,
                     hata_turu = excluded.hata_turu,
                     etkin = excluded.etkin""",
                (
                    site_kaydi.ad,
                    site_kaydi.tur,
                    None if site_kaydi.katman_tercihi is None else str(site_kaydi.katman_tercihi),
                    site_kaydi.son_basari,
                    site_kaydi.son_hata,
                    site_kaydi.hata_turu,
                    int(bool(site_kaydi.etkin)),
                ),
            )
            con.commit()
            return tid
    finally:
        if kapat:
            con.close()


def olay_kaydet(
    v: Any,
    tur: str,
    *,
    urun_id: str | None = None,
    gorev_id: str | None = None,
    mesaj: str | None = None,
    veri: dict[str, Any] | None = None,
    zaman: str | None = None,
) -> Olay:
    olay = Olay(
        tur=tur,
        zaman=_iso_norm(zaman) if zaman else simdi_iso(),
        urun_id=urun_id,
        gorev_id=gorev_id,
        mesaj=mesaj,
        veri=veri or {},
    )
    if hasattr(v, "olay_ekle"):
        return v.olay_ekle(olay)
    con, kilit, kapat = baglanti_coz(v)
    try:
        with kilit:
            imlec = con.execute(
                """INSERT INTO olay (tur, zaman, urun_id, gorev_id, mesaj, veri)
                   VALUES (?,?,?,?,?,?)""",
                (
                    olay.tur,
                    olay.zaman,
                    olay.urun_id,
                    olay.gorev_id,
                    olay.mesaj,
                    cekirdek_db._json_yaz(olay.veri),
                ),
            )
            con.commit()
            return olay.model_copy(update={"id": int(imlec.lastrowid)})
    finally:
        if kapat:
            con.close()
