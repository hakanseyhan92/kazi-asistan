"""Kategori bazlı tarama zamanlayıcısı — saf planlama, ayar ve durum deposu."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import math
import sqlite3
from typing import Any

from ..modeller import TZ_ISTANBUL, UrunKimligi
from ..tarayici import gorunurluk

# 15.11 kullanıcı: 5'er dakikalık seçenekler (30, 35, … 120)
SECENEKLER_DK: list[int] = list(range(30, 121, 5))
# Herhangi iki OTOMATİK tarama arası boşluk. 15.11: 10 → 2 dk (kullanıcı kararı "site başına ~2 dk boşluk"; 10 dk birikip
# vadesi dolan kategorileri saatlerce geciktiriyordu). Siteler zaten işçi başına tek istek + OtoHiz ile korunuyor.
MIN_ARALIK_DK: int = 2
MANUEL_BEKLEME_DK: int = 10
VARSAYILAN_DIGER_DK: int = 60
VARSAYILAN_ARALIKLAR: dict[str, int] = {"gpu": 30, "ucus": 120}

KATEGORI_SIRASI: tuple[str, ...] = (
    "gpu",
    "cpu",
    "ram",
    "anakart",
    "ssd",
    "psu",
    "kasa",
    "sogutucu",
)

KATEGORI_ADLARI: dict[str, str] = {
    "gpu": "Ekran kartı",
    "cpu": "İşlemci",
    "ram": "Bellek (RAM)",
    "anakart": "Anakart",
    "ssd": "SSD",
    "psu": "Güç kaynağı",
    "kasa": "Kasa",
    "sogutucu": "Soğutucu",
    "ucus": "Uçuşlar",
}

SEMA_TARAMA_KATEGORI = """CREATE TABLE IF NOT EXISTS tarama_kategori (
    id TEXT PRIMARY KEY,
    son_baslangic TEXT,
    son_bitis TEXT,
    son_sonuc TEXT,
    manuel_istek_zamani TEXT,
    manuel_son TEXT,
    sayac_sifir TEXT
)"""


@dataclass(frozen=True)
class Kategori:
    id: str
    ad: str
    tur: str
    urunler: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ zaman yardımcıları
def _tz_li(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ_ISTANBUL)
    return dt.astimezone(TZ_ISTANBUL)


def iso_coz(zaman: str | None) -> datetime | None:
    if not zaman:
        return None
    try:
        dt = datetime.fromisoformat(str(zaman).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _tz_li(dt)


def iso_bicim(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return _tz_li(dt).isoformat(timespec="seconds")


def _kalan_saniye(hedef: datetime, simdi: datetime) -> int:
    fark = (hedef - simdi).total_seconds()
    if fark <= 0:
        return 0
    return math.ceil(fark)


# ------------------------------------------------------------------ veritabanı göçü & durum
def _baglanti_ve_kilit(v: Any) -> tuple[sqlite3.Connection, Any, bool]:
    if hasattr(v, "baglanti"):
        kilit = getattr(v, "_kilit", None) or nullcontext()
        return v.baglanti, kilit, bool(getattr(v, "_toplu", False))
    if isinstance(v, sqlite3.Connection):
        v.row_factory = sqlite3.Row
        return v, nullcontext(), False
    raise TypeError(f"Desteklenmeyen veritabanı nesnesi: {type(v)!r}")


def _tablo_var_mi(con: sqlite3.Connection, ad: str) -> bool:
    satir = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (ad,),
    ).fetchone()
    return satir is not None


def kur(v: Any) -> None:
    """`tarama_kategori` tablosunu oluştur (idempotent)."""
    con, kilit, toplu = _baglanti_ve_kilit(v)
    with kilit:
        con.execute(SEMA_TARAMA_KATEGORI)
        if not _sutun_var_mi(con, "sayac_sifir"):   # 15.11 göçü: "Sıfırla" düğmesi
            con.execute("ALTER TABLE tarama_kategori ADD COLUMN sayac_sifir TEXT")
        if not toplu:
            con.commit()


def _sutun_var_mi(con: sqlite3.Connection, sutun: str) -> bool:
    return any(r[1] == sutun for r in con.execute("PRAGMA table_info(tarama_kategori)").fetchall())


def durum_oku(v: Any) -> dict[str, dict[str, Any]]:
    """`tarama_kategori` satırlarını `{id: {...}}` sözlüğü olarak oku."""
    con, kilit, _ = _baglanti_ve_kilit(v)
    with kilit:
        if not _tablo_var_mi(con, "tarama_kategori"):
            return {}
        sifir_sutun = "sayac_sifir" if _sutun_var_mi(con, "sayac_sifir") else "NULL AS sayac_sifir"
        satirlar = con.execute(
            f"""SELECT id, son_baslangic, son_bitis, son_sonuc,
                      manuel_istek_zamani, manuel_son, {sifir_sutun}
               FROM tarama_kategori"""
        ).fetchall()
    sonuc: dict[str, dict[str, Any]] = {}
    for s in satirlar:
        ham_sonuc = s["son_sonuc"]
        try:
            cozulen_sonuc = json.loads(ham_sonuc) if ham_sonuc else None
        except (TypeError, ValueError):
            cozulen_sonuc = None
        if cozulen_sonuc is not None and not isinstance(cozulen_sonuc, dict):
            cozulen_sonuc = None
        sonuc[str(s["id"])] = {
            "son_baslangic": s["son_baslangic"],
            "son_bitis": s["son_bitis"],
            "son_sonuc": cozulen_sonuc,
            "manuel_istek_zamani": s["manuel_istek_zamani"],
            "manuel_son": s["manuel_son"],
            "sayac_sifir": s["sayac_sifir"],
        }
    return sonuc


def sayac_sifirla(v: Any, kat_id: str, zaman_iso: str) -> None:
    """"Sıfırla" (15.11 kullanıcı): kategorinin geri sayımı bu andan, seçili aralıkla yeniden başlar.
    Son tarama bilgisi (başlangıç/bitiş/sonuç) değişmez."""
    kur(v)
    con, kilit, toplu = _baglanti_ve_kilit(v)
    with kilit:
        con.execute(
            """INSERT INTO tarama_kategori (id, sayac_sifir) VALUES (?, ?)
               ON CONFLICT(id) DO UPDATE SET sayac_sifir = excluded.sayac_sifir""",
            (kat_id, zaman_iso),
        )
        if not toplu:
            con.commit()


def tarama_basladi(v: Any, kat_id: str, zaman_iso: str) -> None:
    """Kategori taramasının başlangıcını kaydet ve varsa bekleyen elle isteği düşür."""
    con, kilit, toplu = _baglanti_ve_kilit(v)
    with kilit:
        con.execute(SEMA_TARAMA_KATEGORI)
        con.execute(
            """INSERT INTO tarama_kategori (id, son_baslangic, manuel_istek_zamani)
               VALUES (?, ?, NULL)
               ON CONFLICT(id) DO UPDATE SET
                 son_baslangic = excluded.son_baslangic,
                 manuel_istek_zamani = CASE
                   WHEN tarama_kategori.manuel_istek_zamani IS NOT NULL
                        AND tarama_kategori.manuel_istek_zamani <= excluded.son_baslangic
                   THEN NULL
                   ELSE tarama_kategori.manuel_istek_zamani
                 END""",
            (kat_id, zaman_iso),
        )
        if not toplu:
            con.commit()


def tarama_bitti(
    v: Any,
    kat_id: str,
    bitis_iso: str,
    sonuc: dict[str, Any] | None = None,
) -> None:
    """Kategori taramasının bitiş zamanını ve özet sonucunu kaydet."""
    con, kilit, toplu = _baglanti_ve_kilit(v)
    sonuc_json = json.dumps(sonuc, ensure_ascii=False) if sonuc is not None else None
    with kilit:
        con.execute(SEMA_TARAMA_KATEGORI)
        con.execute(
            """INSERT INTO tarama_kategori (id, son_bitis, son_sonuc)
               VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 son_bitis = excluded.son_bitis,
                 son_sonuc = excluded.son_sonuc""",
            (kat_id, bitis_iso, sonuc_json),
        )
        if not toplu:
            con.commit()


def manuel_istek_kaydet(
    v: Any,
    kat_id: str,
    zaman_iso: str,
    *,
    kuyruga_ekle: bool = True,
) -> None:
    """Elle tarama isteğini ve 10 dk kilit zamanını kaydet."""
    con, kilit, toplu = _baglanti_ve_kilit(v)
    istek_zamani = zaman_iso if kuyruga_ekle else None
    with kilit:
        con.execute(SEMA_TARAMA_KATEGORI)
        con.execute(
            """INSERT INTO tarama_kategori (id, manuel_istek_zamani, manuel_son)
               VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 manuel_istek_zamani = CASE
                   WHEN ? THEN excluded.manuel_istek_zamani
                   ELSE tarama_kategori.manuel_istek_zamani
                 END,
                 manuel_son = excluded.manuel_son""",
            (kat_id, istek_zamani, zaman_iso, int(kuyruga_ekle)),
        )
        if not toplu:
            con.commit()


# ------------------------------------------------------------------ kategoriler & ayar
def kategori_adi(kat_id: str) -> str:
    return KATEGORI_ADLARI.get(kat_id, kat_id)


def _aktif_urunler(v: Any) -> list[UrunKimligi]:
    if hasattr(v, "urunler_listele") and hasattr(v, "takip_disi"):
        disarida = set(v.takip_disi())
        return [u for u in v.urunler_listele(aktif=True) if u.id and u.id not in disarida]

    con, kilit, _ = _baglanti_ve_kilit(v)
    with kilit:
        disarida = (
            {r["urun_id"] for r in con.execute("SELECT urun_id FROM takip_disi")}
            if _tablo_var_mi(con, "takip_disi")
            else set()
        )
        satirlar = con.execute(
            "SELECT id, tur, kanonik_ad, eslesme_kurallari FROM urun_kimligi WHERE aktif = 1 ORDER BY olusturma DESC, id"
        ).fetchall()
    urunler: list[UrunKimligi] = []
    for s in satirlar:
        if s["id"] in disarida:
            continue
        try:
            kurallar = json.loads(s["eslesme_kurallari"] or "{}")
            if not isinstance(kurallar, dict):
                kurallar = {}
        except (TypeError, ValueError):
            kurallar = {}
        urunler.append(
            UrunKimligi(
                id=s["id"],
                tur=s["tur"],
                kanonik_ad=s["kanonik_ad"],
                eslesme_kurallari=kurallar,
            )
        )
    return urunler


def kategoriler(v: Any) -> list[Kategori]:
    """Takipteki (takip_disi olmayan, aktif) ürünleri kategorilere ayır + `ucus`."""
    gruplar: dict[str, list[str]] = {}
    for u in _aktif_urunler(v):
        if str(u.tur) == "ucus":
            continue
        ham_kat = (u.eslesme_kurallari or {}).get("kategori")
        kat_id = str(ham_kat).strip() if ham_kat is not None else ""
        if not kat_id:
            kat_id = "diger"
        if u.id:
            gruplar.setdefault(kat_id, []).append(u.id)

    bilinen_sira = [k for k in KATEGORI_SIRASI if k in gruplar]
    diger_sira = sorted(k for k in gruplar if k not in KATEGORI_SIRASI and k != "ucus")
    sonuc: list[Kategori] = [
        Kategori(id=k, ad=kategori_adi(k), tur="urun", urunler=sorted(gruplar[k]))
        for k in (*bilinen_sira, *diger_sira)
    ]
    sonuc.append(Kategori(id="ucus", ad=kategori_adi("ucus"), tur="ucus", urunler=[]))
    return sonuc


def varsayilan_aralik_dk(kat_id: str) -> int:
    return VARSAYILAN_ARALIKLAR.get(kat_id, VARSAYILAN_DIGER_DK)


def gecerli_aralik_mi(deger: Any) -> bool:
    return isinstance(deger, int) and not isinstance(deger, bool) and deger in SECENEKLER_DK


def aralik_dk_coz(kat_id: str, ayar: dict[str, Any] | None = None) -> int:
    """Ayar sözlüğünden kategori aralığını çöz; geçersiz/eksik ise varsayılana dön."""
    harita = _ayar_haritasi_coz(ayar)
    deger = harita.get(kat_id)
    if gecerli_aralik_mi(deger):
        return int(deger)
    return varsayilan_aralik_dk(kat_id)


def _ayar_haritasi_coz(ayar: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ayar, dict):
        return {}
    if isinstance(ayar.get("kategori_aralik_dk"), dict):
        return ayar["kategori_aralik_dk"]
    return ayar


def kategori_aralik_oku() -> dict[str, int]:
    """`$XDG_CONFIG_HOME/kazi/tarama.json` içindeki geçerli `kategori_aralik_dk` değerlerini oku."""
    ham = gorunurluk.ayar_oku().get("kategori_aralik_dk")
    if not isinstance(ham, dict):
        return {}
    return {
        str(k): int(v)
        for k, v in ham.items()
        if isinstance(k, str) and k.strip() and gecerli_aralik_mi(v)
    }


def kategori_aralik_yaz(guncelleme: dict[str, int]) -> dict[str, int]:
    """Aralıkları doğrula ve mevcut `tarama.json` anahtarlarını (`brave_gorunur`) koruyarak kaydet."""
    if not isinstance(guncelleme, dict):
        raise ValueError("kategoriler sözlük olmalı")
    dogrulanmis: dict[str, int] = {}
    for k, v in guncelleme.items():
        if not isinstance(k, str) or not k.strip():
            raise ValueError(f"geçersiz kategori kodu: {k!r}")
        if not gecerli_aralik_mi(v):
            raise ValueError(
                f"{k}: aralık {SECENEKLER_DK} seçeneklerinden biri olmalı (gelen: {v!r})"
            )
        dogrulanmis[k.strip()] = int(v)
    mevcut = kategori_aralik_oku()
    mevcut.update(dogrulanmis)
    gorunurluk.ayar_yaz(kategori_aralik_dk=mevcut)
    return mevcut


# ------------------------------------------------------------------ çalışma durumu çözümü
def aktif_durumlar_coz(
    durum: dict[str, dict[str, Any]],
    simdi: datetime | None = None,
    *,
    asimi_dk: int = 90,
) -> tuple[list[str], list[str]]:
    """DB durumundan çalışan kategoriler (en yeni başlayan önce; "aynı anda kategori" ile birden çok
    olabilir) ile elle tarama kuyruğunu çıkar."""
    ref = _tz_li(simdi) if simdi is not None else datetime.now(TZ_ISTANBUL)
    calisanlar: list[tuple[datetime, str]] = []
    kuyruk_adaylari: list[tuple[datetime, str]] = []

    for kat_id, d in durum.items():
        bas = iso_coz(d.get("son_baslangic"))
        bit = iso_coz(d.get("son_bitis"))
        if bas is not None and (bit is None or bas > bit):
            if (ref - bas) <= timedelta(minutes=asimi_dk):
                calisanlar.append((bas, kat_id))

        istek = iso_coz(d.get("manuel_istek_zamani"))
        if istek is not None:
            kuyruk_adaylari.append((istek, kat_id))

    calisanlar.sort(key=lambda x: (x[0], x[1]), reverse=True)
    calisan_idler = [kid for _, kid in calisanlar]
    kuyruk_adaylari.sort(key=lambda x: (x[0], x[1]))
    kuyruk = [kid for _, kid in kuyruk_adaylari if kid not in calisan_idler]
    return calisan_idler, kuyruk


def aktif_durum_coz(
    durum: dict[str, dict[str, Any]],
    simdi: datetime | None = None,
    *,
    asimi_dk: int = 90,
) -> tuple[str | None, list[str]]:
    """DB durumundan çalışan kategori (birden çoksa en yeni başlayan) ile elle tarama kuyruğunu çıkar."""
    calisanlar, kuyruk = aktif_durumlar_coz(durum, simdi, asimi_dk=asimi_dk)
    return (calisanlar[0] if calisanlar else None), kuyruk


# ------------------------------------------------------------------ saf planlama
HIC_TARANMAMIS = datetime(2000, 1, 1, tzinfo=TZ_ISTANBUL)
_UCUS = "ucus"  # asla başka kategoriyle birleştirilmez


def _calisan_kumesi(calisan: str | list[str] | tuple[str, ...] | None) -> set[str]:
    if calisan is None:
        return set()
    if isinstance(calisan, str):
        return {calisan}
    return {str(c) for c in calisan}


def _son_global_bitis(durum: dict[str, dict[str, Any]]) -> datetime | None:
    bitisler = [iso_coz(d.get("son_bitis")) for d in durum.values()]
    gecerliler = [b for b in bitisler if b is not None]
    return max(gecerliler) if gecerliler else None


def vade_tabani(d: dict[str, Any]) -> datetime | None:
    """Aralığın sayılmaya başladığı an: kategorinin son taramasının BİTİŞİ (15.11 kullanıcı: "süre dolunca, biten taramanın
    ardından otomatik başlasın"). Bitiş yoksa/eskiyse (yarım kalmış tarama) başlangıç."""
    son_bas = iso_coz(d.get("son_baslangic"))
    son_bit = iso_coz(d.get("son_bitis"))
    taban = son_bit if son_bit is not None and (son_bas is None or son_bit >= son_bas) else son_bas
    sifir = iso_coz(d.get("sayac_sifir"))   # "Sıfırla" daha yeniyse sayaç oradan başlar
    if sifir is not None and (taban is None or sifir > taban):
        return sifir
    return taban


def plan_hesapla(
    kategori_listesi: list[Kategori],
    durum: dict[str, dict[str, Any]],
    ayar: dict[str, Any] | None,
    simdi: datetime,
    calisan: str | list[str] | tuple[str, ...] | None = None,
    kuyruk: list[str] | None = None,
    azami: int = 1,
) -> dict[str, Any]:
    """Sözleşmedeki `GET /tarama/plan` JSON yapısını saf olarak hesapla.

    `azami` > 1 ("aynı anda kategori") iken birlikte başlayacak ürün kategorileri aynı `sonraki`
    zamanını alır; 10 dk boşluk kuralı gruplar arasında geçerlidir. `calisan` birden çok olabilir."""
    ref_simdi = _tz_li(simdi)
    azami = max(1, int(azami))
    calisanlar = _calisan_kumesi(calisan)
    kuyruk_list = list(kuyruk or [])
    kuyruk_set = set(kuyruk_list)
    son_global = _son_global_bitis(durum)
    global_izin = (son_global + timedelta(minutes=MIN_ARALIK_DK)) if son_global else None

    araliklar: dict[str, int] = {k.id: aralik_dk_coz(k.id, ayar) for k in kategori_listesi}
    sonraki_haritasi: dict[str, datetime] = {}
    durum_etiketi: dict[str, str] = {}
    bekleyenler: list[tuple[datetime, int, int, str]] = []

    for idx, k in enumerate(kategori_listesi):
        d = durum.get(k.id) or {}
        aralik = timedelta(minutes=araliklar[k.id])
        son_bas = iso_coz(d.get("son_baslangic"))

        if k.id in calisanlar:
            durum_etiketi[k.id] = "calisiyor"
            aday = ref_simdi + aralik   # tahmini: bitişten itibaren sayılacak
            if global_izin is not None and global_izin > aday:
                aday = global_izin
            sonraki_haritasi[k.id] = aday
        elif k.id in kuyruk_set:
            durum_etiketi[k.id] = "kuyrukta"
            sonraki_haritasi[k.id] = ref_simdi
        else:
            durum_etiketi[k.id] = "bekliyor"
            # hiç taranmamış kategori EN ÖNCE (15.11: "şimdi" sayılınca vadesi geçmiş gpu/cpu/ram hep öne geçiyor,
            # anakart/ssd/psu/kasa/soğutucu/uçuş hiç taranmıyordu)
            taban = vade_tabani(d)
            ham = (taban + aralik) if taban is not None else HIC_TARANMAMIS
            bekleyenler.append((ham, araliklar[k.id], idx, k.id))

    # Bekleyen kategorileri "en çok gecikmiş önce" sırasıyla en az 10 dk arayla diz
    en_erken_izin = global_izin
    if calisanlar:
        aktif_kuyruk = len([x for x in kuyruk_list if x not in calisanlar])
        en_erken = ref_simdi + timedelta(minutes=MIN_ARALIK_DK * (1 + aktif_kuyruk))
        en_erken_izin = max(en_erken_izin, en_erken) if en_erken_izin else en_erken
    elif kuyruk_list:
        en_erken = ref_simdi + timedelta(minutes=MIN_ARALIK_DK * len(kuyruk_list))
        en_erken_izin = max(en_erken_izin, en_erken) if en_erken_izin else en_erken

    bekleyenler.sort(key=lambda item: (item[0], item[1], item[2]))
    kalanlar = list(bekleyenler)
    while kalanlar:
        lider = kalanlar.pop(0)
        planlanan = max(lider[0], en_erken_izin) if en_erken_izin is not None else lider[0]
        grup = [lider]
        if azami > 1 and lider[3] != _UCUS:
            for aday in list(kalanlar):
                if len(grup) >= azami:
                    break
                if aday[3] != _UCUS and aday[0] <= planlanan:
                    grup.append(aday)
                    kalanlar.remove(aday)
        for _ham, _aralik, _idx, kat_id in grup:
            sonraki_haritasi[kat_id] = planlanan
        etkin_baslangic = max(planlanan, ref_simdi)
        en_erken_izin = etkin_baslangic + timedelta(minutes=MIN_ARALIK_DK)

    ogeler: list[dict[str, Any]] = []
    for k in kategori_listesi:
        d = durum.get(k.id) or {}
        son_bas = iso_coz(d.get("son_baslangic"))
        son_bit = iso_coz(d.get("son_bitis"))
        manuel_son = iso_coz(d.get("manuel_son"))
        kilit_bitis = (
            manuel_son + timedelta(minutes=MANUEL_BEKLEME_DK)
            if manuel_son is not None
            else None
        )
        sonraki_dt = sonraki_haritasi[k.id]

        ogeler.append(
            {
                "id": k.id,
                "ad": k.ad,
                "tur": k.tur,
                "urunler": list(k.urunler),
                "aralik_dk": araliklar[k.id],
                "son_baslangic": iso_bicim(son_bas),
                "son_bitis": iso_bicim(son_bit),
                "son_sonuc": d.get("son_sonuc"),
                "sayac_sifir": d.get("sayac_sifir"),
                "sonraki": iso_bicim(sonraki_dt),
                "kalan_sn": _kalan_saniye(sonraki_dt, ref_simdi),
                "durum": durum_etiketi[k.id],
                "manuel_kilit_bitis": iso_bicim(kilit_bitis),
                "manuel_kalan_sn": (
                    _kalan_saniye(kilit_bitis, ref_simdi)
                    if kilit_bitis is not None
                    else 0
                ),
            }
        )

    return {
        "secenekler_dk": list(SECENEKLER_DK),
        "min_aralik_dk": MIN_ARALIK_DK,
        "manuel_bekleme_dk": MANUEL_BEKLEME_DK,
        "simdi": iso_bicim(ref_simdi),
        "kategoriler": ogeler,
    }


def siradakiler(
    kategori_listesi: list[Kategori],
    durum: dict[str, dict[str, Any]],
    ayar: dict[str, Any] | None,
    simdi: datetime,
    calisan: str | list[str] | tuple[str, ...] | None = None,
    kuyruk: list[str] | None = None,
    azami: int = 1,
) -> list[str]:
    """Şu an BİRLİKTE başlatılacak kategoriler (en çok `azami`).

    Elle kuyruktaki istek tek başına (eskisi gibi) öncelikli; yoksa vadesi dolmuş ürün kategorilerinden en
    öncelikli `azami` tanesi. `ucus` asla birleştirilmez: en öncelikli ise tek başına döner, değilse gruptan atlanır."""
    if _calisan_kumesi(calisan):
        return []
    azami = max(1, int(azami))
    bilinen = {k.id for k in kategori_listesi}
    for kid in kuyruk or []:
        if kid in bilinen:
            return [kid]

    ref_simdi = _tz_li(simdi)
    son_global = _son_global_bitis(durum)
    if son_global is not None and ref_simdi < son_global + timedelta(minutes=MIN_ARALIK_DK):
        return []

    adaylar: list[tuple[datetime, int, int, str]] = []
    for idx, k in enumerate(kategori_listesi):
        d = durum.get(k.id) or {}
        aralik_dk = aralik_dk_coz(k.id, ayar)
        taban = vade_tabani(d)
        ham = (taban + timedelta(minutes=aralik_dk)) if taban is not None else HIC_TARANMAMIS
        if ham <= ref_simdi:
            adaylar.append((ham, aralik_dk, idx, k.id))

    if not adaylar:
        return []
    adaylar.sort(key=lambda item: (item[0], item[1], item[2]))
    ilk = adaylar[0][3]
    if ilk == _UCUS or azami == 1:
        return [ilk]
    grup = [a[3] for a in adaylar if a[3] != _UCUS][:azami]
    return grup


def siradaki(
    kategori_listesi: list[Kategori],
    durum: dict[str, dict[str, Any]],
    ayar: dict[str, Any] | None,
    simdi: datetime,
    calisan: str | list[str] | tuple[str, ...] | None = None,
    kuyruk: list[str] | None = None,
) -> str | None:
    """Şu an başlatılması gereken TEK kategori (kuyruktaki elle istek > zamanı gelen)."""
    sonuc = siradakiler(kategori_listesi, durum, ayar, simdi, calisan, kuyruk, azami=1)
    return sonuc[0] if sonuc else None
