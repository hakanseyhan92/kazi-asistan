"""SQLite depolama (SARTNAME §6): WAL, foreign_keys=ON, ? parametreli sorgular."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from .modeller import (
    Katman,
    Olay,
    Site,
    Taksit,
    Teklif,
    TeyitDurumu,
    UrunKimligi,
    simdi_iso,
)

SEMA_SURUMU = 1

SEMALAR: list[str] = [
    """CREATE TABLE IF NOT EXISTS surum (
        k INTEGER PRIMARY KEY CHECK (k = 1),
        surum INTEGER NOT NULL,
        guncelleme TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS urun_kimligi (
        id TEXT PRIMARY KEY,
        tur TEXT NOT NULL,
        kanonik_ad TEXT NOT NULL,
        mpn TEXT,
        ean TEXT,
        eslesme_kurallari TEXT NOT NULL DEFAULT '{}',
        fiyat_bandi TEXT,
        olusturma TEXT NOT NULL,
        aktif INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS site (
        ad TEXT PRIMARY KEY,
        tur TEXT,
        katman_tercihi TEXT,
        son_basari TEXT,
        son_hata TEXT,
        hata_turu TEXT,
        etkin INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS teklif (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        urun_id TEXT NOT NULL REFERENCES urun_kimligi(id) ON DELETE CASCADE,
        site TEXT NOT NULL,
        satici TEXT,
        baslik TEXT,
        url TEXT,
        liste_fiyati REAL,
        sepet_fiyati REAL,
        kupon TEXT,
        kargo REAL,
        stok INTEGER,
        pesin_fiyatina_taksit INTEGER,
        gozlem_zamani TEXT NOT NULL,
        kaynak_katmani TEXT,
        teyit_durumu TEXT NOT NULL DEFAULT 'bekliyor',
        teyit_zamani TEXT,
        kanit_goruntusu TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS taksit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        teklif_id INTEGER NOT NULL REFERENCES teklif(id) ON DELETE CASCADE,
        banka TEXT,
        taksit_sayisi INTEGER NOT NULL,
        aylik REAL,
        toplam REAL,
        vade_farki_yuzde REAL
    )""",
    """CREATE TABLE IF NOT EXISTS ucus_teklifi (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bacak TEXT NOT NULL,
        tarih TEXT NOT NULL,
        havayolu TEXT,
        aktarmalar TEXT NOT NULL DEFAULT '[]',
        sure_dk INTEGER,
        bagaj TEXT,
        fiyat_kisi REAL,
        fiyat_toplam REAL,
        kaynak TEXT,
        link TEXT,
        gozlem_zamani TEXT NOT NULL
    )""",
    # kullanıcının Takip Listesi'nde işaretlediği "toplama dahil" ürünler (14.11). eski.aktar bu tabloyu SİLMEZ.
    """CREATE TABLE IF NOT EXISTS toplam_secimi (
        urun_id TEXT PRIMARY KEY,
        secili INTEGER NOT NULL
    )""",
    # takipten çıkarılan ürünler ve kullanıcının alarm fiyatları (14.11) — eski.aktar bunları SİLMEZ
    """CREATE TABLE IF NOT EXISTS takip_disi (
        urun_id TEXT PRIMARY KEY,
        zaman TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS alarm (
        urun_id TEXT PRIMARY KEY,
        fiyat REAL NOT NULL,
        guncelleme TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS alarm_gonderim (
        anahtar TEXT PRIMARY KEY,
        zaman TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS izleme (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        urun_id TEXT NOT NULL REFERENCES urun_kimligi(id) ON DELETE CASCADE,
        siklik TEXT NOT NULL DEFAULT '6s',
        hedef_fiyat REAL,
        alarm_kanallari TEXT NOT NULL DEFAULT '[]',
        etkin INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS olay (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tur TEXT NOT NULL,
        zaman TEXT NOT NULL,
        urun_id TEXT,
        gorev_id TEXT,
        mesaj TEXT,
        veri TEXT NOT NULL DEFAULT '{}'
    )""",
    "CREATE INDEX IF NOT EXISTS ix_teklif_urun_zaman ON teklif(urun_id, gozlem_zamani)",
    "CREATE INDEX IF NOT EXISTS ix_teklif_site_satici ON teklif(urun_id, site, satici, gozlem_zamani)",
    "CREATE INDEX IF NOT EXISTS ix_taksit_teklif ON taksit(teklif_id)",
    "CREATE INDEX IF NOT EXISTS ix_olay_zaman ON olay(zaman)",
]


def _json_yaz(deger: Any) -> str:
    return json.dumps(deger, ensure_ascii=False, sort_keys=True)


def _json_oku(metin: str | None, yedek: Any) -> Any:
    if not metin:
        return yedek
    try:
        return json.loads(metin)
    except (TypeError, ValueError):
        return yedek


def baglan(yol: Path | str) -> sqlite3.Connection:
    """Bağlantı aç: WAL, foreign_keys=ON, Row fabrikası."""
    yol = Path(yol)
    yol.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(yol), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def kur(con: sqlite3.Connection) -> None:
    """Şemayı oluştur ve sürümü yaz. Idempotenttir."""
    for sema in SEMALAR:
        con.execute(sema)
    goc(con)
    con.commit()


def goc(con: sqlite3.Connection) -> None:
    """Basit sürüm göçü: sürüm satırını SEMA_SURUMU'na getir (idempotent)."""
    satir = con.execute("SELECT surum FROM surum WHERE k = 1").fetchone()
    if satir is None:
        con.execute(
            "INSERT INTO surum (k, surum, guncelleme) VALUES (1, ?, ?)",
            (SEMA_SURUMU, simdi_iso()),
        )
        return
    if int(satir["surum"]) < SEMA_SURUMU:
        con.execute(
            "UPDATE surum SET surum = ?, guncelleme = ? WHERE k = 1",
            (SEMA_SURUMU, simdi_iso()),
        )


def surum_oku(con: sqlite3.Connection) -> int:
    satir = con.execute("SELECT surum FROM surum WHERE k = 1").fetchone()
    return int(satir["surum"]) if satir else 0


def _urun_satiri(k: UrunKimligi) -> tuple:
    return (
        k.id,
        str(k.tur),
        k.kanonik_ad,
        k.mpn,
        k.ean,
        _json_yaz(k.eslesme_kurallari),
        None if k.fiyat_bandi is None else _json_yaz(k.fiyat_bandi.model_dump()),
        k.olusturma,
        int(bool(k.aktif)),
    )


def _urun_nesne(s: sqlite3.Row) -> UrunKimligi:
    return UrunKimligi(
        id=s["id"],
        tur=s["tur"],
        kanonik_ad=s["kanonik_ad"],
        mpn=s["mpn"],
        ean=s["ean"],
        eslesme_kurallari=_json_oku(s["eslesme_kurallari"], {}),
        fiyat_bandi=_json_oku(s["fiyat_bandi"], None),
        olusturma=s["olusturma"],
        aktif=bool(s["aktif"]),
    )


def _teklif_satiri(t: Teklif) -> tuple:
    return (
        t.urun_id,
        t.site,
        t.satici,
        t.baslik,
        t.url,
        t.liste_fiyati,
        t.sepet_fiyati,
        t.kupon,
        t.kargo,
        None if t.stok is None else int(bool(t.stok)),
        None if t.pesin_fiyatina_taksit is None else int(bool(t.pesin_fiyatina_taksit)),
        t.gozlem_zamani,
        None if t.kaynak_katmani is None else str(t.kaynak_katmani),
        str(t.teyit_durumu),
        t.teyit_zamani,
        t.kanit_goruntusu,
    )


def _teklif_nesne(s: sqlite3.Row) -> Teklif:
    return Teklif(
        id=s["id"],
        urun_id=s["urun_id"],
        site=s["site"],
        satici=s["satici"],
        baslik=s["baslik"],
        url=s["url"],
        liste_fiyati=s["liste_fiyati"],
        sepet_fiyati=s["sepet_fiyati"],
        kupon=s["kupon"],
        kargo=s["kargo"],
        stok=None if s["stok"] is None else bool(s["stok"]),
        pesin_fiyatina_taksit=(
            None if s["pesin_fiyatina_taksit"] is None else bool(s["pesin_fiyatina_taksit"])
        ),
        gozlem_zamani=s["gozlem_zamani"],
        kaynak_katmani=s["kaynak_katmani"],
        teyit_durumu=s["teyit_durumu"],
        teyit_zamani=s["teyit_zamani"],
        kanit_goruntusu=s["kanit_goruntusu"],
    )


class VeriTabani:
    """Tek bağlantılı, kilitli SQLite sarmalayıcı."""

    def __init__(self, yol: Path | str) -> None:
        self.yol = Path(yol)
        self._kilit = threading.RLock()
        self._toplu = False
        self._con = baglan(self.yol)
        with self._kilit:
            kur(self._con)

    def kapat(self) -> None:
        with self._kilit:
            self._con.close()

    def _isle(self) -> None:
        if not self._toplu:
            self._con.commit()

    @contextmanager
    def toplu(self):
        """Tek işlem (transaction): içerideki tüm yazımlar sonda BİR KEZ işlenir, hata olursa hepsi geri alınır.
        Okuyucular (WAL) ya eski ya yeni hâli görür, yarım hâli asla (C1 #1)."""
        with self._kilit:
            self._toplu = True
            try:
                yield self
                self._con.commit()
            except BaseException:
                self._con.rollback()
                raise
            finally:
                self._toplu = False

    @property
    def baglanti(self) -> sqlite3.Connection:
        return self._con

    # ---- toplam seçimi (Takip Listesi kutucukları) ----
    def toplam_secimleri(self) -> dict[str, bool]:
        with self._kilit:
            return {r[0]: bool(r[1]) for r in self._con.execute("SELECT urun_id, secili FROM toplam_secimi")}

    def toplam_sec(self, secimler: dict[str, bool]) -> None:
        with self._kilit:
            self._con.executemany("INSERT INTO toplam_secimi (urun_id, secili) VALUES (?, ?) "
                                  "ON CONFLICT(urun_id) DO UPDATE SET secili = excluded.secili",
                                  [(k, int(bool(v))) for k, v in secimler.items()])
            self._isle()

    # ---- takipten çıkarma / alarm fiyatı ----
    def takip_disi(self) -> dict[str, str]:
        with self._kilit:
            return {r[0]: r[1] for r in self._con.execute("SELECT urun_id, zaman FROM takip_disi")}

    def takipten_cikar(self, urun_ids: list[str], zaman: str) -> None:
        with self._kilit:
            self._con.executemany("INSERT OR REPLACE INTO takip_disi (urun_id, zaman) VALUES (?, ?)",
                                  [(i, zaman) for i in urun_ids])
            self._isle()

    def takibe_al(self, urun_ids: list[str]) -> None:
        with self._kilit:
            self._con.executemany("DELETE FROM takip_disi WHERE urun_id = ?", [(i,) for i in urun_ids])
            self._isle()

    def alarmlar(self) -> dict[str, float]:
        with self._kilit:
            return {r[0]: r[1] for r in self._con.execute("SELECT urun_id, fiyat FROM alarm")}

    def alarm_yaz(self, urun_id: str, fiyat: float | None, zaman: str) -> None:
        with self._kilit:
            if fiyat is None:
                self._con.execute("DELETE FROM alarm WHERE urun_id = ?", (urun_id,))
            else:
                self._con.execute("INSERT INTO alarm (urun_id, fiyat, guncelleme) VALUES (?, ?, ?) "
                                  "ON CONFLICT(urun_id) DO UPDATE SET fiyat=excluded.fiyat, guncelleme=excluded.guncelleme",
                                  (urun_id, float(fiyat), zaman))
            self._isle()

    # ---- ürün kimliği ----
    def urun_ekle(self, urun: UrunKimligi) -> str:
        kayit = urun.model_copy(deep=True)
        if not kayit.id:
            kayit.id = uuid4().hex
        with self._kilit:
            self._con.execute(
                """INSERT INTO urun_kimligi
                   (id, tur, kanonik_ad, mpn, ean, eslesme_kurallari, fiyat_bandi,
                    olusturma, aktif)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                _urun_satiri(kayit),
            )
            self._isle()
        return kayit.id

    def urunler_listele(self, aktif: bool | None = None) -> list[UrunKimligi]:
        with self._kilit:
            if aktif is None:
                satirlar = self._con.execute(
                    "SELECT * FROM urun_kimligi ORDER BY olusturma DESC, id"
                ).fetchall()
            else:
                satirlar = self._con.execute(
                    "SELECT * FROM urun_kimligi WHERE aktif = ? ORDER BY olusturma DESC, id",
                    (int(bool(aktif)),),
                ).fetchall()
        return [_urun_nesne(s) for s in satirlar]

    def urun_getir(self, urun_id: str) -> UrunKimligi | None:
        with self._kilit:
            satir = self._con.execute(
                "SELECT * FROM urun_kimligi WHERE id = ?", (urun_id,)
            ).fetchone()
        return _urun_nesne(satir) if satir else None

    # ---- site ----
    def site_kaydet(self, site: Site) -> str:
        with self._kilit:
            self._con.execute(
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
                    site.ad,
                    site.tur,
                    None if site.katman_tercihi is None else str(site.katman_tercihi),
                    site.son_basari,
                    site.son_hata,
                    site.hata_turu,
                    int(bool(site.etkin)),
                ),
            )
            self._isle()
        return site.ad

    def kapali_siteler(self) -> set[str]:
        """Kullanıcının Ayarlar'dan aramadan çıkardığı siteler (15.11): taranmaz, toplayıcı üzerinden gelen
        teklifleri de elenir, kayıtlı teklifleri ekranlara/raporlara girmez (silinmez; açınca geri gelir)."""
        with self._kilit:
            return {r[0] for r in self._con.execute("SELECT ad FROM site WHERE etkin = 0")}

    def site_etkin_yaz(self, ad: str, etkin: bool) -> None:
        with self._kilit:
            self._con.execute(
                "INSERT INTO site (ad, tur, etkin) VALUES (?, 'urun', ?) ON CONFLICT(ad) DO UPDATE SET etkin = excluded.etkin",
                (ad, int(bool(etkin))))
            self._isle()

    def siteler_listele(self, etkin: bool | None = None) -> list[Site]:
        with self._kilit:
            if etkin is None:
                satirlar = self._con.execute("SELECT * FROM site ORDER BY ad").fetchall()
            else:
                satirlar = self._con.execute(
                    "SELECT * FROM site WHERE etkin = ? ORDER BY ad", (int(bool(etkin)),)
                ).fetchall()
        return [
            Site(
                ad=s["ad"],
                tur=s["tur"],
                katman_tercihi=s["katman_tercihi"],
                son_basari=s["son_basari"],
                son_hata=s["son_hata"],
                hata_turu=s["hata_turu"],
                etkin=bool(s["etkin"]),
            )
            for s in satirlar
        ]

    # ---- teklif ----
    def teklif_ekle(self, teklif: Teklif) -> int:
        with self._kilit:
            imlec = self._con.execute(
                """INSERT INTO teklif
                   (urun_id, site, satici, baslik, url, liste_fiyati, sepet_fiyati,
                    kupon, kargo, stok, pesin_fiyatina_taksit, gozlem_zamani,
                    kaynak_katmani, teyit_durumu, teyit_zamani, kanit_goruntusu)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                _teklif_satiri(teklif),
            )
            self._isle()
            return int(imlec.lastrowid)

    def teklifler(self, urun_id: str, yalniz_son: bool = True) -> list[Teklif]:
        """Teklifler; yalniz_son=True ise her İLAN (site + satıcı + link) için en son gözlem.
        14.11 düzeltme: eskiden yalnız site+satıcı gruplanıyordu → aynı satıcının farklı ilanları birbirini eziyordu
        (PttAVM 119.190 TL'lik ilan, aynı satıcının sonraki başka ilanı yüzünden kayboluyordu)."""
        with self._kilit:
            if yalniz_son:
                satirlar = self._con.execute(
                    """SELECT * FROM (
                         SELECT t.*, ROW_NUMBER() OVER (
                           PARTITION BY t.site, COALESCE(t.satici, ''), COALESCE(t.url, '')
                           ORDER BY t.gozlem_zamani DESC, t.id DESC
                         ) AS _sira
                         FROM teklif t WHERE t.urun_id = ? AND t.site NOT IN (SELECT ad FROM site WHERE etkin = 0)
                       ) WHERE _sira = 1
                       ORDER BY sepet_fiyati IS NULL, sepet_fiyati ASC, site ASC, satici ASC""",
                    (urun_id,),
                ).fetchall()
            else:
                satirlar = self._con.execute(
                    """SELECT * FROM teklif t WHERE t.urun_id = ? AND t.site NOT IN (SELECT ad FROM site WHERE etkin = 0)
                       ORDER BY gozlem_zamani DESC, id DESC""",
                    (urun_id,),
                ).fetchall()
        return [_teklif_nesne(s) for s in satirlar]

    # ---- taksit ----
    def taksit_ekle(self, taksit: Taksit) -> int:
        with self._kilit:
            imlec = self._con.execute(
                """INSERT INTO taksit
                   (teklif_id, banka, taksit_sayisi, aylik, toplam, vade_farki_yuzde)
                   VALUES (?,?,?,?,?,?)""",
                (
                    taksit.teklif_id,
                    taksit.banka,
                    taksit.taksit_sayisi,
                    taksit.aylik,
                    taksit.toplam,
                    taksit.vade_farki_yuzde,
                ),
            )
            self._isle()
            return int(imlec.lastrowid)

    def taksitler(self, teklif_id: int) -> list[Taksit]:
        with self._kilit:
            satirlar = self._con.execute(
                "SELECT * FROM taksit WHERE teklif_id = ? ORDER BY taksit_sayisi, id",
                (teklif_id,),
            ).fetchall()
        return [
            Taksit(
                id=s["id"],
                teklif_id=s["teklif_id"],
                banka=s["banka"],
                taksit_sayisi=s["taksit_sayisi"],
                aylik=s["aylik"],
                toplam=s["toplam"],
                vade_farki_yuzde=s["vade_farki_yuzde"],
            )
            for s in satirlar
        ]

    # ---- olay ----
    def olay_ekle(self, olay: Olay) -> Olay:
        with self._kilit:
            imlec = self._con.execute(
                """INSERT INTO olay (tur, zaman, urun_id, gorev_id, mesaj, veri)
                   VALUES (?,?,?,?,?,?)""",
                (
                    olay.tur,
                    olay.zaman,
                    olay.urun_id,
                    olay.gorev_id,
                    olay.mesaj,
                    _json_yaz(olay.veri),
                ),
            )
            self._isle()
            kayit = olay.model_copy(update={"id": int(imlec.lastrowid)})
        return kayit

    def son_olaylar(self, limit: int = 50) -> list[Olay]:
        with self._kilit:
            satirlar = self._con.execute(
                "SELECT * FROM olay ORDER BY zaman DESC, id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [
            Olay(
                id=s["id"],
                tur=s["tur"],
                zaman=s["zaman"],
                urun_id=s["urun_id"],
                gorev_id=s["gorev_id"],
                mesaj=s["mesaj"],
                veri=_json_oku(s["veri"], {}),
            )
            for s in satirlar
        ]


__all__ = [
    "SEMA_SURUMU",
    "Katman",
    "Taksit",
    "Teklif",
    "TeyitDurumu",
    "UrunKimligi",
    "VeriTabani",
    "baglan",
    "goc",
    "kur",
    "surum_oku",
]
