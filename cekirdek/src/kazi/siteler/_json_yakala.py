"""Sayfanın kendi yüklediği JSON'u okuma yardımcıları (ek istek YOK, aşma YOK).

Normal bir ziyarette site zaten ürün listesini ya gömülü duruma (`window.X = {...}`,
`<script type="application/json">`) ya da XHR/fetch yanıtına koyar; burada yalnızca
o veri okunur. Hiçbir işlev tarama akışına istisna fırlatmaz.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

# Tek yanıt ve toplam bellek sınırları (bayt)
AZAMI_YANIT_BAYT = 3_000_000
AZAMI_TOPLAM_BAYT = 12_000_000
AZAMI_ADET = 60


class JsonYakalayici:
    """`pg.on("response")` dinleyicisi: URL'si desene uyan JSON yanıtlarını toplar.

    Arama yazılmadan ÖNCE oluşturulur, iş bitince `kapat()` (ya da `with`) ile
    sökülür. `.yanitlar`: [{"url", "durum", "json"}].
    """

    def __init__(self, pg: Any, url_desenleri: Iterable[str] = (),
                 azami_yanit_bayt: int = AZAMI_YANIT_BAYT,
                 azami_toplam_bayt: int = AZAMI_TOPLAM_BAYT,
                 azami_adet: int = AZAMI_ADET) -> None:
        self.pg = pg
        self._rx = [re.compile(d) for d in (url_desenleri or ())]
        self.azami_yanit_bayt = azami_yanit_bayt
        self.azami_toplam_bayt = azami_toplam_bayt
        self.azami_adet = azami_adet
        self.yanitlar: list[dict[str, Any]] = []
        self.toplam_bayt = 0
        self.atlanan = 0
        self._bagli = False
        try:
            pg.on("response", self._yanit)
            self._bagli = True
        except Exception:
            self._bagli = False

    def _yanit(self, resp: Any) -> None:
        try:
            if len(self.yanitlar) >= self.azami_adet:
                self.atlanan += 1
                return
            url = str(resp.url)
            if self._rx and not any(r.search(url) for r in self._rx):
                return
            if "json" not in str((resp.headers or {}).get("content-type", "")).lower():
                return
            uzun = (resp.headers or {}).get("content-length")
            if uzun and str(uzun).isdigit() and int(uzun) > self.azami_yanit_bayt:
                self.atlanan += 1
                return
            govde = resp.body()
            n = len(govde)
            if n > self.azami_yanit_bayt or self.toplam_bayt + n > self.azami_toplam_bayt:
                self.atlanan += 1
                return
            veri = json.loads(govde)
            self.toplam_bayt += n
            self.yanitlar.append({"url": url, "durum": resp.status, "json": veri})
        except Exception:
            return

    def kapat(self) -> None:
        if not self._bagli:
            return
        self._bagli = False
        try:
            self.pg.remove_listener("response", self._yanit)
        except Exception:
            pass

    def __enter__(self) -> "JsonYakalayici":
        return self

    def __exit__(self, *a: Any) -> None:
        self.kapat()


def html_atamasi(html: str, ifade: str) -> Any:
    """HTML içindeki `<ifade> = {...}` atamasını dengeli biçimde çözer (yoksa None).

    `ifade` düz metin aranır, örn. `window["__single-search-result__PROPS"]`
    ya da `'STATE':` (sonrasındaki değer).
    """
    try:
        i = (html or "").find(ifade)
        if i < 0:
            return None
        j = i + len(ifade)
        n = len(html)
        while j < n and html[j] in " \t\r\n=:":
            j += 1
        return json.JSONDecoder().raw_decode(html, j)[0]
    except Exception:
        return None


def gomulu_durum(pg: Any, seciciler: Iterable[str]) -> Any:
    """Sayfadaki gömülü JSON durumunu okur; ilk dolu olanı döndürür (yoksa None).

    Seçici biçimleri:
      "js:<ifade>"        -> `pg.evaluate("() => (<ifade>)")` (örn. `js:window.__NEXT_DATA__`)
      "script:<css>"      -> eşleşen `<script>` metni JSON olarak çözülür
                             (örn. `script:script#__NEXT_DATA__`, `script:script[type="application/ld+json"]`)
    """
    for sec in seciciler or ():
        try:
            tur, _, govde = str(sec).partition(":")
            if tur == "js":
                v = pg.evaluate(
                    "() => { try { const v = (" + govde + "); "
                    "return v === undefined ? null : JSON.parse(JSON.stringify(v)); }"
                    " catch (e) { return null; } }")
                if v:
                    return v
            elif tur == "script":
                metin = pg.evaluate(
                    "(s) => { const e = document.querySelector(s); return e ? e.textContent : null; }",
                    govde)
                if metin:
                    v = json.loads(metin)
                    if v:
                        return v
        except Exception:
            continue
    return None
