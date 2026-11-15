"""Kural tabanlı ayrıştırıcı (LLM'siz) — ≥30 gerçekçi Türkçe/İngilizce cümle."""
import pytest

from kazi.tanimlama.ayristir import YapisalSorgu, ayristir, katla, kucult

# (metin, beklenen). Anahtarlar: kategori/marka/model/haric/butce_alt/butce_ust/en_ucuz birebir,
# "oz" = ozellikler'in alt kümesi, "guven_min"/"guven_max", "kalan" = kalan_sozcukler alt kümesi.
ORNEKLER = [
    ("rtx 5080 en ucuz", dict(kategori="gpu", model="RTX 5080", en_ucuz=True, guven_min=0.5)),
    ("AM5'e uyan QVL'li 2x32 ddr5 6000 cl30",
     dict(kategori="ram", oz=dict(soket="AM5", qvl=True, kit="2x32", kapasite_gb=64, bellek_tipi="DDR5",
                                  hiz_mts=6000, cl=30), guven_min=0.5)),
    ("1000w gold atx 3.1 psu", dict(kategori="psu", oz=dict(watt=1000, atx="3.1"), guven_min=0.5)),
    ("9800x3d kutulu", dict(kategori="cpu", model="9800X3D", marka="AMD", oz=dict(paket="kutulu"), guven_min=0.5)),
    ("5080 ekran kartı en ucuz", dict(kategori="gpu", model="RTX 5080", en_ucuz=True, guven_min=0.5)),
    ("Inno3D RTX 5080 X3", dict(kategori="gpu", marka="Inno3D", model="RTX 5080", guven_min=0.5, kalan=["x3"])),
    ("Asus TUF Gaming RTX 5080 OC 16GB laptop olmasın",
     dict(kategori="gpu", marka="Asus", model="RTX 5080", haric=["laptop"], oz=dict(vram_gb=16))),
    ("RX 9070 XT 30 bin altı", dict(kategori="gpu", model="RX 9070 XT", butce_ust=30000.0)),
    ("rtx 5070 ti 50.000 TL'ye kadar", dict(kategori="gpu", model="RTX 5070 Ti", butce_ust=50000.0)),
    ("GeForce RTX 4090 hazır sistem hariç", dict(kategori="gpu", model="RTX 4090", haric=["hazır sistem"])),
    ("ekran kartı 25 bin altı", dict(kategori="gpu", butce_ust=25000.0, guven_max=0.49)),
    ("Ryzen 7 9800X3D işlemci", dict(kategori="cpu", model="9800X3D", oz=dict(seri="Ryzen 7"), guven_min=0.5)),
    ("ryzen 9 9950x3d en ucuz", dict(kategori="cpu", model="9950X3D", en_ucuz=True)),
    ("Core Ultra 9 285K", dict(kategori="cpu", marka="Intel", model="285K", oz=dict(seri="Core Ultra 9"))),
    ("i7-14700K kutulu", dict(kategori="cpu", marka="Intel", model="i7-14700K", oz=dict(paket="kutulu"))),
    ("32GB 2×16 DDR5 6000 MT/s CL36 RAM",
     dict(kategori="ram", oz=dict(kit="2x16", kapasite_gb=32, bellek_tipi="DDR5", hiz_mts=6000, cl=36))),
    ("64gb ddr5 6000mhz ram", dict(kategori="ram", oz=dict(kapasite_gb=64, hiz_mts=6000, bellek_tipi="DDR5"))),
    ("DDR4 3200 CL16 16GB 2x8", dict(kategori="ram", oz=dict(bellek_tipi="DDR4", hiz_mts=3200, cl=16, kit="2x8"))),
    ("Corsair Vengeance 2x32 6000 cl30 expo 7500 TL'den fazla",
     dict(kategori="ram", marka="Corsair", butce_alt=7500.0, oz=dict(kit="2x32", cl=30, hiz_mts=6000, profil="EXPO"))),
    ("Gigabyte X870 Aorus Elite WiFi 7 anakart",
     dict(kategori="anakart", marka="Gigabyte", oz=dict(yonga_seti="X870", wifi="7"), kalan=["aorus", "elite"])),
    ("B650 anakart AM5 wifi", dict(kategori="anakart", oz=dict(yonga_seti="B650", soket="AM5"))),
    ("z890 anakart 20 bin altı", dict(kategori="anakart", butce_ust=20000.0, oz=dict(yonga_seti="Z890"))),
    ("X870E ATX anakart notebook değil",
     dict(kategori="anakart", haric=["laptop"], oz=dict(yonga_seti="X870E", form_faktor="ATX"))),
    ("1TB NVMe M.2 SSD gen4",
     dict(kategori="ssd", oz=dict(kapasite_gb=1000, arayuz="NVMe", form_faktor="M.2", pcie="4.0"))),
    ("2tb ssd pcie 5.0 harici olmasın", dict(kategori="ssd", haric=["harici"], oz=dict(kapasite_gb=2000, pcie="5.0"))),
    ("Samsung 9100 Pro 1TB", dict(kategori="ssd", marka="Samsung", oz=dict(kapasite_gb=1000))),
    ("PCIe Gen5 NVMe 2TB", dict(kategori="ssd", oz=dict(pcie="5.0", arayuz="NVMe", kapasite_gb=2000))),
    ("güç kaynağı 850w 80 plus gold", dict(kategori="psu", oz={"watt": 850, "80plus": "Gold"})),
    ("750W 80+ Bronze PSU 4 bin TL altı",
     dict(kategori="psu", butce_ust=4000.0, oz={"watt": 750, "80plus": "Bronze"})),
    ("PSU 1000 W ATX 3.0 PCIe 5.0", dict(kategori="psu", oz=dict(watt=1000, atx="3.0", pcie="5.0"))),
    ("sıvı soğutma 360mm aio", dict(kategori="sogutucu", oz=dict(radyator_mm=360))),
    ("hava soğutucu am5", dict(kategori="sogutucu", oz=dict(soket="AM5"))),
    ("mid tower kasa beyaz", dict(kategori="kasa", oz=dict(form_faktor="Mid Tower", renk="Beyaz"))),
    ("27 inç 240hz oled monitör",
     dict(kategori="monitor", oz=dict(ekran_inc=27, yenileme_hz=240, panel="OLED"))),
    ("LG 32 inç 4K IPS monitör 15 bin ile 25 bin arası",
     dict(kategori="monitor", marka="LG", butce_alt=15000.0, butce_ust=25000.0, oz=dict(cozunurluk="4K"))),
    ("no laptop rtx 5090 cheapest", dict(kategori="gpu", model="RTX 5090", haric=["laptop"], en_ucuz=True)),
    ("graphics card under 60k", dict(kategori="gpu", butce_ust=60000.0)),
    ("memory ddr5 64gb 6000 cl30 exclude sodimm",
     dict(kategori="ram", haric=["so-dimm"], oz=dict(kapasite_gb=64, cl=30))),
    ("ekran kartı 16gb gddr7 beyaz", dict(kategori="gpu", oz=dict(vram_gb=16, vram_tipi="GDDR7", renk="Beyaz"))),
    ("işlemci am5 en az 10 bin", dict(kategori="cpu", butce_alt=10000.0, oz=dict(soket="AM5"))),
    ("rtx 5080 laptop, notebook ve hazır sistem hariç",
     dict(kategori="gpu", model="RTX 5080", haric=["laptop", "hazır sistem"])),
    ("ram 25.000 TL'ye kadar", dict(kategori="ram", butce_ust=25000.0)),
    ("kırmızı çorap", dict(kategori=None, guven_max=0.2)),
]


def _kontrol(q: YapisalSorgu, bek: dict):
    for k in ("kategori", "marka", "model", "butce_alt", "butce_ust", "en_ucuz"):
        if k in bek:
            assert getattr(q, k) == bek[k], f"{k}: {getattr(q, k)!r} != {bek[k]!r}"
    for k, v in bek.get("oz", {}).items():
        assert q.ozellikler.get(k) == v, f"ozellik {k}: {q.ozellikler.get(k)!r} != {v!r} ({q.ozellikler})"
    for h in bek.get("haric", []):
        assert h in q.haric, f"haric {h!r} yok: {q.haric}"
    for w in bek.get("kalan", []):
        assert w in q.kalan_sozcukler, f"kalan {w!r} yok: {q.kalan_sozcukler}"
    if "guven_min" in bek:
        assert q.guven >= bek["guven_min"], q.guven
    if "guven_max" in bek:
        assert q.guven <= bek["guven_max"], q.guven


def test_en_az_30_ornek():
    assert len(ORNEKLER) >= 30


@pytest.mark.parametrize("metin,bek", ORNEKLER, ids=[m[:48] for m, _ in ORNEKLER])
def test_ornek_cumleler(metin, bek):
    _kontrol(ayristir(metin), bek)


# ---------------------------------------------------------------- Türkçe büyük/küçük harf
def test_turkce_kucuk_harf():
    assert kucult("IŞIK") == "ışık"
    assert kucult("İSTANBUL") == "istanbul"
    assert kucult("DİZÜSTÜ BİLGİSAYAR") == "dizüstü bilgisayar"
    assert kucult("ISPARTA") == "ısparta"
    assert katla("İŞLEMCİ") == "islemci" and katla("Işık") == "isik"
    assert len(katla("İİİ ĞÜŞ")) == len("İİİ ĞÜŞ")                # 1:1 (konumlar korunur)


def test_buyuk_harf_turkce_istek():
    q = ayristir("İŞLEMCİ RYZEN 7 9800X3D DİZÜSTÜ OLMASIN")
    assert q.kategori == "cpu" and q.model == "9800X3D" and q.haric == ["laptop"]
    q = ayristir("GÜÇ KAYNAĞI 1000W")
    assert q.kategori == "psu" and q.ozellikler["watt"] == 1000
    q = ayristir("EKRAN KARTI RTX 5080 TEŞHİR OLMASIN")
    assert q.kategori == "gpu" and q.haric == ["teşhir"]


def test_kalan_sozcukler_turkce_kucuk_harfli():
    q = ayristir("IŞIK İLE İSTANBUL")
    assert q.kalan_sozcukler == ["ışık", "istanbul"]
    assert q.kategori is None and q.guven < 0.5


def test_roman_rakam_bozulmaz():
    assert "iii" in ayristir("Arctic Liquid Freezer III 360").kalan_sozcukler


# ---------------------------------------------------------------- ayrıntılar
def test_desenler_birlikte():
    q = ayristir("AM5'e uyan QVL'li 2x32 ddr5 6000 cl30")
    assert q.kalan_sozcukler == []                  # 'e, 'li, uyan artık sayılmaz
    assert q.ozellikler["kapasite_gb"] == 64


@pytest.mark.parametrize("metin", ["2x32 ddr5", "2×32 ddr5", "2 x 32gb ddr5", "32gb x2 ddr5", "2X32GB DDR5"])
def test_kit_yazimlari(metin):
    q = ayristir(metin)
    assert q.ozellikler["kit"] == "2x32" and q.ozellikler["kapasite_gb"] == 64


@pytest.mark.parametrize("metin,hiz", [("ddr5 6000mhz", 6000), ("ddr5 6000 mt/s", 6000), ("ddr5-6400", 6400),
                                       ("ddr5 6000 MT/S", 6000), ("ddr4 3600 mhz", 3600)])
def test_hiz_yazimlari(metin, hiz):
    assert ayristir(metin).ozellikler["hiz_mts"] == hiz


@pytest.mark.parametrize("metin,deger", [("80+ gold 1000w", "Gold"), ("80 plus gold 1000w", "Gold"),
                                         ("80 PLUS Platinum 850w", "Platinum"), ("1000w gold psu", "Gold")])
def test_80plus(metin, deger):
    assert ayristir(metin).ozellikler["80plus"] == deger


@pytest.mark.parametrize("metin,deger", [("pcie 5.0 ssd", "5.0"), ("gen5 ssd", "5.0"), ("PCIe 4 nvme", "4.0"),
                                         ("pci-e 5.0 x4 ssd", "5.0")])
def test_pcie(metin, deger):
    assert ayristir(metin).ozellikler["pcie"] == deger


@pytest.mark.parametrize("metin,alt,ust", [
    ("30 bin altı", None, 30000.0), ("30 bin TL altında ram", None, 30000.0), ("25.000 TL'ye kadar", None, 25000.0),
    ("30000 TL altı", None, 30000.0), ("30k altı", None, 30000.0), ("en fazla 12,5 bin", None, 12500.0),
    ("15 bin üstü", 15000.0, None), ("20-30 bin", 20000.0, 30000.0), ("20 ile 30 bin arası", 20000.0, 30000.0),
    ("1,5 milyon TL'ye kadar", None, 1_500_000.0), ("kasa", None, None),
])
def test_butce(metin, alt, ust):
    q = ayristir(metin + " ssd")
    assert (q.butce_alt, q.butce_ust) == (alt, ust)


def test_sayi_butce_sanilmaz():
    q = ayristir("1000w gold psu 64gb ddr5 6000 cl30")
    assert q.butce_alt is None and q.butce_ust is None


def test_en_ucuz_varyantlari():
    assert ayristir("rtx 5080 en ucuz").en_ucuz
    assert ayristir("en uygun fiyatlı 1tb ssd").en_ucuz
    assert ayristir("cheapest rtx 5080").en_ucuz
    assert not ayristir("rtx 5080").en_ucuz


def test_haric_varyantlari():
    assert ayristir("rtx 5080 laptop olmasın").haric == ["laptop"]
    assert ayristir("rtx 5080 hazır sistem hariç").haric == ["hazır sistem"]
    assert ayristir("rtx 5080 oyuncu bilgisayarı olmasın").haric == ["hazır sistem"]
    assert ayristir("rtx 5080 ikinci el istemiyorum").haric == ["ikinci el"]
    assert ayristir("rtx 5080 -laptop").haric == ["laptop"]
    assert ayristir("rtx 5080 without outlet").haric == ["outlet"]
    assert ayristir("ram rgb olmasın").haric == ["rgb"]
    assert ayristir("rtx 5080 laptop olmasın").kategori == "gpu"        # 'laptop' kategori sanılmaz


def test_haric_oz_kategoriyi_bozmaz():
    q = ayristir("anakart bundle hariç wifi 7")
    assert q.kategori == "anakart" and q.haric == ["bundle"] and q.ozellikler["wifi"] == "7"


def test_ham_metin_saklanir_ve_tur():
    q = ayristir("  rtx 5080  ")
    assert q.ham == "rtx 5080" and q.tur == "urun"
    assert ayristir("Tokyo uçuşu aralık", tur="ucus").tur == "ucus"


def test_bos_ve_anlamsiz():
    assert ayristir("").guven == 0.0 and ayristir("   ").kategori is None
    assert ayristir("???").kategori is None


def test_guven_esigi_gercekci():
    assert ayristir("rtx 5080").guven >= 0.5
    assert ayristir("ekran kartı").guven < 0.5          # çok genel → LLM'e gider
    assert ayristir("Corsair RM1000x").guven < 0.5      # kategori sözcüğü yok


def test_sozluk_json_uyumlu():
    import json
    json.dumps(ayristir("AM5 2x32 ddr5 6000 cl30 qvl 30 bin altı").sozluk())
