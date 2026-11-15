"""Değiştirilebilir LLM sağlayıcı (SARTNAME §9).

Kural tabanlı ayrıştırıcının `guven < 0.5` kaldığı sorgularda, yalnız o zaman, kısa (~300–800 token)
bir istek gider; yanıt yalnız JSON şema çıktısıdır. Hata/zaman aşımı → `None` (çağıran LLM'siz sürer).

Sağlayıcılar:
- `EvrenSaglayici`: OpenAI uyumlu EVREN uç noktası; base URL/anahtar `~/.config/evren/env`.
- `KapaliSaglayici`: hiçbir şey yapmaz (EVREN'in ücretsiz dönemi 01.12.2026'da biter; yalnız kural).

Anahtar HİÇBİR yere yazılmaz (log, istisna iletisi, repr).
"""
from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from .ayristir import KATEGORILER, YapisalSorgu

log = logging.getLogger("kazi.tanimlama.llm")

GUVEN_ESIGI = 0.5
VARSAYILAN_MODEL = "deepseek-v4.1-flash"
VARSAYILAN_ENV = Path.home() / ".config" / "evren" / "env"
ZAMAN_ASIMI_SN = 20.0

IZINLI_ANAHTARLAR = ("kategori", "marka", "model", "ozellikler", "haric", "butce_alt", "butce_ust")

# Katı JSON şeması (strict): serbest sözlük olmadığı için özellikler [{ad, deger}] listesi.
SEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": list(IZINLI_ANAHTARLAR),
    "properties": {
        "kategori": {"type": ["string", "null"], "enum": [*KATEGORILER, None]},
        "marka": {"type": ["string", "null"]},
        "model": {"type": ["string", "null"]},
        "ozellikler": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ad", "deger"],
                "properties": {"ad": {"type": "string"}, "deger": {"type": ["string", "number", "boolean"]}},
            },
        },
        "haric": {"type": "array", "items": {"type": "string"}},
        "butce_alt": {"type": ["number", "null"]},
        "butce_ust": {"type": ["number", "null"]},
    },
}

SISTEM_ISTEMI = (
    "Türkçe/İngilizce bir ürün arama isteğini yapısal sorguya çevir. Yalnız JSON döndür.\n"
    f"kategori şunlardan biri ya da null: {', '.join(KATEGORILER)}.\n"
    "marka: üretici/marka (ör. Asus, Corsair) ya da null. model: çip/model kodu (ör. 'RTX 5080', '9800X3D') ya da null.\n"
    "ozellikler: [{ad, deger}]; ad küçük harf ASCII (kapasite_gb, hiz_mts, cl, watt, kit, soket, yonga_seti, pcie, "
    "bellek_tipi, wifi, qvl, ekran_inc ...). Metinde olmayanı UYDURMA.\n"
    "haric: istenmeyen şeyler (laptop, hazır sistem ...). butce_alt/butce_ust: TL, sayı ya da null."
)


@runtime_checkable
class Saglayici(Protocol):
    def yapilandir(self, metin: str, ipucu: YapisalSorgu) -> dict | None:
        """Serbest metni yapısal sorgu sözlüğüne çevir; başaramazsa None."""
        ...


class KapaliSaglayici:
    """LLM kapalı: her zaman None."""

    def yapilandir(self, metin: str, ipucu: YapisalSorgu) -> dict | None:  # noqa: ARG002
        return None


def env_oku(yol: Path | str | None = None) -> dict[str, str]:
    """`KEY=VALUE` dosyası (yorum satırı/`export`/tırnak toleranslı). Yoksa boş sözlük."""
    yol = Path(yol) if yol else VARSAYILAN_ENV
    try:
        metin = yol.read_text(encoding="utf-8")
    except OSError:
        return {}
    d: dict[str, str] = {}
    for satir in metin.splitlines():
        satir = satir.strip()
        if not satir or satir.startswith("#") or "=" not in satir:
            continue
        if satir.startswith("export "):
            satir = satir[7:].strip()
        k, v = satir.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        d[k.strip()] = v
    return d


def kullanici_istemi(metin: str, ipucu: YapisalSorgu) -> str:
    """Kısa kullanıcı istemi: özgün metin + kural tabanlı ayrıştırıcının bulduğu."""
    bulunan = {k: v for k, v in {
        "kategori": ipucu.kategori, "marka": ipucu.marka, "model": ipucu.model,
        "ozellikler": ipucu.ozellikler or None, "haric": ipucu.haric or None,
        "butce_alt": ipucu.butce_alt, "butce_ust": ipucu.butce_ust,
    }.items() if v}
    return (f"İstek: {metin.strip()[:500]}\n"
            f"Kuralların bulduğu (eksik olabilir): {json.dumps(bulunan, ensure_ascii=False)}")


class EvrenSaglayici:
    """EVREN (OpenAI uyumlu `/v1/chat/completions`). `reasoning_effort: "none"`, 20 sn."""

    def __init__(self, env_yolu: Path | str | None = None, model: str = VARSAYILAN_MODEL,
                 zaman_asimi: float = ZAMAN_ASIMI_SN, istemci: httpx.Client | None = None):
        self._env_yolu = Path(env_yolu) if env_yolu else VARSAYILAN_ENV
        self.model = model
        self.zaman_asimi = zaman_asimi
        self._istemci = istemci

    def __repr__(self) -> str:                       # anahtar sızmasın
        return f"EvrenSaglayici(model={self.model!r})"

    def istek_govdesi(self, metin: str, ipucu: YapisalSorgu) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": SISTEM_ISTEMI},
                         {"role": "user", "content": kullanici_istemi(metin, ipucu)}],
            "max_tokens": 600,
            "temperature": 0,
            "reasoning_effort": "none",
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "yapisal_sorgu", "strict": True, "schema": SEMA}},
        }

    def yapilandir(self, metin: str, ipucu: YapisalSorgu) -> dict | None:
        env = env_oku(self._env_yolu)
        taban, anahtar = env.get("EVREN_BASE_URL", "").rstrip("/"), env.get("EVREN_API_KEY", "")
        if not taban or not anahtar:
            log.warning("EVREN yapılandırması eksik (EVREN_BASE_URL/EVREN_API_KEY); LLM atlandı")
            return None
        try:
            istemci = self._istemci or httpx.Client(timeout=self.zaman_asimi)
            try:
                r = istemci.post(taban + "/v1/chat/completions", json=self.istek_govdesi(metin, ipucu),
                                 headers={"Authorization": "Bearer " + anahtar}, timeout=self.zaman_asimi)
            finally:
                if self._istemci is None:
                    istemci.close()
            if r.status_code != 200:
                log.warning("EVREN HTTP %s", r.status_code)
                return None
            icerik = r.json()["choices"][0]["message"].get("content") or ""
            return yanit_ayikla(icerik)
        except Exception as e:                       # ağ, zaman aşımı, JSON ... → LLM'siz sür
            log.warning("EVREN çağrısı başarısız: %s", type(e).__name__)   # ileti/başlık YAZILMAZ
            return None


def yanit_ayikla(icerik: str) -> dict | None:
    """Model çıktısındaki JSON'u (çit/ön söz toleranslı) doğrulanmış sözlüğe çevir."""
    icerik = (icerik or "").strip()
    if not icerik:
        return None
    m = re.search(r"\{.*\}", icerik, re.S)
    if not m:
        return None
    try:
        ham = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(ham, dict):
        return None
    return dogrula(ham)


def dogrula(ham: dict) -> dict | None:
    """Yalnız izinli anahtarlar, doğru tipler. Hiçbiri kalmazsa None."""
    d: dict[str, Any] = {}
    kat = ham.get("kategori")
    if isinstance(kat, str) and kat in KATEGORILER:
        d["kategori"] = kat
    for k in ("marka", "model"):
        v = ham.get(k)
        if isinstance(v, str) and v.strip():
            d[k] = v.strip()[:80]
    oz = ham.get("ozellikler")
    if isinstance(oz, list):
        oz = {str(x.get("ad")): x.get("deger") for x in oz if isinstance(x, dict) and x.get("ad") is not None}
    if isinstance(oz, dict):
        temiz = {re.sub(r"[^a-z0-9_]", "", str(k).lower()): v for k, v in oz.items()
                 if isinstance(v, (str, int, float, bool))}
        temiz.pop("", None)
        if temiz:
            d["ozellikler"] = temiz
    h = ham.get("haric")
    if isinstance(h, list):
        h = [x.strip().lower() for x in h if isinstance(x, str) and x.strip()]
        if h:
            d["haric"] = h
    for k in ("butce_alt", "butce_ust"):
        v = ham.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            d[k] = float(v)
    return d or None


def birlestir(sorgu: YapisalSorgu, llm: dict | None) -> YapisalSorgu:
    """LLM çıktısını kural sonucuyla birleştir: kuralın bulduğu ASLA ezilmez, yalnız eksik doldurulur."""
    yeni = copy.deepcopy(sorgu)
    if not llm:
        return yeni
    katki = False
    for k in ("kategori", "marka", "model", "butce_alt", "butce_ust"):
        if llm.get(k) is not None and getattr(yeni, k) is None:
            setattr(yeni, k, llm[k])
            katki = True
    for k, v in (llm.get("ozellikler") or {}).items():
        if k not in yeni.ozellikler:
            yeni.ozellikler[k] = v
            katki = True
    for h in llm.get("haric") or []:
        if h not in yeni.haric:
            yeni.haric.append(h)
            katki = True
    if katki:
        yeni.guven = max(yeni.guven, 0.6 if yeni.kategori else 0.45)
    return yeni
