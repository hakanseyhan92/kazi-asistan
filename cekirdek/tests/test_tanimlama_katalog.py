"""Katalog adayları: gerçek Akakçe arama sayfası (tests/veri_tanimlama/akakce_rtx5080.html) + küçük sahte sayfalar."""
from pathlib import Path

import pytest
from veri_tanimlama.yardimci import sahte_arama_html

from kazi.modeller import Aday, TanimlaYanit
from kazi.tanimlama import ayristir, tanimla
from kazi.tanimlama.katalog import (arama_metinleri, arama_url, kartlar, katalog_ara, katalog_ara_ayrintili,
                                    mpn_cikar)

VERI = Path(__file__).parent / "veri_tanimlama"
HTML = (VERI / "akakce_rtx5080.html").read_text(encoding="utf-8")


def getir_sabit(html):
    cagrilar = []

    def getir(url):
        cagrilar.append(url)
        return html

    getir.cagrilar = cagrilar
    return getir


# ---------------------------------------------------------------- gerçek sayfa
def test_gercek_sayfa_kartlari():
    k = kartlar(HTML)
    assert len(k) >= 30
    ilk = k[0]
    assert ilk.kod == "1139197687" and ilk.fiyat == 257000.0 and ilk.satici_sayisi == 1
    assert ilk.ad.startswith("Asus RTX 5080 ROG Astral White")
    assert ilk.url.startswith("https://www.akakce.com/ekran-karti/en-ucuz-asus-rtx-5080")
    assert ilk.gorsel.startswith("https://cdn.akakce.com/")
    assert len({x.kod for x in k}) == len(k)                       # tekrar yok


def test_gercek_sayfa_adaylari():
    getir = getir_sabit(HTML)
    q = ayristir("rtx 5080 en ucuz")
    adaylar, elenen = katalog_ara_ayrintili(q, getir)
    assert getir.cagrilar == ["https://www.akakce.com/arama/?q=RTX+5080"]
    assert adaylar and all(isinstance(a, Aday) for a in adaylar)
    # laptop + hazır sistem(ler) elendi, gerekçeleriyle
    gerekce = {e.gerekce for e in elenen}
    assert "laptop/notebook" in gerekce and "hazır sistem" in gerekce
    basliklar = " ".join(a.baslik for a in adaylar).lower()
    assert "notebook" not in basliklar and "masaüstü" not in basliklar and "oem paket" not in basliklar
    assert all("5080" in a.baslik for a in adaylar)
    # id, kaynak, fiyat, görsel, url
    a = adaylar[0]
    assert a.id.startswith("akakce:") and a.kaynak == "akakce"
    assert a.fiyat_araligi.alt > 0 and a.url.startswith("https://www.akakce.com/") and a.gorsel
    assert a.ozellikler["kategori"] == "gpu" and a.ozellikler["model"] == "RTX 5080"
    assert a.ozellikler["vram_gb"] == 16 and a.ozellikler["vram_tipi"] == "GDDR7"
    assert a.ozellikler["satici_sayisi"] >= 1
    # "en ucuz" → fiyata göre artan
    fiyatlar = [a.fiyat_araligi.alt for a in adaylar]
    assert fiyatlar == sorted(fiyatlar)
    assert adaylar[0].id == "akakce:925262493" and fiyatlar[0] == 120807.0       # Inno3D RTX 5080 X3


def test_gercek_sayfa_en_ucuz_olmadan_puana_gore_ve_pydantic_uyumu():
    adaylar = katalog_ara(ayristir("rtx 5080"), getir_sabit(HTML))
    assert len(adaylar) >= 10
    yanit = TanimlaYanit(istek_id="a1b2c3", adaylar=adaylar)               # SOZLESME biçimine uyar
    assert yanit.model_dump()["adaylar"][0]["fiyat_araligi"]["alt"] > 0
    assert all(a.ozellikler["model"] == "RTX 5080" for a in adaylar)       # Ti/Super/5090 yok


def test_gercek_sayfada_mpn():
    adaylar = {a.id: a for a in katalog_ara(ayristir("rtx 5080"), getir_sabit(HTML))}
    assert adaylar["akakce:903196666"].mpn == "G5080-16V3C"                 # MSI Ventus 3X OC
    assert adaylar["akakce:982957987"].mpn == "ZT-B50800J2-10P"             # Zotac
    assert adaylar["akakce:903198113"].mpn == "PRIME-RTX5080-O16G"          # Asus Prime
    assert adaylar["akakce:925262493"].mpn == "N50803-16D7-176068N"         # Inno3D


def test_marka_filtresi_gercek_sayfa():
    adaylar = katalog_ara(ayristir("MSI rtx 5080"), getir_sabit(HTML))
    assert adaylar and all(a.baslik.upper().startswith("MSI") for a in adaylar)


def test_butce_filtresi_gercek_sayfa():
    q = ayristir("rtx 5080 125 bin altı")
    adaylar, elenen = katalog_ara_ayrintili(q, getir_sabit(HTML))
    assert adaylar and all(a.fiyat_araligi.alt <= 125000 for a in adaylar)
    assert any(e.gerekce.startswith("bütçe üstü") for e in elenen)


def test_ti_ve_super_elenir():
    html = sahte_arama_html([
        {"code": 1, "name": "Asus RTX 5080 TUF OC 16GB Ekran Kartı", "price": 150000},
        {"code": 2, "name": "Asus RTX 5080 Ti TUF OC 24GB Ekran Kartı", "price": 190000},
        {"code": 3, "name": "MSI RTX 5080 Super Ventus 24GB Ekran Kartı", "price": 200000},
        {"code": 4, "name": "MSI RTX 5070 Ti Ventus 16GB Ekran Kartı", "price": 90000},
    ])
    adaylar, elenen = katalog_ara_ayrintili(ayristir("rtx 5080"), lambda u: html)
    assert [a.id for a in adaylar] == ["akakce:1"]
    assert {e.kod for e in elenen} == {"2", "3", "4"}
    assert all(e.gerekce.startswith("model") for e in elenen)


# ---------------------------------------------------------------- RAM: özellik uyumu
RAM = sahte_arama_html([
    {"code": 10, "name": "G.Skill Trident Z5 Neo RGB 64GB (2x32GB) DDR5 6000MHz CL30 F5-6000J3040G32GX2-TZ5NR", "price": 61000},
    {"code": 11, "name": "Kingston Fury Beast 64GB (2x32GB) DDR5 6000MT/s CL36 KF560C36BBEK2-64", "price": 52000},
    {"code": 12, "name": "XPG Lancer 32GB (2x16GB) DDR5 6000MHz CL30 AX5U6000C3016G-DCLARBK", "price": 28000},
    {"code": 13, "name": "Corsair Vengeance 64GB (2x32GB) DDR5 6400MHz CL30 Siyah CMK64GX5M2B6400C30", "price": 64000},
    {"code": 14, "name": "Crucial 64GB DDR5 6000MHz CL30 SO-DIMM Laptop Bellek CT64G60C30S5", "price": 30000},
    {"code": 15, "name": "Corsair Vengeance 64GB (2x32GB) DDR5 6000MHz CL30 EXPO CMK64GX5M2B6000Z30", "price": 66000},
    {"code": 16, "name": "ASUS ROG Strix B650E-F Gaming WiFi DDR5 AM5 Anakart", "price": 12000},
    {"code": 17, "name": "Corsair DDR5 RAM Soğutucu Fan Kiti", "price": 900},
])


def test_ram_ozellik_uyumu_eler_ve_siralar():
    q = ayristir("AM5'e uyan QVL'li 2x32 ddr5 6000 cl30")
    adaylar, elenen = katalog_ara_ayrintili(q, lambda u: RAM)
    idler = [a.id for a in adaylar]
    assert set(idler) == {"akakce:10"}                # 15 (Corsair CMK64GX5M2B6000Z30) Gigabyte QVL'de yok → elenir
    nedenler = {e.kod: e.gerekce for e in elenen}
    assert "QVL" in nedenler["15"]
    assert "cl" in nedenler["11"] and "kit" in nedenler["12"] and "hiz_mts" in nedenler["13"]
    assert nedenler["14"] == "laptop/notebook" and "kategori" in nedenler["16"]
    assert "aksesuar" in nedenler["17"] or "kategori" in nedenler["17"]
    assert adaylar[0].mpn == "F5-6000J3040G32GX2-TZ5NR"
    assert adaylar[0].ozellikler["kit"] == "2x32" and adaylar[0].ozellikler["cl"] == 30


def test_ram_arama_metni_ve_url():
    q = ayristir("AM5'e uyan QVL'li 2x32 ddr5 6000 cl30")
    assert arama_metinleri(q) == ["ram ddr5 64gb 6000 cl30", "ram ddr5 64gb"]
    assert arama_url("ram ddr5 64gb 6000 cl30") == "https://www.akakce.com/arama/?q=ram+ddr5+64gb+6000+cl30"
    assert arama_url("güç kaynağı", 2).endswith("q=g%C3%BC%C3%A7+kayna%C4%9F%C4%B1&p=2")
    assert arama_metinleri(ayristir("rtx 5080 en ucuz")) == ["RTX 5080"]
    assert arama_metinleri(ayristir("Inno3D RTX 5080 X3")) == ["Inno3D RTX 5080"]
    assert arama_metinleri(ayristir("9800x3d kutulu")) == ["9800X3D"]
    assert arama_metinleri(ayristir("kırmızı çorap")) == ["kırmızı çorap"]       # tanınmayan: ham metin


def test_haric_istek_basliga_uygulanir():
    html = sahte_arama_html([
        {"code": 1, "name": "Corsair RM1000x 1000W 80+ Gold ATX 3.1 Tam Modüler PSU", "price": 7000},
        {"code": 2, "name": "Corsair RM1000e 1000W 80+ Gold ATX 3.1 RGB PSU", "price": 6500},
    ])
    adaylar = katalog_ara(ayristir("1000w gold atx 3.1 psu rgb olmasın"), lambda u: html)
    assert [a.id for a in adaylar] == ["akakce:1"]


def test_psu_aksesuar_ve_kasa_elenir():
    html = sahte_arama_html([
        {"code": 1, "name": "Asus TUF Gaming 1000G 1000W 80+ Gold ATX 3.1 PSU", "price": 7900},
        {"code": 2, "name": "Corsair Güç Kaynağı Kablo Seti Siyah", "price": 900},
        {"code": 3, "name": "Cooler Master Tower Kasa 1000W Güç Kaynağı Dahil", "price": 5000},
    ])
    adaylar, elenen = katalog_ara_ayrintili(ayristir("1000w gold atx 3.1 psu"), lambda u: html)
    assert [a.id for a in adaylar] == ["akakce:1"] and len(elenen) == 2


# ---------------------------------------------------------------- dayanıklılık
def test_getir_hatasi_bos_liste():
    def getir(url):
        raise TimeoutError("ağ yok")

    assert katalog_ara(ayristir("rtx 5080"), getir) == []


def test_bos_ve_bozuk_html():
    assert katalog_ara(ayristir("rtx 5080"), lambda u: "") == []
    assert katalog_ara(ayristir("rtx 5080"), lambda u: "<html><astro-island props='{bozuk'></astro-island>") == []
    assert kartlar(None) == []


def test_urun_disi_tur_katalog_aramaz():
    cagri = []
    q, adaylar = tanimla("istanbul tokyo uçuş", "ucus", lambda u: cagri.append(u) or HTML)
    assert adaylar == [] and cagri == [] and q.tur == "ucus"


def test_sade_sorguya_dusme():
    sayfalar = {"ilk": "", "sade": RAM}
    cagri = []

    def getir(url):
        cagri.append(url)
        return sayfalar["ilk"] if len(cagri) == 1 else sayfalar["sade"]

    q = ayristir("Corsair 2x32 ddr5 6000 cl30")
    adaylar = katalog_ara(q, getir)
    assert len(cagri) == 2 and "cl30" in cagri[0] and cagri[1].endswith("q=Corsair+ram+ddr5+64gb")
    assert [a.id for a in adaylar] == ["akakce:15"]


def test_tanimla_uctan_uca_gercek_sayfa():
    q, adaylar = tanimla("rtx 5080 en ucuz", "urun", getir_sabit(HTML))
    assert q.kategori == "gpu" and q.en_ucuz and len(adaylar) >= 10
    assert adaylar[0].fiyat_araligi.alt == min(a.fiyat_araligi.alt for a in adaylar)


# ---------------------------------------------------------------- MPN
@pytest.mark.parametrize("baslik,model,beklenen", [
    ("MSI RTX 5080 16G Ventus 3X OC G5080-16V3C 256 Bit GDDR7 16 GB Ekran Kartı", "RTX 5080", "G5080-16V3C"),
    ("AMD Ryzen 7 9800X3D 4.7 GHz 96MB AM5 İşlemci 100-100001084WOF", "9800X3D", "100-100001084WOF"),
    ("Corsair RM1000x 1000W 80+ Gold ATX 3.1", None, "RM1000x"),
    ("Samsung 9100 PRO 1TB M.2 NVMe PCIe 5.0 SSD MZ-VAP1T0BW", None, "MZ-VAP1T0BW"),
    ("Kingston Fury Beast 64GB (2x32GB) DDR5 6000MT/s CL30", None, None),
    ("RTX 5080 16GB GDDR7 Ekran Kartı", "RTX 5080", None),
])
def test_mpn_cikar(baslik, model, beklenen):
    assert mpn_cikar(baslik, model) == beklenen


def test_kutulu_istenince_tray_geri_duser():
    from kazi.tanimlama.ayristir import ayristir
    from kazi.tanimlama.katalog import uyum_puani
    s = ayristir("9800x3d kutulu")
    kutulu = uyum_puani(s, "AMD Ryzen 7 9800X3D Sekiz Çekirdek 4.7 GHz Kutulu İşlemci")[0]
    belirsiz = uyum_puani(s, "AMD Ryzen 7 9800X3D Sekiz Çekirdek 4.7 GHz İşlemci")[0]
    tray = uyum_puani(s, "AMD Ryzen 7 9800X3D Sekiz Çekirdek 4.7 GHz Kutusuz Fansız İşlemci")[0]
    assert kutulu > belirsiz > tray > -10


def test_qvl_istenince_qvl_disi_ram_elenir(monkeypatch):
    from kazi.tanimlama import katalog
    from kazi.tanimlama.ayristir import ayristir
    kartlar = [katalog.Kart("1", "KLEVV Cras V Rgb 64GB (2x32) 6000 MHz CL30 KD5BGUA80-60A300J DDR5 Ram", 9000, 5, "u1", None),
               katalog.Kart("2", "G.Skill Trident Z5 Neo 64GB (2x32) 6000MHz CL30 F5-6000J3040G32GX2-TZ5NR DDR5", 9500, 5, "u2", None)]
    monkeypatch.setattr(katalog, "kartlar", lambda html: kartlar)
    monkeypatch.setattr("kazi.siteler.qvl.eslestir", lambda tur, ad: {"pn": "x"} if "G.Skill" in ad else None)
    adaylar, elenen = katalog.katalog_ara_ayrintili(ayristir("qvl'li 2x32 ddr5 6000 cl30"), lambda u: "<html/>")
    assert [a.id for a in adaylar] == ["akakce:2"]
    assert elenen[0].kod == "1" and "QVL" in elenen[0].gerekce
