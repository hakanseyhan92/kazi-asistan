"""Taksit ve vade farkı ayrıştırıcıları için ortak yardımcılar."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse

from selectolax.lexbor import LexborHTMLParser

from kazi.modeller import Taksit
from kazi.siteler._kart import tl_sayi
from kazi.siteler._ortak import taksit_metinden
from kazi.tarayici import BotEngeli, blok_mu

log = logging.getLogger(__name__)

_KANONIK_BANKALAR: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Advantage", ("advantage",)),
    ("CardFinans", ("cardfinans", "card finans", "finansbank", "finans sanal pos", "qnb finansbank")),
    ("Kuveyt Türk", ("kuveyt türk", "kuveyt turk", "kuveyt_pos", "kuveytturk")),
    ("Türkiye Finans", ("türkiye finans", "turkiye finans", "turkiyefinans", "happy card")),
    ("Sağlam Kart", ("sağlam kart", "saglam kart", "saglamkart")),
    ("Bonus", ("bonus", "garanti", "flexi", "shop&fly", "miles&smiles")),
    ("World", ("world", "yapı kredi", "yapikredi", "pos net", "albaraka", "vakıfbank", "vakifbank", "anadolubank")),
    ("Maximum", ("maximum", "iş bankası", "is bankasi", "isbank", "işbank", "maximiles")),
    ("Axess", ("axess", "akbank", "wings", "free")),
    ("Paraf", ("paraf", "halkbank", "halk bank")),
    ("Bankkart", ("bankkart", "ziraat")),
    ("QNB", ("qnb",)),
)

_ATLANACAK_KART_ANAHTARLARI = (
    "havale",
    "eft",
    "tek çekim",
    "tek cekim",
    "peşin",
    "pesin",
    "visa",
    "mastercard",
    "diğer",
    "diger",
)

_AY_DESEN = re.compile(r"^\s*(\d{1,2})\s*(?:taksit|ay|x\b|$)", re.I)
_AY_ICINDE_DESEN = re.compile(r"\b(\d{1,2})\s*(?:taksit|ay|x\s*[\d.,]+)", re.I)
_PESIN_DESEN = re.compile(
    r"peşin\s*fiyat(?:ına|ına\s*kadar)?\s*(\d{1,2})\s*(?:taksit|x\b|ay)|"
    r"vade\s*farksız\s*(\d{1,2})\s*(?:taksit|ay)|"
    r"kredi[- ]kart[ıi]-(\d{1,2})-taksit",
    re.I,
)


def html_coz(html: str) -> LexborHTMLParser:
    """Lexbor HTML ayrıştırıcısı oluşturur."""
    return LexborHTMLParser(html or "")


def banka_normalize(ham: str | None) -> str | None:
    """Ham kart/banka adını kanonik Türkçe kart/banka adına çevirir."""
    if not ham:
        return None
    temiz = " ".join(ham.strip().split())
    kucuk = temiz.lower()
    if any(atla in kucuk for atla in _ATLANACAK_KART_ANAHTARLARI):
        # "Card Finans - Advantage Card" gibi bileşik adlarda Advantage yakalanmalı
        if not any(ad.lower() in kucuk for ad, _ in _KANONIK_BANKALAR if ad != "QNB"):
            return None
    for kanonik, anahtarlar in _KANONIK_BANKALAR:
        if any(a in kucuk for a in anahtarlar):
            return kanonik
    return None


def vade_farki_yuzde_hesapla(
    toplam: float | None,
    pesin_fiyat: float | None,
    pesin_mi: bool | None = None,
) -> float:
    """Toplam tutar ve peşin (sepet) fiyatından vade farkı yüzdesini hesaplar."""
    if pesin_mi is True:
        if toplam is None or not pesin_fiyat:
            return 0.0
        if abs(toplam - pesin_fiyat) <= max(1.0, pesin_fiyat * 0.002):
            return 0.0
    if toplam is None or not pesin_fiyat or pesin_fiyat <= 0:
        return 0.0
    fark = (toplam / pesin_fiyat - 1.0) * 100.0
    if abs(fark) <= 0.02:
        return 0.0
    if fark < 0:
        return 0.0
    return round(fark, 2)


def satir_olustur(
    banka: str | None,
    taksit_sayisi: int | None,
    aylik: float | None,
    toplam: float | None,
    pesin_fiyat: float | None,
    teklif_id: int = 0,
    pesin_mi: bool | None = None,
) -> Taksit | None:
    """Geçerli bir Taksit satırı üretir; tek çekim veya bozuk satırları eler."""
    if taksit_sayisi is None or taksit_sayisi < 2 or taksit_sayisi > 36:
        return None
    if toplam is None and aylik is not None and aylik > 0:
        toplam = round(aylik * taksit_sayisi, 2)
    if aylik is None and toplam is not None and toplam > 0:
        aylik = round(toplam / taksit_sayisi, 2)
    if aylik is not None:
        aylik = round(float(aylik), 2)
    if toplam is not None:
        toplam = round(float(toplam), 2)
    if aylik is not None and aylik <= 0:
        aylik = None
    if toplam is not None and toplam <= 0:
        toplam = None
    vade = vade_farki_yuzde_hesapla(toplam, pesin_fiyat, pesin_mi=pesin_mi)
    return Taksit(
        teklif_id=teklif_id,
        banka=banka,
        taksit_sayisi=int(taksit_sayisi),
        aylik=aylik,
        toplam=toplam,
        vade_farki_yuzde=vade,
    )


def pesin_taksit_satirlari(
    taksit_sayisi: int | None,
    sepet_fiyati: float | None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """Yalnız 'X taksit peşin fiyatına' bilgisi varsa banka=None satırı döndürür."""
    if not taksit_sayisi or taksit_sayisi < 2:
        return []
    fiyat = float(sepet_fiyati) if sepet_fiyati and sepet_fiyati > 0 else None
    satir = satir_olustur(
        banka=None,
        taksit_sayisi=taksit_sayisi,
        aylik=(round(fiyat / taksit_sayisi, 2) if fiyat else None),
        toplam=(round(fiyat, 2) if fiyat else None),
        pesin_fiyat=fiyat,
        teklif_id=teklif_id,
        pesin_mi=True,
    )
    return [satir] if satir else []


def ay_sayisi_coz(metin: str | None) -> int | None:
    """Hücre metninden taksit sayısını çıkarır ('Tek Çekim' için None döner)."""
    if not metin:
        return None
    temiz = " ".join(metin.strip().split())
    kucuk = temiz.lower()
    if "tek çekim" in kucuk or "tek cekim" in kucuk or kucuk == "peşin":
        return None
    m = _AY_DESEN.match(temiz) or _AY_ICINDE_DESEN.search(temiz)
    if not m:
        return None
    deger = int(m.group(1))
    return deger if 2 <= deger <= 36 else None


def tablo_satirlarini_dedup(satirlar: list[Taksit]) -> list[Taksit]:
    """Aynı (banka, taksit_sayisi) çiftlerini tekilleştirip sıralar."""
    gorulen: set[tuple[str | None, int]] = set()
    sonuc: list[Taksit] = []
    for s in satirlar:
        anahtar = (s.banka, s.taksit_sayisi)
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        sonuc.append(s)
    sonuc.sort(key=lambda x: (x.banka or "~~~", x.taksit_sayisi))
    return sonuc


def metinden_pesin_taksit(
    html: str,
    sepet_fiyati: float | None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """HTML/metin içinden peşin fiyatına taksit sayısını çıkarır."""
    if not html:
        return []
    n = taksit_metinden(html)
    if not n:
        m = _PESIN_DESEN.search(html)
        if m:
            for g in m.groups():
                if g:
                    n = int(g)
                    break
    return pesin_taksit_satirlari(n, sepet_fiyati, teklif_id=teklif_id)


def _tablo_bankasi(tablo_dugumu: Any) -> str | None:
    """Bir HTML <table> düğümünün çevresinden banka/kart adını bulmaya çalışır."""
    adaylar: list[str] = []
    for oz in ("id", "data-bank", "data-card", "data-bank-name", "aria-label", "title"):
        val = tablo_dugumu.attributes.get(oz)
        if val:
            adaylar.append(val)
    cur = tablo_dugumu
    for _ in range(4):
        if cur is None:
            break
        for oz in ("data-bank", "data-card", "data-bank-name", "id", "class"):
            val = cur.attributes.get(oz)
            if val:
                adaylar.append(val)
        for img in cur.css("img"):
            for oz in ("alt", "title", "src"):
                val = img.attributes.get(oz)
                if val:
                    adaylar.append(val)
            if len(adaylar) > 12:
                break
        for h in cur.css("h1, h2, h3, h4, h5, caption, .bank-name, .card-name"):
            txt = h.text(strip=True)
            if txt:
                adaylar.append(txt)
        for a in adaylar:
            b = banka_normalize(a)
            if b:
                return b
        prev = cur.prev
        while prev is not None and getattr(prev, "tag", None) in ("-text", None):
            prev = prev.prev
        if prev is not None:
            for a in [
                prev.text(strip=True) if hasattr(prev, "text") else "",
                *[img.attributes.get("alt", "") + " " + img.attributes.get("src", "") for img in prev.css("img")],
            ]:
                b = banka_normalize(a)
                if b:
                    return b
        cur = cur.parent
    return None


def html_tablolarini_ayristir(
    html: str,
    sepet_fiyati: float | None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """Genel Türkçe taksit HTML tablolarını ayrıştırır (yedek/ortak ayrıştırıcı)."""
    doc = html_coz(html)
    satirlar: list[Taksit] = []
    for tbl in doc.css("table"):
        banka = _tablo_bankasi(tbl)
        if not banka:
            continue
        basliklar = [c.text(strip=True).lower() for c in tbl.css("thead th, tr:first-child th")]
        aylik_idx: int | None = None
        toplam_idx: int | None = None
        for idx, b in enumerate(basliklar):
            if "aylık" in b or "aylik" in b or "taksit tutar" in b:
                aylik_idx = idx
            elif "toplam" in b:
                toplam_idx = idx
        for tr in tbl.css("tr"):
            hucreler = tr.css("td")
            if len(hucreler) < 2:
                continue
            metinler = [h.text(strip=True) for h in hucreler]
            satir_metni = " ".join(metinler).lower()
            taksit_n = None
            taksit_sutun = -1
            for idx, m in enumerate(metinler):
                n = ay_sayisi_coz(m)
                if n is not None:
                    taksit_n = n
                    taksit_sutun = idx
                    break
            if taksit_n is None:
                continue
            tutarlar: list[tuple[int, float]] = []
            for idx, m in enumerate(metinler):
                if idx == taksit_sutun and "x" not in m.lower():
                    continue
                val = tl_sayi(m.split("x", 1)[-1] if "x" in m.lower() else m)
                if val and val > 0:
                    tutarlar.append((idx, val))
            if not tutarlar:
                continue
            aylik = None
            toplam = None
            if aylik_idx is not None:
                aylik = next((v for i, v in tutarlar if i == aylik_idx), None)
            if toplam_idx is not None:
                toplam = next((v for i, v in tutarlar if i == toplam_idx), None)
            if aylik is None and toplam is None:
                if len(tutarlar) == 1:
                    _, v = tutarlar[0]
                    if sepet_fiyati and v < sepet_fiyati * 0.9:
                        aylik = v
                    else:
                        toplam = v
                else:
                    # "Vade Farkı" sütunlu 3+ tutarlı durumlar için en küçük aylık, en büyük toplam değildir:
                    aylik = tutarlar[0][1]
                    toplam = tutarlar[-1][1]
            pesin_mi = ("peşin" in satir_metni or "ücretsiz" in satir_metni or "vade farksız" in satir_metni)
            satir = satir_olustur(
                banka=banka,
                taksit_sayisi=taksit_n,
                aylik=aylik,
                toplam=toplam,
                pesin_fiyat=sepet_fiyati,
                teklif_id=teklif_id,
                pesin_mi=pesin_mi or None,
            )
            if satir:
                satirlar.append(satir)
    return tablo_satirlarini_dedup(satirlar)


def json_tablolarini_ayristir(
    veri: Any,
    sepet_fiyati: float | None,
    teklif_id: int = 0,
) -> list[Taksit]:
    """Yakalanmış API JSON yanıtlarından banka/taksit listelerini çıkarır."""
    satirlar: list[Taksit] = []

    def _gez(dugum: Any, miras_banka: str | None = None) -> None:
        if isinstance(dugum, list):
            for eleman in dugum:
                _gez(eleman, miras_banka)
            return
        if not isinstance(dugum, dict):
            return

        yerel_banka = miras_banka
        for anahtar in (
            "bankName",
            "cardName",
            "name",
            "cardFamily",
            "cardFamilyName",
            "familyName",
            "bank",
            "creditCardName",
        ):
            val = dugum.get(anahtar)
            if isinstance(val, str):
                b = banka_normalize(val)
                if b:
                    yerel_banka = b
                    break

        taksit_sayisi = None
        for k in ("months", "installmentCount", "installment", "count", "numberOfInstallments", "term", "duration"):
            v = dugum.get(k)
            if isinstance(v, (int, float)) and 2 <= int(v) <= 36:
                taksit_sayisi = int(v)
                break
            if isinstance(v, str) and v.isdigit() and 2 <= int(v) <= 36:
                taksit_sayisi = int(v)
                break

        aylik = _sayi_al(dugum, ("installmentAmount", "monthlyAmount", "monthlyPayment", "monthlyRate", "amountPerMonth", "installmentPrice"))
        toplam = _sayi_al(dugum, ("totalAmount", "totalPrice", "totalPayment", "total"))

        if taksit_sayisi and (aylik or toplam) and yerel_banka:
            has_interest = dugum.get("hasInterestRate")
            interest_free = dugum.get("interestFree") or dugum.get("withoutInterest")
            pesin_mi = True if interest_free is True else (False if has_interest is True else None)
            satir = satir_olustur(
                banka=yerel_banka,
                taksit_sayisi=taksit_sayisi,
                aylik=aylik,
                toplam=toplam,
                pesin_fiyat=sepet_fiyati,
                teklif_id=teklif_id,
                pesin_mi=pesin_mi,
            )
            if satir:
                satirlar.append(satir)

        for v in dugum.values():
            if isinstance(v, (dict, list)):
                _gez(v, yerel_banka)

    _gez(veri, None)
    return tablo_satirlarini_dedup(satirlar)


def _sayi_al(sozluk: dict[str, Any], anahtarlar: tuple[str, ...]) -> float | None:
    for k in anahtarlar:
        v = sozluk.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            f = tl_sayi(v)
            if f is not None:
                return f
        if isinstance(v, dict):
            for alt in ("value", "amount", "val", "price"):
                av = v.get(alt)
                if isinstance(av, (int, float)):
                    return float(av)
    return None


def mevcut_sekme_html(tarayici: Any, url: str) -> str | None:
    """Tarayıcıda zaten açık ve URL'si eşleşen bir sekme varsa yeni sayfa açmadan HTML'ini okur."""
    sekmeler = getattr(tarayici, "_sekmeler", None)
    if not isinstance(sekmeler, dict) or not sekmeler:
        return None
    hedef = urlparse(url)
    hedef_host = (hedef.hostname or "").lower().removeprefix("www.")
    hedef_yol = hedef.path.rstrip("/")
    for pg in list(sekmeler.values()):
        try:
            mevcut_url = pg.url if isinstance(pg.url, str) else pg.url()
            p = urlparse(mevcut_url)
            p_host = (p.hostname or "").lower().removeprefix("www.")
            if p_host == hedef_host and p.path.rstrip("/") == hedef_yol:
                return pg.content()
        except Exception:
            continue
    return None


def http_getir_guvenli(tarayici: Any, url: str, site: str) -> tuple[int, str]:
    """K0 HTTP GET yapar; 403/429/503 veya bot engeli gövdesinde BotEngeli fırlatır."""
    try:
        durum, govde = tarayici.http_getir(url, site=site)
    except HTTPError as exc:
        if exc.code in (401, 403, 429, 503):
            raise BotEngeli(site, url) from exc
        raise
    if durum in (401, 403, 429, 503) or blok_mu(govde):
        raise BotEngeli(site, url)
    return durum, govde


def sayfa_getir(
    tarayici: Any,
    site: str,
    url: str,
    *,
    ag_desen: str = r"installment|taksit|payment|lending",
    hazir_desen: str | None = None,
    html: str | None = None,
    yanitlar: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    """Önce hazır/mevcut sekme, sonra K0 HTTP, engelde K2 Brave ile sayfa + ağ yanıtı getirir.

    Döner: (html, ag_yanitlari, son_url)
    """
    if html is not None:
        return html, list(yanitlar or []), url

    if tarayici is None:
        return "", list(yanitlar or []), url

    acik_html = mevcut_sekme_html(tarayici, url)
    if acik_html and not blok_mu(acik_html):
        return acik_html, list(yanitlar or []), url

    yalniz_k0 = bool(getattr(tarayici, "yalniz_k0", False))

    if hasattr(tarayici, "http_getir"):
        try:
            _durum, govde = http_getir_guvenli(tarayici, url, site)
            return govde, list(yanitlar or []), url
        except BotEngeli:
            if yalniz_k0 or not hasattr(tarayici, "brave_ag_ve_govde"):
                raise
        except Exception as exc:
            log.debug("%s K0 getirme hatası (%s): %s", site, url, exc)
            if yalniz_k0 or not hasattr(tarayici, "brave_ag_ve_govde"):
                raise

    if yalniz_k0:
        raise BotEngeli(site, url)

    if hasattr(tarayici, "brave_ag_ve_govde"):
        toplanan, govde = tarayici.brave_ag_ve_govde(
            url,
            ag_desen,
            site=site,
            hazir_desen=hazir_desen,
        )
        return govde, toplanan, url

    if hasattr(tarayici, "brave_getir"):
        govde = tarayici.brave_getir(url, site=site)
        return govde, list(yanitlar or []), url

    raise BotEngeli(site, url)
