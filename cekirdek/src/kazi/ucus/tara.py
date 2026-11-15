"""Uçuş tarama orkestrası (SARTNAME §13): plan.bacaklar() + plan.yan_turlar × tarih × kaynak.

Kurallar:
- `plan.bacaklar()` (ana rota bacakları) ve `plan.yan_turlar` (gidiş/dönüş yan tur bacakları,
  `rotasyon=True`) için tarih seçimi `secilen_tarihler` ile yapılır.
- Kaynaklar birbirinden bağımsızdır: biri hata fırlatırsa (istisna, BotEngeli) diğerleri
  çalışmaya devam eder, tur ölmez.
- Tekilleştirme: aynı uçuşu (firma, kalkış/varış noktası ve zamanı, aktarmalar) birden
  çok kaynak döndürürse en ucuz gözlem temsilci seçilir.
- Sonuçlar `depo.kaydet` ile `segment_teklifi` tablosuna yazılır ve kapsam özeti üretilir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import threading
import time
from typing import Any, Callable

from ..seyahat.plan import Bacak, Plan, bacaklar as seyahat_bacaklar, ice_aktar_eski_plan
from ..seyahat.segment import Segment
from ..seyahat.transit import denetle
from ..seyahat.yan_tur import YanTur
from . import depo
from .kaynaklar import DESTEKLENMIYOR, HIZLI_KAYNAKLAR, KAYNAKLAR
from .model import BotEngeli, TR, Ucus, _tz, sehir_iata, ucus_segment

# `Plan.bacaklar()` metot çağrısını da destekle (SARTNAME / görev metni uyumu).
if not hasattr(Plan, "bacaklar"):
    Plan.bacaklar = seyahat_bacaklar  # type: ignore[attr-defined]


# ---------------------------------------------------------------- tarih seçimi

def _site_penceresi_kapat(t, site: str) -> None:
    """Havuz: bekleyen işi kalmayan sitenin bu işçideki penceresini kapat (sahte/eski tarayıcıda yöntem yoksa geç)."""
    kapat = getattr(t, "sekme_kapat", None)
    if callable(kapat):
        kapat(site)

def _bacak_sozluk_tarih(bacak) -> dict:
    if isinstance(bacak, dict):
        return bacak
    en_erken = getattr(bacak, "en_erken", None)
    en_gec = getattr(bacak, "en_gec", None)
    if en_erken and en_gec:
        orta = en_erken + (en_gec - en_erken) // 2
        esnek = (en_gec - en_erken).days // 2
        return {
            "id": getattr(bacak, "id", None),
            "tarih": orta.isoformat(),
            "esnek_gun": esnek,
            "en_erken": en_erken,
            "en_gec": en_gec,
            "rotasyon": bool(getattr(bacak, "rotasyon", False)),
        }
    return {"id": getattr(bacak, "id", None)}


def tarihler(bacak) -> list[str]:
    """Bacağın tarih penceresindeki tüm günler (ISO, artan sıra)."""
    b = _bacak_sozluk_tarih(bacak)
    if b.get("en_erken") and b.get("en_gec"):
        d1 = b["en_erken"] if isinstance(b["en_erken"], date) else date.fromisoformat(str(b["en_erken"]))
        d2 = b["en_gec"] if isinstance(b["en_gec"], date) else date.fromisoformat(str(b["en_gec"]))
        if d2 >= d1:
            return [(d1 + timedelta(days=i)).isoformat() for i in range((d2 - d1).days + 1)]
    try:
        gun = datetime.strptime(str(b.get("tarih")), "%Y-%m-%d").date()
    except Exception:
        return []
    try:
        esnek = max(int(b.get("esnek_gun") or 0), 0)
    except Exception:
        esnek = 0
    return [(gun + timedelta(days=i)).isoformat() for i in range(-esnek, esnek + 1)]


def secilen_tarihler(bacak, tarih_listesi=None, azami_tarih: int | None = None,
                     rotasyon: int | None = None) -> list[str]:
    """Bu turda taranacak tarihler.

    `azami_tarih` → ana tarihe en yakınlar; bacakta `rotasyon: True` ve `rotasyon`
    sayacı verilmişse (14.11 yan tur) penceredeki tarihler sırayla dolaşılır.
    """
    b = _bacak_sozluk_tarih(bacak)
    gunler = list(tarih_listesi) if tarih_listesi else tarihler(b)
    if azami_tarih and rotasyon is not None and b.get("rotasyon") and len(gunler) > 1:
        gunler = sorted(gunler)
        bas = int(rotasyon) % len(gunler)
        return [gunler[(bas + i) % len(gunler)] for i in range(min(int(azami_tarih), len(gunler)))]
    if azami_tarih and gunler:
        ana = str(b.get("tarih") or gunler[len(gunler) // 2])
        gunler.sort(key=lambda x: (
            x != ana,
            abs((datetime.strptime(x, "%Y-%m-%d") - datetime.strptime(ana, "%Y-%m-%d")).days),
        ))
        gunler = gunler[:max(1, int(azami_tarih))]
    return gunler


# ---------------------------------------------------------------- plan bacakları
def _bacak_kaydi_olustur(
    bid: str,
    kalkis_sehir: str,
    varis_sehir: str,
    en_erken: date | None,
    en_gec: date | None,
    yolcular: list,
    rotasyon: bool = False,
) -> dict:
    kalkis = sehir_iata(kalkis_sehir)
    varis = sehir_iata(varis_sehir)
    if en_erken and en_gec:
        orta = en_erken + (en_gec - en_erken) // 2
        esnek = (en_gec - en_erken).days // 2
        tarih_str = orta.isoformat()
    else:
        tarih_str = ""
        esnek = 0
    return {
        "id": bid or f"{kalkis.lower()}_{varis.lower()}",
        "kalkis": kalkis,
        "varis": varis,
        "kalkis_sehir": kalkis_sehir,
        "varis_sehir": varis_sehir,
        "tarih": tarih_str,
        "esnek_gun": esnek,
        "en_erken": en_erken,
        "en_gec": en_gec,
        "yolcu": max(len(yolcular), 1),
        "yolcular": list(yolcular),
        "rotasyon": rotasyon,
    }


def plan_bacaklari(plan: Plan | dict | None = None) -> list[dict]:
    """`plan.bacaklar()` ve `plan.yan_turlar` içinden taranacak tüm bacak sözlüklerini üret."""
    if plan is None:
        plan = ice_aktar_eski_plan()
    if isinstance(plan, dict):
        out: list[dict] = []
        for b in plan.get("bacaklar") or []:
            out.append({
                **b,
                "id": b.get("id") or f"{b.get('kalkis')}-{b.get('varis')}",
                "kalkis": sehir_iata(b.get("kalkis") or ""),
                "varis": sehir_iata(b.get("varis") or ""),
                "yolcu": max(int(b.get("yolcu") or 1), 1),
                "yolcular": b.get("yolcular") or [],
                "rotasyon": bool(b.get("rotasyon", False)),
            })
        return out

    bacak_fn = getattr(plan, "bacaklar", None)
    ana_bacaklar: list[Bacak] = bacak_fn() if callable(bacak_fn) else seyahat_bacaklar(plan)
    yolcu_sozluk = {y.id: y for y in (getattr(plan, "yolcular", None) or [])}

    liste: list[dict] = []
    for b in ana_bacaklar:
        kalkis = sehir_iata(b.kalkis_sehir)
        varis = sehir_iata(b.varis_sehir)
        bid = b.id or f"{kalkis.lower()}_{varis.lower()}"
        liste.append(
            _bacak_kaydi_olustur(
                bid=bid,
                kalkis_sehir=b.kalkis_sehir,
                varis_sehir=b.varis_sehir,
                en_erken=b.en_erken,
                en_gec=b.en_gec,
                yolcular=b.yolcular,
                rotasyon=False,
            )
        )

    for yt in getattr(plan, "yan_turlar", None) or []:
        if not isinstance(yt, YanTur):
            continue
        yt_yolcular = [yolcu_sozluk[yid] for yid in (yt.yolcu_idler or []) if yid in yolcu_sozluk]
        min_kalis = max(int(yt.kalis_gece or 0) - int(yt.kalis_esneklik or 0), 0)
        g_erken = yt.en_erken_gidis
        g_gec = (yt.en_gec_donus - timedelta(days=min_kalis)) if yt.en_gec_donus else yt.en_erken_gidis
        d_erken = (yt.en_erken_gidis + timedelta(days=min_kalis)) if yt.en_erken_gidis else yt.en_gec_donus
        d_gec = yt.en_gec_donus

        ana_iata = sehir_iata(yt.ana_sehir).lower()
        hedef_iata = sehir_iata(yt.hedef_sehir).lower()
        g_id = yt.gidis_bacak_id or f"{ana_iata}_{hedef_iata}"
        d_id = yt.donus_bacak_id or f"{hedef_iata}_{ana_iata}"

        liste.append(
            _bacak_kaydi_olustur(
                bid=g_id,
                kalkis_sehir=yt.ana_sehir,
                varis_sehir=yt.hedef_sehir,
                en_erken=g_erken,
                en_gec=g_gec,
                yolcular=yt_yolcular,
                rotasyon=True,
            )
        )
        liste.append(
            _bacak_kaydi_olustur(
                bid=d_id,
                kalkis_sehir=yt.hedef_sehir,
                varis_sehir=yt.ana_sehir,
                en_erken=d_erken,
                en_gec=d_gec,
                yolcular=yt_yolcular,
                rotasyon=True,
            )
        )
    return liste


# ---------------------------------------------------------------- tekilleştirme
def tekillestir(segmentler: list[Segment]) -> list[Segment]:
    """Aynı uçuşun farklı kaynaklardan gelen segmentlerini tekilleştir (en ucuz kaynak kalır)."""
    gruplar: dict[tuple, list[Segment]] = {}
    for s in segmentler:
        k = (
            s.arac,
            (s.firma or "").strip().lower(),
            s.kalkis_nokta,
            s.varis_nokta,
            tuple(a.nokta for a in s.aktarmalar),
            int(s.aktarma_sayisi or 0),
            s.kalkis,
            s.varis,
        )
        gruplar.setdefault(k, []).append(s)

    out: list[Segment] = []
    for g in gruplar.values():
        temsilci = min(g, key=lambda x: x.kisi_basi)
        if any(x.ayri_bilet for x in g):
            for a in temsilci.aktarmalar:
                a.ayri_bilet = True
        out.append(temsilci)
    out.sort(key=lambda s: (s.kalkis[:10], s.kisi_basi))
    return out


# ---------------------------------------------------------------- sonuç
@dataclass
class TaramaSonucu:
    zaman: str
    bacak_idler: list[str] = field(default_factory=list)
    kaynaklar: list[str] = field(default_factory=list)
    segmentler: dict[str, list[Segment]] = field(default_factory=dict)
    kapsam: dict[tuple[str, str, str], dict] = field(default_factory=dict)
    elenen: dict[str, int] = field(default_factory=dict)
    hatalar: list[str] = field(default_factory=list)

    @property
    def ucuslar(self) -> dict[str, list[Segment]]:
        return self.segmentler

    def kaynak_ok(self) -> list[str]:
        ok = set()
        for (_b, _t, k), v in self.kapsam.items():
            if k not in DESTEKLENMIYOR and v.get("hata") is None and v.get("tur") in ("ok", "bos"):
                ok.add(k)
        return sorted(ok)

    def kapsam_ozeti(self) -> str:
        deneme = len(self.kapsam)
        sonuclu = sum(1 for v in self.kapsam.values() if v.get("sayi", 0) > 0)
        aktif = [k for k in self.kaynaklar if k not in DESTEKLENMIYOR]
        return (
            f"kapsam: {len(self.bacak_idler)}/{len(self.bacak_idler)} bacak, "
            f"{sonuclu}/{deneme} deneme sonuçlu, "
            f"kaynak {len(self.kaynak_ok())}/{len(aktif)} çalıştı"
        )

    def tablo(self) -> str:
        if not self.kapsam:
            return "(kapsam kaydı yok)"
        satirlar = []
        for (b, tr, k), v in sorted(self.kapsam.items()):
            if v.get("hata"):
                etiket = "BLOK" if v.get("tur") == "blok" else "HATA"
                durum = f"{etiket}: {v['hata']}"
            elif v.get("sayi", 0):
                durum = f"{v['sayi']} sonuç ({v.get('yontem') or '?'}, {v.get('sure', 0):.1f}s)"
            else:
                durum = "0 sonuç"
            satirlar.append(f"{b:<10} {tr}  {k:<12} {durum}")
        satirlar.append(self.kapsam_ozeti())
        return "\n".join(satirlar)


# ---------------------------------------------------------------- çalıştırma
def _dene(kaynak: str, t, bacak: dict, tarih: str, zaman: str) -> tuple[list[Segment], str, str | None, str]:
    """Tek kaynak denemesi → (segmentler, yöntem, hata, tür). İstisna yukarı sızmaz."""
    mod = KAYNAKLAR[kaynak]
    yontem = "http" if kaynak == "google" else "brave"
    try:
        if t is not None and getattr(t, "hiz_sinirla", None):
            t.hiz_sinirla(kaynak)
        ham = mod.ara(t, bacak, tarih) or []
        sonuc: list[Segment] = []
        for item in ham:
            if isinstance(item, Segment):
                seg = item
            elif isinstance(item, Ucus):
                seg = ucus_segment(item)
            else:
                seg = None
            if seg is not None:
                seg.gozlem_zamani = _tz(seg.gozlem_zamani) if seg.gozlem_zamani else zaman
                if not seg.kaynak:
                    seg.kaynak = kaynak
                sonuc.append(seg)
        return sonuc, yontem, None, ("ok" if sonuc else "bos")
    except BotEngeli as e:
        return [], yontem, f"blok: {e}", "blok"
    except NotImplementedError as e:
        return [], yontem, f"desteklenmiyor: {e}", "desteklenmiyor"
    except Exception as e:
        ad = type(e).__name__
        if ad == "BotEngeli":
            return [], yontem, f"blok: {e}", "blok"
        if ad == "KilitZamanAsimi":
            return [], yontem, f"brave kilidi: {e}", "hata"
        return [], yontem, f"{ad}: {e}", "hata"


def _is_kaydi(kaynak, bacak, tarih, bulunan, yontem, hata, tur, sure, t=None) -> None:
    """Uçuş işi BİTER BİTMEZ kayıt defterine (15.11: önceden her şey tarama sonunda yazılıyordu → arayüzde dakikalarca
    'taranıyor' ama ilerleme yok)."""
    from .. import gunluk
    try:
        en_ucuz = min((s.kisi_basi for s in bulunan), default=None)
    except Exception:
        en_ucuz = None
    if yontem == "http":
        tar = "HTTP (K0)"
    elif yontem == "yerel_brave":
        tar = "Brave (yerel profil klonu)"
    else:
        tar = getattr(t, "gunluk_adi", None) or "Brave (kazı)"
    seviye = "hata" if tur == "hata" else ("uyari" if tur in ("blok", "bos") else "bilgi")
    if tur in ("desteklenmiyor", "blok_atlandi"):
        seviye = "bilgi"
    gunluk.yaz(seviye, "ucus", f"{bacak.get('id')} {tarih}: {len(bulunan)} uçuş"
               + (", en ucuz " + f"{en_ucuz:,.0f}".replace(",", ".") + " TL/kişi" if en_ucuz else "")
               + (f" — {hata}" if hata else ""),
               olay=f"ucus_bitti:{tur}", site=kaynak, tarayici=tar, adet=len(bulunan), sure_sn=sure,
               veri={"bacak": bacak.get("id"), "tarih": tarih, "tur": tur})


def _paralel_tara(secili, srcs, sonuc, zaman, tarayici_fabrika, sekme_sayisi, engelli,
                  hizli, tarih_listesi, azami_tarih, rotasyon, yerel_dene, bacak_isle) -> None:
    """Çok sekmeli yol: (kaynak, (bacak, tarih)) işleri havuzda; bir kaynak aynı anda tek pencerede.
    Yerel profil yedeği havuz bittikten sonra çağıran iş parçacığında (tek sync_playwright) sırayla denenir."""
    from ..tarama import havuz

    # iş listesi bacak-ana sıralı; kayıt = (bacak, tarih, kaynak) ya da doğrudan sonuç (desteklenmiyor)
    kayitlar: list[tuple[dict, str, str]] = []
    for bacak in secili:
        for tarih in secilen_tarihler(bacak, tarih_listesi, azami_tarih, rotasyon):
            for kaynak in srcs:
                if hizli and kaynak == "skyscanner" and tarih != bacak.get("tarih"):
                    continue
                kayitlar.append((bacak, tarih, kaynak))
    isler = [(k, i) for i, (_b, _t, k) in enumerate(kayitlar) if k not in DESTEKLENMIYOR]
    kilit = threading.Lock()
    sonuclar: dict[int, tuple[list[Segment], str, str | None, str, float]] = {}

    def _is(t, kaynak: str, i: int):
        bacak, tarih, _k = kayitlar[i]
        t0 = time.monotonic()
        with kilit:
            neden = engelli.get(kaynak)
        if neden is not None:
            return [], "", f"atlandı: bu taramada engellendi ({neden})", "blok_atlandi", 0.0
        bulunan, yontem, hata, tur = _dene(kaynak, t, bacak, tarih, zaman)
        if tur == "blok":
            with kilit:
                engelli.setdefault(kaynak, f"{bacak['id']} {tarih}")
        sure = round(time.monotonic() - t0, 1)
        _is_kaydi(kaynak, bacak, tarih, bulunan, yontem, hata, tur, sure, t)
        return bulunan, yontem, hata, tur, sure

    ozet = havuz.calistir(isler, sekme_sayisi, kaynak_fabrika=tarayici_fabrika, is_fn=_is,
                          kaynak_kapat=lambda t: t.kapat(), site_bitti=_site_penceresi_kapat)
    for r in ozet.sonuclar:
        if r.sira < 0:
            sonuc.hatalar.append(f"sekme açılamadı: {type(r.hata).__name__}: {r.hata}")
            continue
        i = r.yuk
        if r.hata is not None:
            sonuclar[i] = ([], "brave", f"{type(r.hata).__name__}: {r.hata}", "hata", 0.0)
        else:
            sonuclar[i] = r.deger

    # yerel yedek: engellenen her kaynağın İLK denemesi için, havuz kapandıktan sonra, sırayla
    ilk_blok: dict[str, int] = {}
    for i, (_b, _t, k) in enumerate(kayitlar):
        if i in sonuclar and sonuclar[i][3] == "blok" and k not in ilk_blok:
            ilk_blok[k] = i
    for k, i in ilk_blok.items():
        bacak, tarih, _k = kayitlar[i]
        y = yerel_dene(k, bacak, tarih)
        if y is not None:
            sure = sonuclar[i][4]
            sonuclar[i] = (y[0], "yerel_brave", y[2], y[3], sure)
            if y[3] != "blok":
                with kilit:
                    engelli.pop(k, None)        # yerel profil geçti: kaynak engelli sayılmaz (sonraki işler zaten atlandı)

    hepsi: dict[str, list[Segment]] = {b["id"]: [] for b in secili}
    for i, (bacak, tarih, kaynak) in enumerate(kayitlar):
        if kaynak in DESTEKLENMIYOR:
            bulunan, yontem, hata, tur, sure = [], "", f"desteklenmiyor: {DESTEKLENMIYOR[kaynak]}", "desteklenmiyor", 0.0
        else:
            bulunan, yontem, hata, tur, sure = sonuclar.get(
                i, ([], "", "işçi kalmadı", "hata", 0.0))
        sonuc.kapsam[(bacak["id"], tarih, kaynak)] = {
            "sayi": len(bulunan), "yontem": yontem, "hata": hata, "tur": tur, "sure": sure,
        }
        hepsi[bacak["id"]].extend(bulunan)
    for bacak in secili:
        bacak_isle(bacak, hepsi[bacak["id"]])


def _site_olaylari(v, sonuc: "TaramaSonucu") -> None:
    """Her (bacak, tarih, kaynak) denemesi için `tarama_site_bitti` olayı: arayüzün site sağlığı bunu okur."""
    if v is None:
        return
    try:
        from ..tarama import depo as tdepo
    except Exception:
        return
    for (bacak_id, tarih, kaynak), k in sonuc.kapsam.items():
        try:
            tdepo.olay_kaydet(
                v, "tarama_site_bitti",
                mesaj=f"{kaynak}: {k.get('sayi', 0)} sonuç ({k.get('sure', 0)}s, ucus)",
                veri={"site": kaynak, "adet": k.get("sayi", 0), "sure": k.get("sure", 0), "hata": k.get("hata"),
                      "tur": k.get("tur"), "katman": "ucus", "bacak_id": bacak_id, "tarih": tarih},
                zaman=sonuc.zaman,
            )
        except Exception:
            return      # olay tablosu yok / yazılamadı: tarama sonucunu etkilemesin


def tara(
    v=None,
    tarayici=None,
    plan: Plan | dict | None = None,
    kaynaklar=None,
    azami_tarih: int | None = None,
    rotasyon: int | None = None,
    bacak_idler=None,
    hizli: bool = False,
    tarih_listesi=None,
    yerel_fabrika=None,
    sekme_sayisi: int | None = None,
    tarayici_fabrika: Callable[[int], Any] | None = None,
) -> TaramaSonucu:
    """Planın ana bacaklarını ve yan turlarını tarar, tekilleştirip `segment_teklifi`'ne yazar."""
    tum_bacaklar = plan_bacaklari(plan)
    secili = [b for b in tum_bacaklar if not bacak_idler or b.get("id") in bacak_idler]
    if bacak_idler:
        bilinmeyen = [b for b in bacak_idler if b not in {x.get("id") for x in secili}]
        if bilinmeyen:
            raise KeyError(f"bacak yok: {', '.join(bilinmeyen)}")

    srcs = list(kaynaklar) if kaynaklar else (list(HIZLI_KAYNAKLAR) if hizli else list(KAYNAKLAR))
    for s in srcs:
        if s not in KAYNAKLAR:
            raise KeyError(f"kaynak yok: {s}")

    depo.kur(v)
    yerel: dict = {"t": None, "bitti": False}      # yerel profil yedeği: taramada en çok bir kez, tembel kurulur

    def _yerel_dene(kaynak, bacak, tarih):
        """Engellenen deneme için son çare: yerel Brave klonu. Başarısızsa None (ilk blok sonucu korunur)."""
        if yerel_fabrika is None or yerel["bitti"]:
            return None
        try:
            from ..tarayici.paralel_ayar import paralel_ayarlar
            if not paralel_ayarlar().get("yerel_profil_yedek"):
                return None
        except Exception:
            return None
        if yerel["t"] is None:
            try:
                if tarayici is not None and getattr(tarayici, "brave_birak", None):
                    tarayici.brave_birak()          # aynı iş parçacığında tek sync_playwright
                yerel["t"] = yerel_fabrika()
            except Exception:
                yerel["bitti"] = True
                return None
        try:
            r = _dene(kaynak, yerel["t"], bacak, tarih, zaman)
        finally:
            try:        # sonraki denemeler kazı Brave'ine döner: aynı iş parçacığında iki Playwright açık kalmasın (nesne yeniden kullanılır)
                yerel["t"].kapat()
            except Exception:
                pass
        return r if r[3] in ("ok", "bos") else None

    zaman = datetime.now(TR).isoformat(timespec="seconds")
    sonuc = TaramaSonucu(
        zaman=zaman,
        bacak_idler=[b["id"] for b in secili],
        kaynaklar=srcs,
    )

    def _bacak_isle(bacak: dict, hepsi: list[Segment]) -> None:
        """Bir bacağın toplanan segmentleri: tekilleştir → transit denetle → depoya yaz (yalnız çağıran iş parçacığı)."""
        bacak_id = bacak["id"]
        tek = tekillestir(hepsi)
        yolcular = bacak.get("yolcular") or []
        elenme_haritasi: dict[str, str] = {}
        elenen_sayisi = 0
        if yolcular:
            for seg in tek:
                ts = denetle(seg, yolcular)
                if not ts.gecer and ts.gerekceler:
                    elenen_sayisi += 1
                    elenme_haritasi[seg.id] = "; ".join(ts.gerekceler)
        sonuc.segmentler[bacak_id] = tek
        sonuc.elenen[bacak_id] = elenen_sayisi
        if tek:
            try:
                depo.kaydet(v, tek, bacak_id, elenme_gerekceleri=elenme_haritasi)
            except Exception as e:
                sonuc.hatalar.append(f"{bacak_id} segmentler yazılamadı: {type(e).__name__}: {e}")

    paralel = bool(sekme_sayisi and sekme_sayisi > 1 and tarayici_fabrika is not None)

    try:
        engelli: dict[str, str] = {}
        if paralel:
            _paralel_tara(secili, srcs, sonuc, zaman, tarayici_fabrika, int(sekme_sayisi), engelli,
                          hizli, tarih_listesi, azami_tarih, rotasyon, _yerel_dene, _bacak_isle)
            secili_sirali = []
        else:
            secili_sirali = secili
        for bacak in secili_sirali:
            bacak_id = bacak["id"]
            gunler = secilen_tarihler(bacak, tarih_listesi, azami_tarih, rotasyon)
            hepsi: list[Segment] = []
            for tarih in gunler:
                for kaynak in srcs:
                    if hizli and kaynak == "skyscanner" and tarih != bacak.get("tarih"):
                        continue
                    t0 = time.monotonic()
                    if kaynak in DESTEKLENMIYOR:
                        hata = f"desteklenmiyor: {DESTEKLENMIYOR[kaynak]}"
                        bulunan, yontem, tur = [], "", "desteklenmiyor"
                    elif kaynak in engelli:
                        # 15.11: bu taramada engellenen kaynak (yerel profil de geçemedi) diğer bacaklarda tekrar
                        # denenmez — her bacakta aynı doğrulama sayfasında dakikalar harcanıyordu (Skyscanner)
                        hata = f"atlandı: bu taramada engellendi ({engelli[kaynak]})"
                        bulunan, yontem, tur = [], "", "blok_atlandi"
                    else:
                        bulunan, yontem, hata, tur = _dene(kaynak, tarayici, bacak, tarih, zaman)
                        if tur == "blok":
                            y = _yerel_dene(kaynak, bacak, tarih)
                            if y is not None:
                                bulunan, yontem, hata, tur = y[0], "yerel_brave", y[2], y[3]
                        if tur == "blok":
                            engelli[kaynak] = f"{bacak_id} {tarih}"
                    sure = round(time.monotonic() - t0, 1)
                    if tur != "desteklenmiyor":
                        _is_kaydi(kaynak, bacak, tarih, bulunan, yontem, hata, tur, sure, tarayici)
                    sonuc.kapsam[(bacak_id, tarih, kaynak)] = {
                        "sayi": len(bulunan),
                        "yontem": yontem,
                        "hata": hata,
                        "tur": tur,
                        "sure": sure,
                    }
                    hepsi.extend(bulunan)

            _bacak_isle(bacak, hepsi)
        _site_olaylari(v, sonuc)
    finally:
        if yerel["t"] is not None:
            try:
                yerel["t"].kapat()
            except Exception:
                pass

    return sonuc
