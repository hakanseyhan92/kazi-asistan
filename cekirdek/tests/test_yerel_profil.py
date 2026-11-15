"""Yerel profil son çaresi: klon kopyalama ve tara.py / ucus.tara sırası — tarayıcı AÇILMAZ, sahte nesneler."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kazi.ayar import Ayar
from kazi.db import VeriTabani
from kazi.modeller import Katman, UrunKimligi
from kazi.siteler._kart import Kart
from kazi.tarama import tara as tara_mod
from kazi.tarayici import BotEngeli
from kazi.tarayici import yerel_profil as yp
from kazi.tarayici.yerel_profil import KLON_TAZELE_SN, YerelBrave, klon_tazele, yerel_profil_var
from kazi.ucus import depo
from kazi.ucus import tara as ucus_mod
from test_ucus_tara import SahteKaynak, _seg


# ------------------------------------------------------------------ klon
def _kaynak_kur(kok: Path) -> Path:
    d = kok / "Default"
    d.mkdir(parents=True)
    (kok / "Local State").write_text(json.dumps({"brave": {"p3a": {"enabled": True}}, "kullanici_ozel": 1}))
    (d / "Preferences").write_text(json.dumps({"extensions": {"settings": {"abc": {}}}, "sync": {"on": True},
                                               "brave_sync_v2": {"seed": "GIZLI"}}))
    db = sqlite3.connect(d / "Cookies")
    db.execute("create table cookies (host_key text, name text)")
    db.execute("insert into cookies values ('.ornek.com', 'oturum')")
    db.commit()
    db.close()
    (d / "Local Storage" / "leveldb").mkdir(parents=True)
    (d / "Local Storage" / "leveldb" / "000003.log").write_text("ls")
    for gizli in ("Login Data", "History", "Web Data", "Bookmarks"):
        (d / gizli).write_text("GIZLI")
    (d / "Extensions").mkdir()
    (d / "Extensions" / "x.txt").write_text("e")
    return kok


def _durum(kok: Path) -> dict:
    return {str(p.relative_to(kok)): (p.stat().st_mtime_ns, p.read_bytes()) for p in kok.rglob("*") if p.is_file()}


def test_yerel_profil_var(tmp_path):
    assert not yerel_profil_var(tmp_path / "yok")
    assert yerel_profil_var(_kaynak_kur(tmp_path / "k"))


def test_klon_yalniz_izinli_dosyalar(tmp_path):
    kaynak, hedef = _kaynak_kur(tmp_path / "k"), tmp_path / "klon"
    once = _durum(kaynak)
    assert klon_tazele(kaynak, hedef) is True
    dosyalar = {str(p.relative_to(hedef)) for p in hedef.rglob("*") if p.is_file()}
    assert dosyalar == {"Local State", "Default/Preferences", "Default/Cookies", yp.ZAMAN_DOSYASI,
                        ".kazi_klon_surum", "Default/Local Storage/leveldb/000003.log"}
    # 15.11 canlı: ayarlar KOPYALANMAZ (eklenti listesi / Brave Sync sızmasın); klonunkiler temiz tohumdur
    pr = json.loads((hedef / "Default" / "Preferences").read_text())
    assert "extensions" not in pr and "sync" not in pr and "brave_sync_v2" not in pr
    assert "kullanici_ozel" not in (hedef / "Local State").read_text()
    c = sqlite3.connect(hedef / "Default" / "Cookies")
    assert c.execute("select host_key, name from cookies").fetchall() == [(".ornek.com", "oturum")]
    c.close()
    assert _durum(kaynak) == once                         # kaynağa dokunulmadı


def test_eski_surum_klon_tamamen_silinir(tmp_path):
    kaynak, hedef = _kaynak_kur(tmp_path / "k"), tmp_path / "klon"
    (hedef / "Default" / "Extensions").mkdir(parents=True)
    (hedef / "Default" / "Preferences").write_text('{"extensions": {"settings": {}}, "sync": {"x": 1}}')
    (hedef / yp.ZAMAN_DOSYASI).write_text(str(10**12))       # "taze" görünse de sürüm eski → yeniden kurulur
    assert klon_tazele(kaynak, hedef) is True
    assert not (hedef / "Default" / "Extensions").exists()
    assert "extensions" not in json.loads((hedef / "Default" / "Preferences").read_text())


def test_klon_tohumlandi(tmp_path):
    kaynak, hedef = _kaynak_kur(tmp_path / "k"), tmp_path / "klon"
    klon_tazele(kaynak, hedef)
    ls = json.loads((hedef / "Local State").read_text())
    assert ls["brave"]["p3a"]["enabled"] is False


def test_klon_agdaki_network_cerezi(tmp_path):
    kaynak = tmp_path / "k"
    (kaynak / "Default" / "Network").mkdir(parents=True)
    db = sqlite3.connect(kaynak / "Default" / "Network" / "Cookies")
    db.execute("create table cookies (a text)")
    db.commit()
    db.close()
    assert yerel_profil_var(kaynak) and klon_tazele(kaynak, tmp_path / "klon")
    assert (tmp_path / "klon" / "Default" / "Network" / "Cookies").is_file()


def test_klon_6_saat_sinirlamasi_ve_zorla(tmp_path):
    kaynak, hedef = _kaynak_kur(tmp_path / "k"), tmp_path / "klon"
    zaman = [1000.0]
    assert klon_tazele(kaynak, hedef, simdi=lambda: zaman[0]) is True
    zaman[0] += KLON_TAZELE_SN - 1
    assert klon_tazele(kaynak, hedef, simdi=lambda: zaman[0]) is False
    assert klon_tazele(kaynak, hedef, zorla=True, simdi=lambda: zaman[0]) is True
    zaman[0] += KLON_TAZELE_SN + 1
    assert klon_tazele(kaynak, hedef, simdi=lambda: zaman[0]) is True


def test_klon_kaynak_yoksa_kopyalanmaz(tmp_path):
    assert klon_tazele(tmp_path / "yok", tmp_path / "klon") is False
    assert not (tmp_path / "klon").exists()


def test_yerel_brave_yapilandirma(tmp_path):
    y = YerelBrave(klon=tmp_path / "k", kilit_dosya=tmp_path / "y.lock", durum_dosya=tmp_path / "d.json")
    assert y.kilit_paylasimli is True and y.kademe.k1 is None and y.cdp_url.endswith(":9335")
    assert y.kademe._denenecek_katmanlar("epey") == [Katman.K2]
    assert yp.YEREL_KLON.name == "kazi_yerel_klon" and yp.YEREL_PORT == 9335
    y.kapat()


# ------------------------------------------------------------------ tara.py sırası
class SahteTarayici:
    def __init__(self, sira, ad):
        self.brave_acik = False
        self.kapandi = 0
        self.bildirimler: list[dict] = []
        self._sira, self._ad = sira, ad

    def brave_birak(self):
        self._sira.append(f"{self._ad}_birak")

    def kapat(self):
        self.kapandi += 1
        self._sira.append(f"{self._ad}_kapat")


class Site:
    """Brave ve chromium BotEngeli; yerel kart döner."""

    def __init__(self, sira):
        self.sira = sira

    def ara(self, t, sorgu, min_f=None, max_f=None, sayfa=2):
        self.sira.append(f"{t._ad}_ara")
        if t._ad != "yerel":
            raise BotEngeli("n11", "https://www.n11.com")
        return [Kart(site="n11", baslik="Inno3D RTX 5080 X3 16GB", url="https://www.n11.com/u/1",
                     sepet_fiyati=115000.0, satici="X", yontem="brave")]


@pytest.fixture
def v(tmp_path):
    vt = VeriTabani(Ayar(veri_dizini=tmp_path).db_yolu)
    vt.urun_ekle(UrunKimligi(id="gpu", kanonik_ad="RTX 5080", eslesme_kurallari={
        "sorgular": ["rtx 5080"], "dahil": ["5080"], "min_fiyat": 90000, "max_fiyat": 260000}))
    yield vt
    vt.kapat()


def _tara(v, monkeypatch, *, ayar=True, chromium=True):
    sira: list = []
    monkeypatch.setattr(tara_mod, "SITELER", {"n11": Site(sira)})
    monkeypatch.setattr(tara_mod, "paralel_ayarlar", lambda: {"chromium_yedek": True, "yerel_profil_yedek": ayar})
    yerel: list = []

    def yerel_f():
        sira.append("yerel_fabrika")
        y = SahteTarayici(sira, "yerel")
        yerel.append(y)
        return y

    def chromium_f():
        sira.append("chromium_fabrika")
        return SahteTarayici(sira, "chromium")

    olaylar: list[dict] = []
    s = tara_mod.tara(v, SahteTarayici(sira, "brave"), urun_idler=["gpu"], bildirim_fn=olaylar.append,
                      yedek_fabrika=chromium_f if chromium else None, yerel_fabrika=yerel_f)
    return s, sira, yerel, olaylar


def test_sira_brave_chromium_yerel(v, monkeypatch):
    s, sira, yerel, olaylar = _tara(v, monkeypatch)
    assert [x for x in sira if x.endswith("_ara")] == ["brave_ara", "chromium_ara", "yerel_ara"]
    assert sira.index("chromium_kapat") < sira.index("yerel_fabrika")
    assert len(yerel) == 1 and yerel[0].kapandi == 1
    kap = s.kapsam[("gpu", "n11")]
    assert kap["tur"] == "ok" and kap["yontem"] == "yerel_brave" and kap["katman"] == str(Katman.K2)
    assert olaylar[-1]["yedek"] == "yerel_brave"
    assert len(v.teklifler("gpu")) == 1


def test_ayar_kapaliysa_yerel_yok(v, monkeypatch):
    s, _, yerel, olaylar = _tara(v, monkeypatch, ayar=False)
    assert yerel == [] and s.kapsam[("gpu", "n11")]["tur"] == "blok" and "yedek" not in olaylar[-1]


def test_yerel_chromium_fabrikasi_olmadan_da(v, monkeypatch):
    s, sira, yerel, _ = _tara(v, monkeypatch, chromium=False)
    assert "chromium_ara" not in sira and len(yerel) == 1
    assert s.kapsam[("gpu", "n11")]["yontem"] == "yerel_brave"


# ------------------------------------------------------------------ uçuş
def test_ucus_engelli_kaynak_yerelle_tekrar(monkeypatch):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    depo.kur(con)
    monkeypatch.setattr("kazi.tarayici.paralel_ayar.paralel_ayarlar", lambda *a, **k: {"yerel_profil_yedek": True})

    class Brave:
        birakildi = 0

        def hiz_sinirla(self, *_):
            pass

        def brave_birak(self):
            Brave.birakildi += 1

    class Yerel(Brave):
        kapandi = 0

        def kapat(self):
            Yerel.kapandi += 1

    class Kaynak(SahteKaynak):
        def ara(self, t, bacak, tarih):
            self.cagrilar.append((type(t).__name__, tarih))
            if isinstance(t, Yerel):
                return [_seg("google", 70000.0, kalkis_nokta=bacak["kalkis"], varis_nokta=bacak["varis"], tarih=tarih)]
            raise BotEngeli("google", "x")

    k = Kaynak()
    monkeypatch.setitem(ucus_mod.KAYNAKLAR, "google", k)
    uretilen: list = []
    plan = {"bacaklar": [{"id": "a", "kalkis": "IST", "varis": "NRT", "tarih": "2026-12-27", "esnek_gun": 0},
                         {"id": "b", "kalkis": "NRT", "varis": "IST", "tarih": "2027-01-06", "esnek_gun": 0}]}

    def fabrika():
        y = Yerel()
        uretilen.append(y)
        return y

    s = ucus_mod.tara(con, Brave(), plan, kaynaklar=["google"], yerel_fabrika=fabrika)
    assert len(uretilen) == 1 and Brave.birakildi == 1
    assert [c[0] for c in k.cagrilar] == ["Brave", "Yerel", "Brave", "Yerel"]
    assert all(r["tur"] == "ok" and r["yontem"] == "yerel_brave" for r in s.kapsam.values())
    assert Yerel.kapandi >= 1
    s2 = ucus_mod.tara(con, Brave(), plan, kaynaklar=["google"])      # fabrika yoksa blok kalır
    turler = [r["tur"] for _, r in sorted(s2.kapsam.items())]
    assert "blok" in turler and set(turler) <= {"blok", "blok_atlandi"}   # 15.11: engellenen kaynak sonra atlanır
    assert turler.count("blok") == 1
