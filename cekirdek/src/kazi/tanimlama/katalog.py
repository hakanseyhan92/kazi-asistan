"""Katalog adayları: Akakçe arama sayfası → `Aday` listesi (SARTNAME §4.2).

HTML ayrıştırma `evren-asistan/fiyat/siteler/akakce.py` (`modeller`) içinden uyarlanmıştır: arama sayfasındaki
astro-island `props` JSON'u (`searchData.productList.products`: code, price, countOfPrices, name, url, imageUrl).

Ağ erişimi YOK: sayfayı `getir(url) -> html` çağrısı getirir (orkestra Tarayici'yi bağlar; testler dosyadan okur).
Adaylar sorguya UYUM PUANIYLA sıralanır; laptop / hazır sistem / aksesuar / yanlış model elenir (gerekçesi saklanır).
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from ..modeller import Aday, FiyatBandi
from .ayristir import YapisalSorgu, ayristir, katla

log = logging.getLogger("kazi.tanimlama.katalog")

KAYNAK = "akakce"
TABAN = "https://www.akakce.com"
ARAMA = TABAN + "/arama/"
EN_FAZLA_ADAY = 20

_ASTRO = re.compile(r"<astro-island\b[^>]*>", re.S | re.I)
_PROPS = re.compile(r'props="(.*?)"', re.S)


# ----------------------------------------------------------------------------- HTML ayrıştırma
def arama_url(sorgu: str, sayfa: int = 1) -> str:
    u = ARAMA + "?q=" + urllib.parse.quote_plus(sorgu)
    if sayfa and sayfa > 1:
        u += "&p=" + str(int(sayfa))
    return u


def _astro(v: Any) -> Any:
    """Astro serileştirmesini çöz: [0, x] -> x, [1, [x, y]] -> [x, y]."""
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int):
        if v[0] == 1 and isinstance(v[1], list):
            return [_astro(x) for x in v[1]]
        return _astro(v[1])
    if isinstance(v, dict):
        return {k: _astro(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_astro(x) for x in v]
    return v


def _adalar(html: str) -> Iterator[dict]:
    for m in _ASTRO.finditer(html or ""):
        p = _PROPS.search(m.group(0))
        if not p:
            continue
        try:
            d = json.loads(_html.unescape(p.group(1)))
        except ValueError:
            continue
        if isinstance(d, dict):
            yield _astro(d)


def _mutlak(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return TABAN + url
    return url


def _fiyat(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = re.sub(r"[^\d.,]", "", v)
        if not s:
            return None
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
            s = s.replace(".", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


@dataclass
class Kart:
    """Akakçe model kartı (ham)."""
    kod: str
    ad: str
    fiyat: float
    satici_sayisi: int
    url: str
    gorsel: str | None


def kartlar(html: str) -> list[Kart]:
    """Arama sayfası HTML'i -> model kartları (aynı kod tekrarında en düşük fiyat)."""
    bulunan: dict[str, Kart] = {}
    for d in _adalar(html):
        sd = d.get("searchData")
        pl = sd.get("productList") if isinstance(sd, dict) else None
        if not isinstance(pl, dict):
            continue
        for p in pl.get("products") or []:
            if not isinstance(p, dict):
                continue
            ad = str(p.get("name") or "").strip()
            fiyat = _fiyat(p.get("price"))
            url = _mutlak(str(p.get("url") or ""))
            kod = str(p.get("code") or "").strip()
            if not (ad and fiyat and fiyat > 0 and url and kod):
                continue
            try:
                n = int(p.get("countOfPrices") or 0)
            except (TypeError, ValueError):
                n = 0
            gorsel = _mutlak(str(p.get("imageUrl") or "")) or None
            k = Kart(kod=kod, ad=ad, fiyat=fiyat, satici_sayisi=n, url=url, gorsel=gorsel)
            eski = bulunan.get(kod)
            if eski is None or k.fiyat < eski.fiyat:
                bulunan[kod] = k
    return list(bulunan.values())


# ----------------------------------------------------------------------------- arama metni
_KAT_SOZCUK = {"gpu": "ekran kartı", "cpu": "işlemci", "ram": "ram", "anakart": "anakart", "ssd": "ssd",
               "psu": "güç kaynağı", "kasa": "kasa", "sogutucu": "soğutucu", "monitor": "monitör"}


def _ozellik_parcalari(oz: dict[str, Any]) -> list[str]:
    p: list[str] = []
    if oz.get("bellek_tipi"):
        p.append(str(oz["bellek_tipi"]).lower())
    if oz.get("kapasite_gb") and not oz.get("vram_gb"):
        gb = int(oz["kapasite_gb"])
        p.append(f"{gb // 1000}tb" if gb >= 1000 and gb % 1000 == 0 else f"{gb}gb")
    if oz.get("hiz_mts"):
        p.append(str(oz["hiz_mts"]))
    if oz.get("cl"):
        p.append(f"cl{oz['cl']}")
    if oz.get("watt"):
        p.append(f"{oz['watt']}w")
    if oz.get("80plus") and oz["80plus"] != "80+":
        p.append(str(oz["80plus"]).lower())
    if oz.get("atx"):
        p.append(f"atx {oz['atx']}")
    if oz.get("yonga_seti"):
        p.append(str(oz["yonga_seti"]).lower())
    if oz.get("radyator_mm"):
        p.append(str(oz["radyator_mm"]))
    if oz.get("pcie"):
        p.append(f"pcie {oz['pcie']}")
    if oz.get("ekran_inc"):
        p.append(f"{oz['ekran_inc']} inç")
    if oz.get("yenileme_hz"):
        p.append(f"{oz['yenileme_hz']}hz")
    return p


def arama_metinleri(s: YapisalSorgu) -> list[str]:
    """Önce ayrıntılı, sonra sade sorgu (en çok 2 sayfa çekilir)."""
    kat = _KAT_SOZCUK.get(s.kategori or "", "")
    cekirdek: list[str] = []
    if s.marka and s.kategori != "cpu":
        cekirdek.append(s.marka)
    if s.model:
        if s.kategori == "cpu" and s.ozellikler.get("seri", "").startswith("Ryzen"):
            cekirdek.append(f"{s.ozellikler['seri']} {s.model}")
        else:
            cekirdek.append(s.model)
    elif kat:
        cekirdek.append(kat)
    if not cekirdek:                                 # tanınmayan istek: ham metinle ara
        return [s.ham] if s.ham else []
    parcalar = _ozellik_parcalari(s.ozellikler)
    tam = " ".join(cekirdek + parcalar).strip()
    sade = " ".join(cekirdek + parcalar[:2]).strip()      # sonuç gelmezse: çekirdek + ilk iki özellik
    cikti = [tam]
    if sade and sade != tam:
        cikti.append(sade)
    return [x for x in cikti if x]


# ----------------------------------------------------------------------------- MPN
_MPN_ELE = re.compile(r"^(?:\d+(?:[.,]\d+)?(?:gb|tb|mb|mhz|ghz|mt/?s|mts|w|mm|hz|bit|inc|inch|cl)|g?ddr\d.*|pcie.*|gen\d.*|"
                      r"(?:rtx|gtx|rx)\d{3,4}(?:ti|super|xt|xtx)?|\d+x\d+(?:gb)?|wifi\d.*|atx\d.*|am\d|lga\d+)$", re.I)


def mpn_cikar(baslik: str, model: str | None = None) -> str | None:
    """Başlıktan üretici parça numarası (MPN) tahmini: harf+rakam içeren, ≥6 karakter, özellik/çip olmayan."""
    model_n = re.sub(r"[^a-z0-9]", "", katla(model or ""))
    adaylar: list[str] = []
    for ham in re.findall(r"[A-Za-z0-9][A-Za-z0-9./_-]*", baslik):
        t = ham.strip(".-_/")
        if len(t) < 6 or not re.search(r"[A-Za-z]", t) or not re.search(r"\d", t):
            continue
        if _MPN_ELE.match(t) or (model_n and re.sub(r"[^a-z0-9]", "", t.lower()) == model_n):
            continue
        adaylar.append(t)
    if not adaylar:
        return None
    tireli = [t for t in adaylar if "-" in t]
    return (tireli or adaylar)[0]


# ----------------------------------------------------------------------------- uyum puanı
_LAPTOP = re.compile(r"laptop|notebook|diz\s*ustu|ultrabook|(?<![a-z0-9])nb(?![a-z0-9])|ekran\s+kart\w*\s+laptop")
_HAZIR = re.compile(
    r"masaustu\s+(?:bilgisayar|pc|oyun|oyuncu)|oyun(?:cu)?\s+bilgisayar|gaming\s+pc|hazir\s+sistem|oem\s+paket|"
    r"all\s*-?\s*in\s*-?\s*one|mini\s+pc|(?<![a-z0-9])pc\s+paket|workstation|is\s+istasyonu")
_AILE = {
    "gpu": re.compile(r"rtx\s*\d{4}|gtx\s*\d{3,4}|radeon|geforce|(?<![a-z0-9])rx\s*\d{3,4}"),
    "cpu": re.compile(r"ryzen|core\s*(?:ultra|i[3579])|(?<![a-z0-9])i[3579]-\d{4,5}"),
    "ram": re.compile(r"(?<![a-z])ddr[345]|(?<![a-z0-9])ram(?![a-z0-9])"),
    "ssd": re.compile(r"(?<![a-z0-9])ssd|nvme|(?<![a-z0-9])m\.2"),
}
_KENDI_ISIM = {
    "gpu": r"ekran\s+kart|grafik\s+kart|graphics\s+card", "cpu": r"islemci|processor", "ram": r"bellek|(?<![a-z0-9])ram(?![a-z0-9])",
    "anakart": r"anakart|motherboard", "ssd": r"ssd|nvme", "psu": r"guc\s+kaynag|power\s+supply",
    "kasa": r"kasa|case", "sogutucu": r"sogut|cooler", "monitor": r"monitor",
}
_AKSESUAR: dict[str, re.Pattern[str]] = {k: re.compile(v) for k, v in {
    "gpu": r"su\s+sogut\w*\s+blo|sogutma\s+blo|waterblock|water\s*block|(?<![a-z0-9])blok|bracket|tutucu|backplate|"
           r"(?<![a-z0-9])kablo(?![a-z0-9])|adaptor|riser|(?:fan|termal)\s*(?:kit|paket|pad|set)|destek\s+aparat",
    "cpu": r"anakart|bundle|(?<![a-z0-9])set(?![a-z0-9])|kombo|(?<![a-z0-9])rtx|sogutucu|cooler|macun|ekran\s+kart",
    "ram": r"so\s*-?\s*dimm|ekran\s+kart|ram\s+sogutucu|heat\s*spreader|ram\s+fan",
    "anakart": r"bundle|kombo|(?<![a-z0-9])set(?![a-z0-9])|kablo|braket|bracket|islemci\s*\+",
    "ssd": r"harici|external|portable|tasinabilir|enclosure|kasasi|(?<![a-z0-9])kutu(?![a-z0-9])|adaptor|"
           r"kart\s+okuyucu|flash\s+bellek|usb\s+bellek|micro\s*sd|sd\s+kart|sogutucu|heatsink",
    "psu": r"(?<![a-z0-9])kasa(?![a-z0-9])|kablo\s+set|adaptor|tester|(?<![a-z0-9])ups(?![a-z0-9])|kesintisiz",
    "kasa": r"fan\s*(?:paket|set|kit)|ek\s+panel|kablo\s+set|(?<![a-z0-9])stand(?![a-z0-9])|tutucu|toz\s+filt|riser|yedek\s+parca",
    "sogutucu": r"fan\s*paket|fan\s*set|ek\s+fan|macun|termal|(?<![a-z0-9])pad(?![a-z0-9])|bracket|montaj\s+kit",
    "monitor": r"(?<![a-z0-9])kol(?![a-z0-9])|(?<![a-z0-9])stand|tutucu|aski|kablo|adaptor|koruyucu|film|ekran\s+kart",
}.items()}

# istenen özellik anahtarı -> sert çelişki mi (True) yoksa hafif mi (False)
_KONTROL: dict[str, bool] = {
    "kit": True, "kapasite_gb": True, "vram_gb": True, "hiz_mts": True, "cl": True, "watt": True, "80plus": True,
    "atx": True, "pcie": True, "wifi": True, "bellek_tipi": True, "radyator_mm": True, "ekran_inc": True,
    "yenileme_hz": True, "cozunurluk": True, "panel": True, "yonga_seti": True, "arayuz": False,
    "form_faktor": False, "renk": False, "profil": False, "soket": True,
    "paket": False,                      # kutulu/tray: yumuşak (uyumsuz −1, uyumlu +1)
}


def _norm_model(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", katla(s or ""))


@dataclass
class Eleme:
    baslik: str
    kod: str
    gerekce: str


def hazir_sistem_mi(baslik_k: str, kategori: str | None) -> bool:
    """`baslik_k`: katla() uygulanmış başlık."""
    if _HAZIR.search(baslik_k):
        return True
    aile = sum(1 for rx in _AILE.values() if rx.search(baslik_k))
    kendi = _KENDI_ISIM.get(kategori or "")
    if aile >= 3 and not (kendi and re.search(kendi, baslik_k)):
        return True
    return False


def uyum_puani(s: YapisalSorgu, baslik: str) -> tuple[float, str | None, dict[str, Any], YapisalSorgu]:
    """(puan, eleme_gerekçesi|None, aday_özellikleri, aday_ayrışımı). Gerekçe doluysa aday elenir."""
    bk = katla(baslik)
    ay = ayristir(baslik)
    oz = ay.ozellikler

    if _LAPTOP.search(bk):
        return -10, "laptop/notebook", oz, ay
    if hazir_sistem_mi(bk, s.kategori):
        return -10, "hazır sistem", oz, ay
    if s.kategori and (rx := _AKSESUAR.get(s.kategori)) and (m := rx.search(bk)):
        return -10, f"aksesuar ({m.group(0).strip()})", oz, ay
    for h in s.haric:
        hk = katla(h)
        if hk and re.search(rf"(?<![a-z0-9]){re.escape(hk)}", bk):
            return -10, f"hariç istendi ({h})", oz, ay

    puan = 0.0
    if s.kategori:
        if ay.kategori and ay.kategori != s.kategori:
            return -10, f"kategori farklı ({ay.kategori})", oz, ay
        puan += 2.0

    if s.model:
        sm = _norm_model(s.model)
        am = _norm_model(ay.model)
        bn = _norm_model(baslik)
        if am and am == sm:
            puan += 4.0
        elif am and am != sm and sm not in bn.replace(am, ""):
            return -10, f"model farklı ({ay.model})", oz, ay
        elif sm in bn and not am:
            puan += 3.0
        elif sm in bn:
            puan += 3.0
        else:
            return -10, "model eşleşmedi", oz, ay

    if s.marka:
        if ay.marka and ay.marka == s.marka:
            puan += 1.5
        elif ay.marka and ay.marka != s.marka and not re.search(rf"(?<![a-z0-9]){re.escape(katla(s.marka))}(?![a-z0-9])", bk):
            return -10, f"marka farklı ({ay.marka})", oz, ay
        elif re.search(rf"(?<![a-z0-9]){re.escape(katla(s.marka))}(?![a-z0-9])", bk):
            puan += 1.5

    for k, istenen in s.ozellikler.items():
        if k not in _KONTROL:
            continue
        sert = _KONTROL[k]
        if k == "soket":
            if re.search(rf"(?<![a-z0-9]){katla(str(istenen))}(?![a-z0-9])", bk):
                puan += 1.0
            elif "soket" in oz and sert:
                return -10, f"özellik uyumsuz (soket {oz['soket']}≠{istenen})", oz, ay
            continue
        if k == "kit" and "kit" not in oz:
            if "kapasite_gb" in oz and oz["kapasite_gb"] == s.ozellikler.get("kapasite_gb"):
                puan += 0.5
            continue
        if k == "kapasite_gb" and "kit" in oz and "kit" in s.ozellikler:
            continue                                  # kit karşılaştırması yeterli
        if k not in oz:
            continue
        if str(oz[k]).lower() == str(istenen).lower():
            puan += 1.0
        elif sert:
            return -10, f"özellik uyumsuz ({k} {oz[k]}≠{istenen})", oz, ay
        else:
            puan -= 1.0
    return puan, None, oz, ay


def _aday_olustur(k: Kart, ay: YapisalSorgu) -> Aday:
    oz: dict[str, Any] = dict(ay.ozellikler)
    if ay.kategori:
        oz["kategori"] = ay.kategori
    if ay.marka:
        oz["marka"] = ay.marka
    if ay.model:
        oz["model"] = ay.model
    oz["satici_sayisi"] = k.satici_sayisi
    return Aday(id=f"akakce:{k.kod}", baslik=k.ad, kaynak=KAYNAK, url=k.url, gorsel=k.gorsel,
                mpn=mpn_cikar(k.ad, ay.model), ozellikler=oz,
                fiyat_araligi=FiyatBandi(alt=k.fiyat, ust=None))


def katalog_ara_ayrintili(sorgu: YapisalSorgu, getir: Callable[[str], str]) -> tuple[list[Aday], list[Eleme]]:
    """(sıralı adaylar, elenenler+gerekçe). `getir` hata verirse o sayfa atlanır."""
    if sorgu.tur != "urun":
        return [], []
    adaylar: list[tuple[float, Aday]] = []
    elenen: list[Eleme] = []
    gorulen: set[str] = set()
    for metin in arama_metinleri(sorgu):
        try:
            html = getir(arama_url(metin))
        except Exception as e:                     # ağ/Tarayici hatası: çağıran boş liste görür
            log.warning("katalog sayfası alınamadı: %s", type(e).__name__)
            continue
        for k in kartlar(html or ""):
            if k.kod in gorulen:
                continue
            gorulen.add(k.kod)
            puan, gerekce, _oz, ay = uyum_puani(sorgu, k.ad)
            if gerekce is None and sorgu.butce_ust is not None and k.fiyat > sorgu.butce_ust:
                gerekce = f"bütçe üstü ({k.fiyat:.0f} TL)"
            if gerekce is None and sorgu.butce_alt is not None and k.fiyat < sorgu.butce_alt:
                gerekce = f"bütçe altı ({k.fiyat:.0f} TL)"
            if gerekce is None and sorgu.ozellikler.get("qvl") and sorgu.kategori in ("ram", "ssd"):
                from ..siteler.qvl import eslestir as qvl_eslestir
                if qvl_eslestir("bellek" if sorgu.kategori == "ram" else "ssd", k.ad) is None:
                    gerekce = "Gigabyte X870 QVL listesinde yok"
            if gerekce:
                elenen.append(Eleme(k.ad, k.kod, gerekce))
                continue
            adaylar.append((puan, _aday_olustur(k, ay)))
        if adaylar:
            break                                  # ilk sorgu sonuç verdiyse sade sorguya gerek yok
    if sorgu.en_ucuz and adaylar:
        en = max(p for p, _ in adaylar)
        adaylar.sort(key=lambda pa: (0 if pa[0] >= en - 0.5 else 1, pa[1].fiyat_araligi.alt))  # type: ignore[union-attr]
    else:
        adaylar.sort(key=lambda pa: (-pa[0], pa[1].fiyat_araligi.alt))                          # type: ignore[union-attr]
    return [a for _, a in adaylar[:EN_FAZLA_ADAY]], elenen


def katalog_ara(sorgu: YapisalSorgu, getir: Callable[[str], str]) -> list[Aday]:
    return katalog_ara_ayrintili(sorgu, getir)[0]
