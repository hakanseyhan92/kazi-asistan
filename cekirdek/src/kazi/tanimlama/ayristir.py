"""LLM'siz Türkçe/İngilizce ürün isteği ayrıştırıcı (SARTNAME §4.1).

`ayristir(metin, tur="urun") -> YapisalSorgu`

Metin Türkçe harf kurallarına göre küçültülür (İ→i, I→ı) ve ardından aksan-düzleştirilir
(ı→i, ş→s, ...). Düzleştirme karakter-karaktere (1:1) olduğundan eşleşme konumları özgün metinle
aynı kalır; böylece `marka`/`kalan_sozcukler` özgün yazımla geri verilebilir.

Çıkarım sırası: bütçe → hariç → özellik kalıpları → kategori sözcükleri → marka/model → artık.
Eşleşen her parça çalışma metninden silinir; kalanlar `kalan_sozcukler` olur.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

# ----------------------------------------------------------------------------- veri sınıfı


@dataclass
class YapisalSorgu:
    tur: str = "urun"
    kategori: str | None = None
    marka: str | None = None
    model: str | None = None
    ozellikler: dict[str, Any] = field(default_factory=dict)
    haric: list[str] = field(default_factory=list)
    butce_alt: float | None = None
    butce_ust: float | None = None
    guven: float = 0.0
    kalan_sozcukler: list[str] = field(default_factory=list)
    en_ucuz: bool = False
    ham: str = ""

    def sozluk(self) -> dict[str, Any]:
        return asdict(self)


KATEGORILER = ("gpu", "cpu", "ram", "anakart", "ssd", "psu", "kasa", "sogutucu", "monitor")

# ----------------------------------------------------------------------------- Türkçe harf


def kucult(s: str) -> str:
    """Türkçe küçük harf: İ→i, I→ı (Python'un `lower()` 'İ'yi bozar)."""
    return s.replace("İ", "i").replace("I", "ı").lower()


_KATLA = str.maketrans({
    "ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c", "â": "a", "î": "i", "û": "u",
    "×": "x", "’": "'", "‘": "'", "´": "'", "`": "'", "ʼ": "'", "\u00a0": " ", "–": "-", "—": "-",
})


def katla(s: str) -> str:
    """Küçült + aksanları düzleştir. Uzunluk korunur (1:1)."""
    return kucult(s).translate(_KATLA)


# ----------------------------------------------------------------------------- çalışma metni


class _Metin:
    def __init__(self, orijinal: str):
        self.orj = orijinal
        self.t = katla(orijinal)
        assert len(self.t) == len(orijinal)

    def sil(self, bas: int, son: int) -> None:
        self.t = self.t[:bas] + " " * (son - bas) + self.t[son:]

    def bul(self, rx: re.Pattern[str]) -> re.Match[str] | None:
        m = rx.search(self.t)
        if m:
            self.sil(m.start(), m.end())
        return m

    def hepsi(self, rx: re.Pattern[str]) -> list[re.Match[str]]:
        ms = list(rx.finditer(self.t))
        for m in ms:
            self.sil(m.start(), m.end())
        return ms

    def parca(self, bas: int, son: int) -> str:
        s = self.orj[bas:son].strip()
        if re.fullmatch(r"[IVXivx]+", s):             # roman rakam (III): Türkçe ı kuralı uygulanmaz
            return s.lower()
        return kucult(s)


def _rx(desen: str) -> re.Pattern[str]:
    return re.compile(desen)


# ----------------------------------------------------------------------------- bütçe
_SAYI = r"(?:\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d+)?)"
_BIRIM = r"(?:bin|k|milyon)"
_PARA = r"(?:tl|₺|lira|try)"
_EK = r"(?:'?(?:y?[ea]|n?in))"          # 'ye / 'e / 'a / in / nin

_B_ARALIK = _rx(
    rf"(?<![\w.,])(?P<a>{_SAYI})\s*(?P<ab>{_BIRIM})?\s*(?:{_PARA})?\s*(?:-|ile|ila)\s*"
    rf"(?P<b>{_SAYI})\s*(?:(?P<bb>{_BIRIM})\s*(?:{_PARA})?|(?:{_PARA})|(?=\s*arasi))(?:\s*arasi(?:nda)?)?(?![\w])"
)
_B_UST = _rx(
    rf"(?<![\w.,])(?P<a>{_SAYI})\s*(?P<ab>{_BIRIM})?\s*(?:{_PARA})?\s*{_EK}?\s*"
    r"(?:kadar|alti(?:nda|na|ndaki)?|'?d[ea]n\s+az|ve\s+alti|veya\s+alti|altinda)(?![a-z0-9])"
)
_B_UST_ON = _rx(
    rf"(?:en\s+fazla|en\s+cok|maks(?:imum)?|max|butce(?:m)?|limit|<=?|under|below|up\s+to|less\s+than|within)\s*:?\s*"
    rf"(?P<a>{_SAYI})\s*(?P<ab>{_BIRIM})?\s*(?:{_PARA})?(?![a-z0-9])"
)
_B_ALT = _rx(
    rf"(?<![\w.,])(?P<a>{_SAYI})\s*(?P<ab>{_BIRIM})?\s*(?:{_PARA})?\s*{_EK}?\s*"
    r"(?:ve\s+ustu|veya\s+ustu|ustunde|ustu|uzerinde|uzeri|ustundeki|'?d[ea]n\s+fazla)(?![a-z0-9])"
)
_B_ALT_ON = _rx(
    rf"(?:en\s+az|min(?:imum)?|>=?|over|above|more\s+than|at\s+least)\s*:?\s*(?P<a>{_SAYI})\s*(?P<ab>{_BIRIM})?\s*(?:{_PARA})?(?![a-z0-9])"
)
_B_GENEL = _rx(
    rf"(?<![\w.,])(?P<a>{_SAYI})\s*(?P<ab>{_BIRIM})?\s*(?:{_PARA})\s*{_EK}?\s*(?:butce|butceli|butcesi)?(?![a-z0-9])"
)

_EN_UCUZ = _rx(
    r"en\s+(?:ucuz|uygun|dusuk|ekonomik)\w*(?:\s+fiyat\w*)?|(?<![a-z])ucuz\w*|cheapest|lowest\s+price|"
    r"en\s+iyi\s+fiyat\w*|best\s+price"
)


def _sayi(s: str, birim: str | None) -> float:
    s = s.strip()
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        v = float(s.replace(".", ""))
    else:
        v = float(s.replace(",", "."))
    if birim in ("bin", "k"):
        v *= 1000
    elif birim == "milyon":
        v *= 1_000_000
    return float(round(v, 2))


def _butce(c: _Metin) -> tuple[float | None, float | None]:
    alt = ust = None
    m = c.bul(_B_ARALIK)
    if m:
        birim = m.group("bb") or m.group("ab")
        a, b = _sayi(m.group("a"), m.group("ab") or birim), _sayi(m.group("b"), m.group("bb") or birim)
        alt, ust = min(a, b), max(a, b)
        return alt, ust
    m = c.bul(_B_UST) or c.bul(_B_UST_ON)
    if m:
        ust = _sayi(m.group("a"), m.group("ab"))
    m = c.bul(_B_ALT) or c.bul(_B_ALT_ON)
    if m:
        alt = _sayi(m.group("a"), m.group("ab"))
    if ust is None and alt is None:
        m = c.bul(_B_GENEL)             # "30 bin TL" -> tavan
        if m:
            ust = _sayi(m.group("a"), m.group("ab"))
    return alt, ust


# ----------------------------------------------------------------------------- hariç
# (düzleştirilmiş desen, çıktı)
_HARIC_SOZLUK: list[tuple[str, str]] = [
    (r"laptop\w*|notebook\w*|diz\s*ustu\w*", "laptop"),
    (r"hazir\s+(?:sistem|pc|bilgisayar|paket)\w*|oyuncu\s+bilgisayar\w*|oyun\s+bilgisayar\w*|"
     r"masaustu\s+(?:bilgisayar|pc)\w*|gaming\s+pc|prebuilt|all\s*-?\s*in\s*-?\s*one", "hazır sistem"),
    (r"ikinci\s*el\w*|2\.\s*el|kullanilmis", "ikinci el"),
    (r"outlet", "outlet"),
    (r"tesh[ie]r\w*", "teşhir"),
    (r"yenilenmis\w*|refurb\w*|refurbished", "yenilenmiş"),
    (r"so\s*-?\s*dimm\w*", "so-dimm"),
    (r"waterblock\w*|su\s+blo[gk]\w*", "waterblock"),
    (r"bundle|kombo|combo", "bundle"),
]
_HARIC_KEY = (r"(?:olmasin|olmayan|olmayacak|olmadan|haric|disinda|istemiyorum|istemiyoruz|istemem|"
              r"degil|harici\s+olsun)")
_HARIC_SONEK = [(re.compile(rf"(?<![a-z0-9])(?:{d})\s*$"), c) for d, c in _HARIC_SOZLUK]
_HARIC_ON = _rx(rf"(?<![a-z0-9])(?:{_HARIC_KEY})(?![a-z0-9])")
_HARIC_INGILIZCE = _rx(r"(?<![a-z0-9])(?:no|without|not|exclude|excluding|except)\s+(?=[a-z])")
_HARIC_EKSI = _rx(r"(?<![\w])-(?=[a-z])")
_HARIC_BIR_SOZ = _rx(r"[a-z0-9][a-z0-9.+-]*")
_HARIC_DURAK = {"ve", "veya", "ile", "bir", "bu", "su", "ama", "hem", "de", "da", "ki", "ve/veya", "olsun"}


def _haric_cikar(c: _Metin) -> list[str]:
    cikti: list[str] = []

    def ekle(terim: str) -> None:
        if terim and terim not in cikti:
            cikti.append(terim)

    # 1) "X olmasın / X hariç / A, B ve C hariç"
    while True:
        m = _HARIC_ON.search(c.t)
        if not m:
            break
        bas = m.start()
        onceki = c.t[:bas]
        pos = len(onceki.rstrip())
        silinecek_bas = pos
        bulundu = 0
        while True:
            on = onceki[:pos]
            eslesti = False
            for rx, canon in _HARIC_SONEK:
                mm = rx.search(on)
                if mm:
                    ekle(canon)
                    pos = mm.start()
                    eslesti = True
                    break
            if not eslesti and bulundu == 0:
                # bilinmeyen: son sözcük
                kelimeler = list(re.finditer(r"[a-z0-9][a-z0-9.+-]*", on))
                if kelimeler and kelimeler[-1].group(0) not in _HARIC_DURAK:
                    k = kelimeler[-1]
                    ekle(c.parca(k.start(), k.end()))
                    pos = k.start()
                    eslesti = True
            if not eslesti:
                break
            bulundu += 1
            silinecek_bas = pos
            # ayraç: virgül / ve / veya
            kalan = onceki[:pos]
            ayrac = re.search(r"(?:[\s,;/]+(?:ve|veya|ile)[\s,;/]+|[\s,;/]+)$", kalan)
            if not ayrac or not kalan[:ayrac.start()].strip():
                break
            pos = ayrac.start()
        c.sil(silinecek_bas, m.end())
    # 2) "no laptop", "without rtx"
    while True:
        m = _HARIC_INGILIZCE.search(c.t)
        if not m:
            break
        sonra = c.t[m.end():]
        eslesti = False
        for d, canon in _HARIC_SOZLUK:
            mm = re.match(rf"(?:{d})", sonra)
            if mm:
                ekle(canon)
                c.sil(m.start(), m.end() + mm.end())
                eslesti = True
                break
        if not eslesti:
            mm = _HARIC_BIR_SOZ.match(sonra)
            if mm:
                ekle(c.parca(m.end(), m.end() + mm.end()))
                c.sil(m.start(), m.end() + mm.end())
            else:
                c.sil(m.start(), m.end())
    # 3) "-laptop"
    while True:
        m = _HARIC_EKSI.search(c.t)
        if not m:
            break
        mm = _HARIC_BIR_SOZ.match(c.t[m.end():])
        if not mm:
            c.sil(m.start(), m.end())
            continue
        terim = c.parca(m.end(), m.end() + mm.end())
        for d, canon in _HARIC_SOZLUK:
            if re.fullmatch(d, katla(terim)):
                terim = canon
                break
        ekle(terim)
        c.sil(m.start(), m.end() + mm.end())
    return cikti


# ----------------------------------------------------------------------------- özellik kalıpları
_ESIT_MODUL = {4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256}
_KIT = _rx(r"(?<![0-9.x])(?P<n>[1-8])\s*x\s*(?P<m>\d{1,3})\s*(?:gb)?(?![0-9a-z.])")
_KIT_TERS = _rx(r"(?<![0-9.])(?P<m>\d{1,3})\s*gb\s*x\s*(?P<n>[1-8])(?![0-9a-z])")
_DDR = _rx(r"(?<![a-z])(?P<g>g?)ddr\s*(?P<v>[2-7]x?)(?:\s*-\s*(?P<h>\d{4,5}))?(?![a-z0-9])")
_HIZ = _rx(r"(?<![0-9.])(?P<v>\d{3,5})\s*(?:mhz|mt\s*/\s*s|mts|mt)(?![a-z])")
_CL = _rx(r"(?<![a-z0-9])cl\s*-?\s*(?P<v>\d{2})(?![0-9])")
_C_KISA = _rx(r"(?<![a-z0-9])c(?P<v>\d{2})(?![a-z0-9])")
_HIZ_CIPLAK = _rx(r"(?<![0-9.,])(?P<v>\d{4})(?![0-9])")
_PROFIL = _rx(r"(?<![a-z0-9])(?P<v>expo|xmp)(?![a-z0-9])")
_KAPASITE = _rx(r"(?<![0-9.,])(?P<v>\d{1,4})\s*(?P<b>gb|tb)(?![a-z])")
_WATT = _rx(r"(?<![0-9.,])(?P<v>\d{3,4})\s*w(?:att)?(?![a-z0-9])")
_SERT80 = _rx(r"(?<![a-z0-9])80\s*(?:\+|plus)\s*(?P<d>white|bronze|silver|gold|platinum|titanium)?(?![a-z0-9])")
_SERT_CIPLAK = _rx(r"(?<![a-z0-9])(?P<d>bronze|gold|platinum|titanium)(?![a-z0-9])")
_ATX_SURUM = _rx(r"(?<![a-z0-9])atx\s*(?P<v>\d(?:\.\d)?)(?![0-9])")
_FORM = _rx(r"(?<![a-z0-9])(?P<f>e\s*-?\s*atx|micro\s*-?\s*atx|m\s*-?\s*atx|mini\s*-?\s*itx|itx|"
            r"mid\s*-?\s*tower|full\s*-?\s*tower|mini\s*-?\s*tower)(?![a-z0-9])")
_PCIE = _rx(r"(?<![a-z0-9])pci\s*-?\s*e(?:xpress)?\s*(?:gen\s*)?(?P<v>\d(?:\.\d)?)(?:\s*x\s*(?:4|8|16))?(?![0-9a-z])")
_GEN = _rx(r"(?<![a-z0-9])gen\s*-?\s*(?P<v>[3-6])(?:\s*x\s*(?:4|8|16)|\.0)?(?![0-9a-z])")
_SOKET = _rx(r"(?<![a-z0-9])(?P<v>am[345]|lga\s*-?\s*(?:1700|1851|1200|1151|1155)|str5|tr5|sp[35])(?![a-z0-9])")
_YONGA = _rx(r"(?<![a-z0-9])(?P<v>a[2-6]20|b[2-9]\d0|x[3-9]\d0|z[3-9]\d0|h[3-9]\d0)(?P<e>e|m)?(?![a-z0-9])")
_WIFI = _rx(r"(?<![a-z0-9])wi\s*-?\s*fi\s*-?\s*(?P<v>\d[e]?)?(?![a-z0-9])")
_QVL = _rx(r"(?<![a-z0-9])qvl\w*")
_INC = _rx(r"(?<![0-9.])(?P<v>\d{2}(?:[.,]\d)?)\s*(?:inc|inch|inclik|\")(?![a-z])")
_HZ = _rx(r"(?<![0-9.])(?P<v>\d{2,3})\s*hz(?![a-z])")
_COZUNURLUK = _rx(r"(?<![a-z0-9])(?P<v>8k|4k|2k|qhd|wqhd|uhd|fhd|1440p|1080p|2160p|ultrawide)(?![a-z0-9])")
_PANEL = _rx(r"(?<![a-z0-9])(?P<v>qd\s*-?\s*oled|oled|mini\s*-?\s*led|ips)(?![a-z0-9])")
_RADYATOR = _rx(r"(?<![0-9.])(?P<v>120|140|240|280|360|420)\s*mm(?![a-z])")
_RADYATOR_CIPLAK = _rx(r"(?<![0-9.a-z])(?P<v>240|280|360|420)(?![0-9a-z])")
_NVME = _rx(r"(?<![a-z0-9])nvme(?![a-z0-9])")
_SATA = _rx(r"(?<![a-z0-9])sata(?:\s*3)?(?![a-z0-9])")
_M2 = _rx(r"(?<![a-z0-9])m\.?\s?2(?![a-z0-9.])")
_PAKET_KUTULU = _rx(r"(?<![a-z0-9])(?:kutulu|boxed|box|retail)(?![a-z0-9])")
_PAKET_TRAY = _rx(r"(?<![a-z0-9])(?:tray|fansiz|oem)(?![a-z0-9])")
_RENK = _rx(r"(?<![a-z0-9])(?P<v>beyaz|siyah|white|black)(?![a-z0-9])")

# GPU
_GPU_MODEL = _rx(r"(?<![a-z0-9])(?P<s>rtx|gtx|rx|arc)\s*-?\s*(?P<n>\d{3,4})"
                 r"(?:\s*(?P<k>ti\s+super|ti|super|xtx|xt|gre))?(?![a-z0-9])")
_GPU_CIPLAK = _rx(r"(?<![a-z0-9.])(?P<n>[345]0[5-9]0)(?:\s*(?P<k>ti\s+super|ti|super))?(?![a-z0-9])")
# CPU
_RYZEN = _rx(r"(?<![a-z0-9])ryzen\s*(?:(?P<s>[3579])(?![0-9])\s*)?(?:(?P<m>\d{4}(?:x3d|xt|x|gt|ge|g|f)?)(?![a-z0-9]))?")
_X3D_CIPLAK = _rx(r"(?<![a-z0-9])(?P<m>[3579]\d{3}\s*(?:x3d|xt|x))(?![a-z0-9])")
_ULTRA = _rx(r"(?<![a-z0-9])(?:core\s*)?ultra\s*(?P<s>[3579])(?:\s*(?P<m>\d{3}[a-z]{0,2}))?(?![a-z0-9])")
_INTEL_I = _rx(r"(?<![a-z0-9])(?:core\s*)?i(?P<s>[3579])(?:[\s-]*(?P<m>\d{4,5}[a-z]{0,2}))?(?![a-z0-9])")

# ----------------------------------------------------------------------------- kategori sözcükleri
# (desen, kategori, ağırlık) — sıralı: uzun/özel ifadeler önce
_KATEGORI_SOZ: list[tuple[str, str, float]] = [
    (r"islemci\s+(?:sivi\s+|hava\s+|su\s+)?sogut\w*|cpu\s+(?:liquid\s+|water\s+|air\s+)?cooler|cpu\s+sogutucu\w*", "sogutucu", 3.5),
    (r"sivi\s+sogut\w*|su\s+sogut\w*|hava\s+sogut\w*|kule\s+sogutucu\w*|liquid\s+cool\w*|air\s+cooler", "sogutucu", 3),
    (r"(?<![a-z0-9])aio(?![a-z0-9])|sogutucu\w*|sogutma|(?<![a-z0-9])cooler\w*", "sogutucu", 3),
    (r"ekran\s+kart\w*|grafik\s+kart\w*|video\s+kart\w*|graphics?\s+card|video\s+card|"
     r"(?<![a-z0-9])gpu(?![a-z0-9])|(?<![a-z0-9])vga(?![a-z0-9])", "gpu", 3),
    (r"geforce|radeon", "gpu", 2.5),
    (r"islemci\w*|(?<![a-z0-9])cpu\w*|processor\w*|mikroislemci\w*", "cpu", 3),
    (r"anakart\w*|ana\s+kart\w*|mother\s*board\w*|main\s*board\w*|(?<![a-z0-9])mobo(?![a-z0-9])", "anakart", 3),
    (r"guc\s+kaynag\w*|power\s+supply|(?<![a-z0-9])psu\w*|guc\s+unitesi", "psu", 3),
    (r"(?<![a-z0-9])ram(?:ler|leri|i|in|e)?(?![a-z0-9])|bellek\w*|(?<![a-z0-9])memor(?:y|ies)(?![a-z0-9])|"
     r"(?<![a-z0-9])dimm\w*", "ram", 3),
    (r"(?<![a-z0-9])ssd\w*|(?<![a-z0-9])hdd\w*|depolama|disk\w*", "ssd", 3),
    (r"(?<![a-z0-9])kasa\w*|pc\s+case|(?<![a-z0-9])case(?![a-z0-9])|(?<![a-z0-9])tower(?![a-z0-9])", "kasa", 3),
    (r"monitor\w*|(?<![a-z0-9])display\w*", "monitor", 3),
]
_KATEGORI_RX = [(re.compile(d), k, a) for d, k, a in _KATEGORI_SOZ]
_EKRAN = _rx(r"(?<![a-z0-9])ekran\w*")

# ----------------------------------------------------------------------------- marka
_MARKALAR: list[tuple[str, str]] = [
    (r"asus", "Asus"), (r"msi", "MSI"), (r"gigabyte", "Gigabyte"), (r"zotac", "Zotac"), (r"inno\s*3d", "Inno3D"),
    (r"palit", "Palit"), (r"gainward", "Gainward"), (r"pny", "PNY"), (r"sapphire", "Sapphire"),
    (r"power\s*color", "PowerColor"), (r"xfx", "XFX"), (r"asrock", "ASRock"), (r"colorful", "Colorful"),
    (r"galax", "Galax"), (r"evga", "EVGA"), (r"corsair", "Corsair"), (r"g\.?\s*skill", "G.Skill"),
    (r"kingston", "Kingston"), (r"xpg", "XPG"), (r"adata", "ADATA"), (r"crucial", "Crucial"),
    (r"samsung", "Samsung"), (r"western\s+digital|wd", "WD"), (r"seagate", "Seagate"), (r"sk\s*hynix", "SK Hynix"),
    (r"team\s*group", "TeamGroup"), (r"patriot", "Patriot"), (r"lexar", "Lexar"), (r"lian\s*-?\s*li", "Lian Li"),
    (r"nzxt", "NZXT"), (r"fractal(?:\s+design)?", "Fractal Design"), (r"be\s*quiet!?", "be quiet!"),
    (r"arctic", "Arctic"), (r"noctua", "Noctua"), (r"deepcool", "Deepcool"), (r"cooler\s*master", "Cooler Master"),
    (r"thermaltake", "Thermaltake"), (r"seasonic", "Seasonic"), (r"super\s*flower", "Super Flower"),
    (r"silverstone", "SilverStone"), (r"antec", "Antec"), (r"hyte", "Hyte"), (r"montech", "Montech"),
    (r"phanteks", "Phanteks"), (r"enermax", "Enermax"), (r"lg", "LG"), (r"dell", "Dell"), (r"aoc", "AOC"),
    (r"benq", "BenQ"), (r"acer", "Acer"), (r"viewsonic", "ViewSonic"), (r"philips", "Philips"),
    (r"teamgroup", "TeamGroup"), (r"sandisk", "SanDisk"), (r"biostar", "Biostar"),
]
_MARKA_RX = [(re.compile(rf"(?<![a-z0-9])(?:{d})(?![a-z0-9])"), ad) for d, ad in _MARKALAR]
_CIP = _rx(r"(?<![a-z0-9])(?P<v>amd|intel|nvidia)(?![a-z0-9])")
_CIP_AD = {"amd": "AMD", "intel": "Intel", "nvidia": "NVIDIA"}

# model kodu tarzı artık (rm1000x, tuf-rtx5080-o16g ...)
_MODEL_KODU = _rx(r"(?<![a-z0-9])(?P<v>[a-z]{1,6}-?\d{3,}[a-z0-9-]*)(?![a-z0-9])")

_DURAK = set("""
ve veya ile icin uyan uyumlu uyum olan olanlar olanlari olsun bul bulur musun bana ara arayin arama goster
fiyat fiyati fiyatlari fiyatlarini lutfen bir bu su o mi mu gibi tane adet yeni sifir urun urunler urunu
istiyorum ariyorum ariyoruz lazim gerek nerede hangi varsa kit takim tl de da ki
li lu e a ye ya den dan i in un nin the a an for with and of find search compatible best price
fiyatina fiyatiyla modeller modelleri secenek secenekler listele
""".split())


# ----------------------------------------------------------------------------- yardımcılar


def _norm_surum(v: str) -> str:
    return v if "." in v else f"{v}.0"


def _oy(oylar: dict[str, float], kat: str, agirlik: float) -> None:
    oylar[kat] = oylar.get(kat, 0.0) + agirlik


# ----------------------------------------------------------------------------- ana işlev


def ayristir(metin: str, tur: str = "urun") -> YapisalSorgu:
    tur = str(getattr(tur, "value", tur) or "urun")
    metin = metin or ""
    c = _Metin(metin)
    q = YapisalSorgu(tur=tur, ham=metin.strip())

    # --- bütçe / en ucuz / hariç
    q.butce_alt, q.butce_ust = _butce(c)
    if c.bul(_EN_UCUZ):
        q.en_ucuz = True
    q.haric = _haric_cikar(c)
    # "en ucuz" hariç ifadesinden sonra da gelebilir
    if c.bul(_EN_UCUZ):
        q.en_ucuz = True

    if tur != "urun":
        q.kalan_sozcukler = _artik(c)
        q.guven = round(max(0.0, 0.2 - 0.05 * len(q.kalan_sozcukler)), 2) if q.kalan_sozcukler else 0.0
        return q

    oz: dict[str, Any] = {}
    oylar: dict[str, float] = {}
    cip: str | None = None
    marka: str | None = None
    model: str | None = None

    # --- bellek düzeni (kit)
    for m in list(_KIT.finditer(c.t)):
        n, mm = int(m.group("n")), int(m.group("m"))
        if mm in _ESIT_MODUL and "kit" not in oz:
            oz["kit"] = f"{n}x{mm}"
            oz["kapasite_gb"] = n * mm
            c.sil(m.start(), m.end())
            _oy(oylar, "ram", 1.0)
    if "kit" not in oz:
        for m in list(_KIT_TERS.finditer(c.t)):
            n, mm = int(m.group("n")), int(m.group("m"))
            if mm in _ESIT_MODUL:
                oz["kit"] = f"{n}x{mm}"
                oz["kapasite_gb"] = n * mm
                c.sil(m.start(), m.end())
                _oy(oylar, "ram", 1.0)
                break

    # --- DDR / GDDR
    for m in c.hepsi(_DDR):
        v = m.group("v").upper()
        if m.group("g"):
            oz.setdefault("vram_tipi", f"GDDR{v}")
        else:
            oz.setdefault("bellek_tipi", f"DDR{v}")
            _oy(oylar, "ram", 1.5)
            if m.group("h"):
                oz.setdefault("hiz_mts", int(m.group("h")))
    for m in c.hepsi(_HIZ):
        v = int(m.group("v"))
        if 1000 <= v <= 12000:
            oz.setdefault("hiz_mts", v)
            _oy(oylar, "ram", 1.0)
    for m in c.hepsi(_CL):
        oz.setdefault("cl", int(m.group("v")))
        _oy(oylar, "ram", 1.0)
    ram_baglam = bool(oz.get("bellek_tipi") or oz.get("kit") or oz.get("cl") or oz.get("hiz_mts"))
    if ram_baglam and "cl" not in oz:
        for m in list(_C_KISA.finditer(c.t)):
            v = int(m.group("v"))
            if 14 <= v <= 50:
                oz["cl"] = v
                c.sil(m.start(), m.end())
                break
    if (oz.get("bellek_tipi") or oz.get("kit") or oz.get("cl")) and "hiz_mts" not in oz:
        for m in list(_HIZ_CIPLAK.finditer(c.t)):
            v = int(m.group("v"))
            if 2133 <= v <= 9600:
                oz["hiz_mts"] = v
                c.sil(m.start(), m.end())
                break
    for m in c.hepsi(_PROFIL):
        oz.setdefault("profil", m.group("v").upper())

    # --- güç kaynağı
    for m in c.hepsi(_ATX_SURUM):
        oz.setdefault("atx", m.group("v"))
        _oy(oylar, "psu", 2.0)
    for m in c.hepsi(_SERT80):
        oz.setdefault("80plus", (m.group("d") or "").capitalize() or "80+")
        _oy(oylar, "psu", 2.0)
    for m in list(_WATT.finditer(c.t)):
        v = int(m.group("v"))
        if 200 <= v <= 2500 and "watt" not in oz:
            oz["watt"] = v
            c.sil(m.start(), m.end())
            _oy(oylar, "psu", 1.0)
    if "watt" in oz and oz.get("80plus") in (None, "80+"):
        mm = c.bul(_SERT_CIPLAK)
        if mm:
            oz["80plus"] = mm.group("d").capitalize()

    # --- PCIe / soket / yonga seti / form / wifi / qvl
    for m in c.hepsi(_PCIE):
        oz.setdefault("pcie", _norm_surum(m.group("v")))
    for m in c.hepsi(_GEN):
        oz.setdefault("pcie", _norm_surum(m.group("v")))
    for m in c.hepsi(_SOKET):
        oz.setdefault("soket", re.sub(r"[\s-]+", "", m.group("v")).upper())
    for m in c.hepsi(_YONGA):
        oz.setdefault("yonga_seti", (m.group("v") + (m.group("e") if m.group("e") == "e" else "")).upper())
        if m.group("e") == "m":
            oz.setdefault("form_faktor", "mATX")
        _oy(oylar, "anakart", 2.0)
    for m in c.hepsi(_FORM):
        f = re.sub(r"[\s-]+", "", m.group("f"))
        oz.setdefault("form_faktor", {"eatx": "E-ATX", "microatx": "mATX", "matx": "mATX", "miniitx": "Mini-ITX",
                                      "itx": "Mini-ITX", "midtower": "Mid Tower", "fulltower": "Full Tower",
                                      "minitower": "Mini Tower"}.get(f, f.upper()))
        if "tower" in f:
            _oy(oylar, "kasa", 1.0)
    for m in c.hepsi(_WIFI):
        oz.setdefault("wifi", (m.group("v") or "var").upper() if m.group("v") else "var")
        _oy(oylar, "anakart", 1.0)
    if c.bul(_QVL):
        oz["qvl"] = True

    # --- monitör
    for m in c.hepsi(_INC):
        oz.setdefault("ekran_inc", float(m.group("v").replace(",", ".")) if "." in m.group("v") or "," in m.group("v")
                      else int(m.group("v")))
        _oy(oylar, "monitor", 2.0)
    for m in c.hepsi(_HZ):
        oz.setdefault("yenileme_hz", int(m.group("v")))
        _oy(oylar, "monitor", 1.5)
    for m in c.hepsi(_COZUNURLUK):
        v = {"uhd": "4K", "2160p": "4K", "qhd": "1440p", "wqhd": "1440p", "2k": "1440p", "fhd": "1080p"}.get(
            m.group("v"), m.group("v").upper() if m.group("v").endswith("k") else m.group("v"))
        oz.setdefault("cozunurluk", v)
        _oy(oylar, "monitor", 1.0)
    for m in c.hepsi(_PANEL):
        oz.setdefault("panel", re.sub(r"\s*-\s*|\s+", "-", m.group("v")).upper())
        _oy(oylar, "monitor", 0.5)

    # --- soğutucu / SSD
    for m in c.hepsi(_RADYATOR):
        oz.setdefault("radyator_mm", int(m.group("v")))
        _oy(oylar, "sogutucu", 1.5)
    nvme = c.hepsi(_NVME)
    sata = c.hepsi(_SATA)
    m2 = c.hepsi(_M2)
    if nvme:
        oz.setdefault("arayuz", "NVMe")
    elif sata:
        oz.setdefault("arayuz", "SATA")
    if m2:
        oz.setdefault("form_faktor", "M.2")
    if nvme or m2:
        _oy(oylar, "ssd", 2.5)
    if sata:
        _oy(oylar, "ssd", 1.0)

    # --- kapasite
    for m in list(_KAPASITE.finditer(c.t)):
        v = int(m.group("v"))
        tb = m.group("b") == "tb"
        if "kapasite_gb" not in oz:
            oz["kapasite_gb"] = v * 1000 if tb else v
        if tb:
            _oy(oylar, "ssd", 1.0)
        c.sil(m.start(), m.end())

    # --- paket / renk
    if c.bul(_PAKET_KUTULU):
        oz["paket"] = "kutulu"
    if c.bul(_PAKET_TRAY):
        oz["paket"] = "tray"
    for m in c.hepsi(_RENK):
        oz.setdefault("renk", {"beyaz": "Beyaz", "white": "Beyaz", "siyah": "Siyah", "black": "Siyah"}[m.group("v")])

    # --- GPU / CPU model kalıpları
    m = c.bul(_GPU_MODEL)
    if m:
        seri = m.group("s").upper()
        ek = (m.group("k") or "").strip()
        model = f"{seri} {m.group('n')}" + (f" {_buyuk_ilk(ek)}" if ek else "")
        cip = "NVIDIA" if seri in ("RTX", "GTX") else ("AMD" if seri == "RX" else "Intel")
        _oy(oylar, "gpu", 2.5)
    m = c.bul(_RYZEN)
    if m and (m.group("s") or m.group("m")):
        if m.group("s"):
            oz.setdefault("seri", f"Ryzen {m.group('s')}")
        if m.group("m"):
            model = model or re.sub(r"\s+", "", m.group("m")).upper()
        cip = cip or "AMD"
        _oy(oylar, "cpu", 2.5 if m.group("m") else 2.0)
    elif m:
        cip = cip or "AMD"
        _oy(oylar, "cpu", 2.0)
    m = c.bul(_X3D_CIPLAK)
    if m:
        model = model or re.sub(r"\s+", "", m.group("m")).upper()
        cip = cip or "AMD"
        _oy(oylar, "cpu", 2.5)
    m = c.bul(_ULTRA)
    if m:
        oz.setdefault("seri", f"Core Ultra {m.group('s')}")
        if m.group("m"):
            model = model or m.group("m").upper()
        cip = cip or "Intel"
        _oy(oylar, "cpu", 2.5 if m.group("m") else 2.0)
    m = c.bul(_INTEL_I)
    if m:
        oz.setdefault("seri", f"Core i{m.group('s')}")
        if m.group("m"):
            model = model or f"i{m.group('s')}-{m.group('m').upper()}"
        cip = cip or "Intel"
        _oy(oylar, "cpu", 2.5 if m.group("m") else 1.5)

    # --- kategori sözcükleri
    for rx, kat, agirlik in _KATEGORI_RX:
        for mm in c.hepsi(rx):
            _oy(oylar, kat, agirlik)
    # tek başına "ekran": kart yoksa monitör
    if c.bul(_EKRAN):
        _oy(oylar, "monitor", 1.5)

    # --- GPU çıplak model numarası (5080 ekran kartı)
    if model is None and (oylar.get("gpu", 0) >= 2.5 or not oylar):
        m = c.bul(_GPU_CIPLAK)
        if m:
            ek = (m.group("k") or "").strip()
            model = f"RTX {m.group('n')}" + (f" {_buyuk_ilk(ek)}" if ek else "")
            cip = cip or "NVIDIA"
            _oy(oylar, "gpu", 2.0)

    # --- ikinci geçiş: bağlama bağlı çıplak değerler
    kat = _kategori_sec(oylar)
    if kat == "sogutucu" and "radyator_mm" not in oz:
        m = c.bul(_RADYATOR_CIPLAK)
        if m:
            oz["radyator_mm"] = int(m.group("v"))
    if kat == "psu" and "80plus" not in oz:
        m = c.bul(_SERT_CIPLAK)
        if m:
            oz["80plus"] = m.group("d").capitalize()
    if kat == "psu":
        c.bul(_rx(r"(?<![a-z0-9])atx(?![a-z0-9])"))
    elif kat in ("anakart", "kasa"):
        m = c.bul(_rx(r"(?<![a-z0-9])atx(?![a-z0-9])"))
        if m:
            oz.setdefault("form_faktor", "ATX")
    if kat == "gpu" and "kapasite_gb" in oz and "kit" not in oz:
        oz["vram_gb"] = oz.pop("kapasite_gb")
        oz.pop("kapasite", None)
    elif "kapasite_gb" in oz:
        gb = oz["kapasite_gb"]
        oz["kapasite"] = f"{gb // 1000}TB" if gb >= 1000 and gb % 1000 == 0 else f"{gb}GB"

    # --- marka
    for rx, ad in _MARKA_RX:
        m = c.bul(rx)
        if m:
            marka = marka or ad
    for m in c.hepsi(_CIP):
        cip = cip or _CIP_AD[m.group("v")]
    if cip:
        if kat == "gpu":
            oz.setdefault("cip", cip)
        else:
            marka = marka or cip
            if kat == "cpu":
                marka = cip
    if kat is None and marka is None:
        pass

    # --- model kodu (artık içinden)
    if model is None:
        m = _MODEL_KODU.search(c.t)
        if m:
            model = c.parca(m.start("v"), m.end("v")).upper()
            c.sil(m.start(), m.end())

    q.kategori = kat
    q.marka = marka
    q.model = model
    q.ozellikler = oz
    q.kalan_sozcukler = _artik(c)
    q.guven = _guven(q)
    return q


def _buyuk_ilk(s: str) -> str:
    """GPU model eki: 'ti super' → 'Ti Super', 'xt' → 'XT'."""
    return " ".join({"ti": "Ti", "super": "Super", "xt": "XT", "xtx": "XTX", "gre": "GRE"}.get(w, w.upper())
                    for w in s.split())


def _kategori_sec(oylar: dict[str, float]) -> str | None:
    if not oylar:
        return None
    # eşitlikte KATEGORILER sırası
    en = max(oylar.items(), key=lambda kv: (kv[1], -KATEGORILER.index(kv[0])))
    return en[0] if en[1] >= 1.0 else None


def _artik(c: _Metin) -> list[str]:
    t = re.sub(r"'[a-z]+", lambda m: " " * len(m.group(0)), c.t)       # AM5'e, QVL'li ekleri
    cikti: list[str] = []
    for m in re.finditer(r"[a-z0-9]+(?:[.\-+][a-z0-9]+)*", t):
        w = m.group(0)
        if w in _DURAK or re.fullmatch(r"[0-9.,+-]+", w):
            continue
        cikti.append(c.parca(m.start(), m.end()))
    return cikti


def _guven(q: YapisalSorgu) -> float:
    g = 0.0
    if q.kategori:
        g += 0.4
    if q.model:
        g += 0.35
    elif q.ozellikler:
        g += min(0.3, 0.1 * len(q.ozellikler))
    if q.marka:
        g += 0.1
    g -= min(0.3, 0.08 * len(q.kalan_sozcukler))
    return round(max(0.0, min(1.0, g)), 2)
