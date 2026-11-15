"""Site modüllerinin döndürdüğü ara nesne `Kart` ve ortak fiyat ayrıştırıcı."""
from __future__ import annotations

import re
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from ..modeller import TZ_ISTANBUL, Katman, Teklif, TeyitDurumu, simdi_iso

_KATMAN_HARITASI: dict[str, Katman] = {
    "http": Katman.K0,
    "k0": Katman.K0,
    "chromium": Katman.K1,
    "k1": Katman.K1,
    "brave": Katman.K2,
    "brave_fetch": Katman.K2,
    "k2": Katman.K2,
    "chromium_gorunur": Katman.K1,
    "yerel_brave": Katman.K2,
    "k3": Katman.K3,
}

_TEYIT_HARITASI: dict[str, TeyitDurumu] = {
    "gecerli": TeyitDurumu.bekliyor,
    "bekliyor": TeyitDurumu.bekliyor,
    "bayat": TeyitDurumu.bekliyor,
    "acilamadi": TeyitDurumu.bekliyor,
    "dogrulandi": TeyitDurumu.teyitli,
    "teyitli": TeyitDurumu.teyitli,
    "supheli": TeyitDurumu.supheli,
    "urun_farkli": TeyitDurumu.urun_farkli,
    "stok_yok": TeyitDurumu.stok_yok,
    "fiyat_degisti": TeyitDurumu.fiyat_degisti,
}


def _iso_arti_03(zaman: str | None) -> str:
    """Zaman dizgesini Europe/Istanbul (+03:00) ISO-8601 biçimine tamamlar."""
    if not zaman:
        return simdi_iso()
    try:
        dt = datetime.fromisoformat(str(zaman).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_ISTANBUL)
        else:
            dt = dt.astimezone(TZ_ISTANBUL)
        return dt.isoformat(timespec="seconds")
    except Exception:
        return simdi_iso()


@dataclass
class Kart:
    site: str
    baslik: str
    url: str
    sepet_fiyati: float
    liste_fiyati: float | None = None
    satici: str | None = None
    kaynak: str | None = None
    pesin_taksit: int | None = None
    stok: bool | None = None
    yontem: str = "http"
    zaman: str = field(default_factory=simdi_iso)
    guncelleme: str | None = None
    durum: str = "gecerli"
    kampanya: str | None = None

    def __post_init__(self) -> None:
        if self.sepet_fiyati is not None:
            self.sepet_fiyati = float(self.sepet_fiyati)
        if self.liste_fiyati is not None:
            self.liste_fiyati = float(self.liste_fiyati)
        self.zaman = _iso_arti_03(self.zaman)

    # Eski alan adlarıyla geriye dönük uyum özellikleri
    @property
    def ad(self) -> str:
        return self.baslik

    @ad.setter
    def ad(self, deger: str) -> None:
        self.baslik = deger

    @property
    def fiyat(self) -> float:
        return self.sepet_fiyati

    @fiyat.setter
    def fiyat(self, deger: float) -> None:
        self.sepet_fiyati = float(deger) if deger is not None else deger

    @property
    def liste_fiyat(self) -> float | None:
        return self.liste_fiyati

    @liste_fiyat.setter
    def liste_fiyat(self, deger: float | None) -> None:
        self.liste_fiyati = float(deger) if deger is not None else None

    @property
    def taksit(self) -> int | None:
        return self.pesin_taksit

    @taksit.setter
    def taksit(self, deger: int | None) -> None:
        self.pesin_taksit = deger

    def teklife(self, urun_id: str) -> Teklif:
        """Kart ara nesnesini `kazi.modeller.Teklif` nesnesine dönüştürür."""
        katman = _KATMAN_HARITASI.get((self.yontem or "http").lower(), Katman.K0)
        from ..site_adi import site_anahtari
        if self.kaynak:
            site = site_anahtari(self.kaynak)
            satici = self.satici or f"{self.site} üzerinden"
        else:
            site = self.site
            satici = self.satici
        pesin_taksit_bool: bool | None = None
        if self.pesin_taksit is not None:
            pesin_taksit_bool = bool(self.pesin_taksit and self.pesin_taksit > 0)
        teyit = _TEYIT_HARITASI.get(self.durum, TeyitDurumu.bekliyor)
        return Teklif(
            urun_id=urun_id,
            site=site,
            satici=satici,
            baslik=self.baslik,
            url=self.url,
            liste_fiyati=self.liste_fiyati,
            sepet_fiyati=self.sepet_fiyati,
            kupon=self.kampanya,
            stok=self.stok,
            pesin_fiyatina_taksit=pesin_taksit_bool,
            gozlem_zamani=_iso_arti_03(self.zaman),
            kaynak_katmani=katman,
            teyit_durumu=teyit,
        )


_ALT_PAREN = re.compile(r"\(([^()]*/[^()]*)\)")


def sorgu_varyantlari(sorgu: str) -> list[str]:
    """Mağaza aramasına uygun sorgular. "SSD 1TB PCIe 5.0 NVMe (Samsung 9100 Pro / Crucial T705)" gibi
    parantez içinde "/" ile ayrılmış seçenekler tek aramada hiçbir şey bulmaz (tüm sözcükler aranır);
    her seçenek ayrı sorgu olur ("Samsung 9100 Pro 1TB", "Crucial T705 1TB"), son olarak parantezsiz ana ifade.
    Parantezde "/" yoksa sorgu aynen döner."""
    m = _ALT_PAREN.search(sorgu or "")
    if not m:
        return [sorgu]
    ana = " ".join((sorgu[:m.start()] + " " + sorgu[m.end():]).split())
    kap = re.search(r"\b\d+\s*(?:tb|gb)\b", ana, re.IGNORECASE)
    ek = (" " + kap.group(0).replace(" ", "")) if kap else ""
    cikti = []
    for alt in (a.strip() for a in m.group(1).split("/")):
        if alt:
            cikti.append(alt + ek)
    if ana:
        cikti.append(ana)
    return cikti or [sorgu]


_TEMIZ = re.compile(r"₺|\bTL\b|\bTRY\b", re.IGNORECASE)


def tl_sayi(metin) -> float | None:
    """'119.559,02 TL' -> 119559.02 · '₺1.299,90' -> 1299.9 · '13 099 TL' -> 13099.0

    Kural: nokta binlik ayırıcı, virgül ondalık. Boşluk, \\xa0, ₺ ve TL temizlenir.
    """
    if metin is None:
        return None
    if isinstance(metin, (int, float)):
        return float(metin)
    s = str(metin).replace("\xa0", " ").replace("\u202f", " ").replace(" ", " ")
    s = _TEMIZ.sub("", s)
    s = re.sub(r"[^\d.,]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    virgul, nokta = s.rfind(","), s.rfind(".")
    if virgul >= 0 and nokta >= 0:
        if virgul > nokta:                       # 119.559,02
            s = s.replace(".", "").replace(",", ".")
        else:                                    # 1,299.90
            s = s.replace(",", "")
    elif virgul >= 0:                            # 1.299,90 değil; yalnız virgül
        parca = s.split(",")
        if len(parca) == 2 and 1 <= len(parca[1]) <= 2:
            s = s.replace(",", ".")              # ondalık
        else:
            s = s.replace(",", "")               # binlik (İngilizce alışkanlık)
    elif nokta >= 0:                             # yalnız nokta
        parca = s.split(".")
        if len(parca) > 2:
            s = s.replace(".", "")
        elif len(parca) == 2 and len(parca[1]) == 3 and 1 <= len(parca[0]) <= 3:
            s = s.replace(".", "")               # 119.559 -> binlik
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# selectolax.lexbor yedekleyici (pyproject.toml'da selectolax yoksa devreye girer)
# ---------------------------------------------------------------------------
def _selectolax_hazirla() -> None:
    try:
        from selectolax.lexbor import LexborHTMLParser as _L  # noqa: F401
        return
    except ImportError:
        sys.modules.pop("selectolax.lexbor", None)
        sys.modules.pop("selectolax", None)

    _VOID = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    })
    _GORUNMEZ_ETIKET: frozenset[str] = frozenset()
    _OTO_KAPANAN = {"li": frozenset({"ul", "ol", "menu"}), "p": frozenset({"div", "section", "article", "main", "body"})}
    _ATTR_RX = re.compile(
        r'\[\s*([a-zA-Z0-9_:-]+)\s*(?:(\*=|=)\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s\]"\']+))\s*(i)?)?\s*\]'
    )
    _ID_RX = re.compile(r"#([a-zA-Z0-9_-]+)")
    _CLS_RX = re.compile(r"\.([a-zA-Z0-9_-]+)")
    _TAG_RX = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*")

    def _coz_bilesen(parca: str):
        tag_m = _TAG_RX.match(parca)
        tag = tag_m.group(0).lower() if tag_m else None
        ids = _ID_RX.findall(parca)
        clss = _CLS_RX.findall(parca)
        attrs = []
        for m in _ATTR_RX.finditer(parca):
            k = m.group(1).lower()
            op = m.group(2)
            val = m.group(3) if m.group(3) is not None else (m.group(4) if m.group(4) is not None else m.group(5))
            icase = bool(m.group(6))
            attrs.append((k, op, val, icase))
        return tag, ids, clss, attrs

    @functools.lru_cache(maxsize=256)
    def _coz_secici(secici: str):
        gruplar = []
        for alt in secici.split(","):
            alt = alt.strip()
            if not alt:
                continue
            adimlar = [_coz_bilesen(p) for p in alt.split() if p]
            if adimlar:
                gruplar.append(adimlar)
        return gruplar

    class _Dugum:
        __slots__ = ("tag", "attributes", "parent", "children", "_texts")

        def __init__(self, tag: str, attrs: dict[str, str], parent: _Dugum | None = None):
            self.tag = tag
            self.attributes = attrs
            self.parent = parent
            self.children: list[_Dugum | str] = []

        def _metin_parcalari(self, out: list[str]) -> None:
            if self.tag in _GORUNMEZ_ETIKET:
                return
            for ch in self.children:
                if isinstance(ch, str):
                    out.append(ch)
                else:
                    ch._metin_parcalari(out)

        def text(self, deep: bool = True, separator: str = "", strip: bool = False) -> str:
            if not deep:
                parcalar = [c for c in self.children if isinstance(c, str)]
            else:
                parcalar = []
                self._metin_parcalari(parcalar)
            if strip:
                parcalar = [p.strip() for p in parcalar if p.strip()]
            return separator.join(parcalar)

        def _eslesir_adim(self, adim) -> bool:
            tag, ids, clss, attrs = adim
            if tag and self.tag != tag:
                return False
            for i in ids:
                if self.attributes.get("id") != i:
                    return False
            if clss:
                mevcut = (self.attributes.get("class") or "").split()
                for c in clss:
                    if c not in mevcut:
                        return False
            for k, op, val, icase in attrs:
                if k not in self.attributes:
                    return False
                if op is None:
                    continue
                av = self.attributes.get(k) or ""
                v = val or ""
                if icase:
                    av, v = av.lower(), v.lower()
                if op == "=" and av != v:
                    return False
                if op == "*=" and v not in av:
                    return False
            return True

        def _eslesir_zincir(self, adimlar, kok: _Dugum) -> bool:
            if not self._eslesir_adim(adimlar[-1]):
                return False
            if len(adimlar) == 1:
                return True
            idx = len(adimlar) - 2
            cur = self.parent
            while cur is not None and cur is not kok:
                if cur._eslesir_adim(adimlar[idx]):
                    idx -= 1
                    if idx < 0:
                        return True
                cur = cur.parent
            return False

        def _alt_dugumler(self):
            stack = [c for c in reversed(self.children) if isinstance(c, _Dugum)]
            while stack:
                n = stack.pop()
                yield n
                for c in reversed(n.children):
                    if isinstance(c, _Dugum):
                        stack.append(c)

        def css(self, secici: str) -> list[_Dugum]:
            gruplar = _coz_secici(secici)
            if not gruplar:
                return []
            sonuc: list[_Dugum] = []
            for n in self._alt_dugumler():
                if any(n._eslesir_zincir(adimlar, self) for adimlar in gruplar):
                    sonuc.append(n)
            return sonuc

        def css_first(self, secici: str) -> _Dugum | None:
            gruplar = _coz_secici(secici)
            if not gruplar:
                return None
            for n in self._alt_dugumler():
                if any(n._eslesir_zincir(adimlar, self) for adimlar in gruplar):
                    return n
            return None

    class _Kurucu(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.kok = _Dugum("[document]", {})
            self.yigin: list[_Dugum] = [self.kok]

        def handle_starttag(self, tag: str, attrs):
            t = tag.lower()
            if t in _OTO_KAPANAN:
                sinir = _OTO_KAPANAN[t]
                for i in range(len(self.yigin) - 1, 0, -1):
                    if self.yigin[i].tag == t:
                        del self.yigin[i:]
                        break
                    if self.yigin[i].tag in sinir:
                        break
            ebeveyn = self.yigin[-1]
            attr_dict = {k.lower(): (v if v is not None else "") for k, v in attrs if k}
            dugum = _Dugum(t, attr_dict, ebeveyn)
            ebeveyn.children.append(dugum)
            if t not in _VOID:
                self.yigin.append(dugum)

        def handle_endtag(self, tag: str):
            t = tag.lower()
            if t in _VOID:
                return
            for i in range(len(self.yigin) - 1, 0, -1):
                if self.yigin[i].tag == t:
                    del self.yigin[i:]
                    break

        def handle_data(self, data: str):
            if data:
                self.yigin[-1].children.append(data)

    class LexborHTMLParser:
        def __init__(self, html: str):
            k = _Kurucu()
            k.feed(html or "")
            k.close()
            self._kok = k.kok

        def css(self, secici: str) -> list[_Dugum]:
            return self._kok.css(secici)

        def css_first(self, secici: str) -> _Dugum | None:
            return self._kok.css_first(secici)

        def text(self, deep: bool = True, separator: str = "", strip: bool = False) -> str:
            return self._kok.text(deep=deep, separator=separator, strip=strip)

    mod_sel = types.ModuleType("selectolax")
    mod_lex = types.ModuleType("selectolax.lexbor")
    mod_lex.LexborHTMLParser = LexborHTMLParser  # type: ignore[attr-defined]
    mod_sel.lexbor = mod_lex  # type: ignore[attr-defined]
    sys.modules["selectolax"] = mod_sel
    sys.modules["selectolax.lexbor"] = mod_lex


import functools  # noqa: E402

_selectolax_hazirla()
