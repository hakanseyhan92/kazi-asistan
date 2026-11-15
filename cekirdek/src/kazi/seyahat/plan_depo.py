"""Seyahat planı: JSON biçimi, doğrulama, ön izleme, kalıcı kayıt ve eski tarayıcıya aktarım (SARTNAME §13.1–13.2, §13.5).

Arayüz planı bu biçimde okur/yazar (GET/PUT /seyahat/plan). Biçim (surum 1):
{
  "surum": 1, "ad": "Tokyo 2026-27",
  "yolcular": [{"id", "ad", "uyruklar": ["TR"], "izinler": [{"ulke", "tur": vize|ikamet|eta, "bitis": "YYYY-AA-GG"|null,
                                                              "durum": gercek|varsayim, "not"}]}],
  "duraklar": [{"sehir", "noktalar": [kod…]|null, "tarih": "YYYY-AA-GG"|null, "esneklik": 0-3, "kalis_gece": int|null,
                "gecis_noktasi": bool}],
  "bacaklar": [{"id"?, "yolcular": [id…], "araclar": ["ucak","yht","otobus"]}],
              # len(bacaklar) == len(duraklar) - 1; i. bacak = duraklar[i] → duraklar[i+1]
  "yan_turlar": [{"ana_sehir", "hedef_sehir", "yolcular": [id…], "kalis_gece", "kalis_esneklik", "en_erken_gidis", "en_gec_donus",
                  "gidis_id"?, "donus_id"?}]
}
"""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .plan import ARACLAR, Bacak, Durak, Plan, Yolcu, bacaklar, ice_aktar_eski_plan, varsayilan_araclar
from .segment import noktalar, veri
from .transit import gecerli_izin, kural, kurallar_yukle, ulke_adi
from .yan_tur import YanTur

SURUM = 1
ESKI_PLAN = Path.home() / "evren-asistan" / "ucus" / "plan.json"
METRO = {"İstanbul": "IST", "Tokyo": "TYO", "Seul": "SEL", "Osaka": "OSA"}       # eski tarayıcının şehir kodları
ESKI_SEHIR = {"IST": "İstanbul", "TYO": "Tokyo", "SEL": "Seul", "OSA": "Osaka"}
ADLAR = {"İstanbul": ["Istanbul", "Constantinople"], "Tokyo": ["Narita", "Haneda"], "Seul": ["Seoul", "Incheon", "Gimpo"],
         "Osaka": ["Kansai", "Itami"], "İzmir": ["Izmir"], "Eskişehir": ["Eskisehir"]}


def plan_yolu() -> Path:
    ozel = os.environ.get("KAZI_PLAN")
    if ozel:
        return Path(ozel).expanduser()
    kok = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return kok / "kazi" / "seyahat_plan.json"


# ------------------------------------------------------------------ yer arama
def katla(s: str) -> str:
    """Türkçe duyarsız arama anahtarı: İ/ı/ş/ğ… → i/i/s/g, aksanlar atılır, küçük harf."""
    s = s.replace("İ", "i").replace("I", "ı").lower().replace("ı", "i")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _havalimanlari() -> list[dict]:
    try:
        return veri("havalimanlari.json")
    except FileNotFoundError:
        return []


def yerler() -> list[dict]:
    """Seçilebilir şehirler: önce elle tutulan noktalar.json (gar/otogar dahil), sonra tarifeli havalimanı olan şehirler."""
    ulkeler = veri("ulkeler.json")
    sonuc, gorulen = [], set()
    for sehir, liste in noktalar().items():
        ulke = liste[0]["ulke"]
        sonuc.append({"sehir": sehir, "ulke": ulke, "ulke_ad": ulkeler.get(ulke, ulke), "kaynak": "elle",
                      "noktalar": [{"kod": n["kod"], "ad": n["ad"], "tur": n["tur"]} for n in liste]})
        gorulen.update(n["kod"] for n in liste)
    grup: dict[tuple, list[dict]] = {}
    for h in _havalimanlari():
        if h["kod"] in gorulen:
            continue
        grup.setdefault((h["sehir"], h["ulke"]), []).append(h)
    for (sehir, ulke), liste in grup.items():
        sonuc.append({"sehir": sehir, "ulke": ulke, "ulke_ad": ulkeler.get(ulke, ulke), "kaynak": "ourairports",
                      "buyuk": any(h["buyuk"] for h in liste),
                      "noktalar": [{"kod": h["kod"], "ad": h["ad"], "tur": "havalimani"} for h in liste]})
    return sonuc


def yer_ara(sorgu: str, azami: int = 12) -> list[dict]:
    """Şehir adı, Türkçe/İngilizce ad, havalimanı kodu ya da adıyla arama (Türkçe harf duyarsız)."""
    q = katla(sorgu.strip())
    if not q:
        return []
    puanli = []
    for y in yerler():
        adlar = [y["sehir"], *ADLAR.get(y["sehir"], [])]
        kodlar = [n["kod"] for n in y["noktalar"]]
        ka = [katla(a) for a in adlar]
        if len(q) == 3 and q.upper() in kodlar:
            p = 0
        elif any(a == q for a in ka):
            p = 1
        elif any(a.startswith(q) for a in ka):
            p = 2
        elif any(q in katla(n["ad"]) for n in y["noktalar"]) or any(q in a for a in ka):
            p = 3
        else:
            continue
        on = 0 if y["kaynak"] == "elle" else (1 if y.get("buyuk") else 2)
        puanli.append((p, on, y["sehir"], y))
    puanli.sort(key=lambda x: x[:3])
    return [y for *_, y in puanli[:azami]]


def sehir_noktalari(sehir: str) -> list[dict]:
    for y in yerler():
        if y["sehir"] == sehir:
            return y["noktalar"]
    return []


# ------------------------------------------------------------------ biçim ↔ Plan
def _tarih(s: Any) -> date | None:
    if not s:
        return None
    return s if isinstance(s, date) else date.fromisoformat(str(s)[:10])


def _bacak_id(a: str, b: str, kullanilan: set[str]) -> str:
    def kod(s):
        if s in METRO:
            return METRO[s].lower()
        if s in noktalar():                        # elle tutulan TR/çok noktalı şehir: ad kısaltması (ank, esk…)
            return katla(s)[:3]
        nk = sehir_noktalari(s)
        return (nk[0]["kod"] if nk else katla(s)[:3]).lower()
    temel = f"{kod(a)}_{kod(b)}"
    bid, n = temel, 2
    while bid in kullanilan:
        bid, n = f"{temel}_{n}", n + 1
    kullanilan.add(bid)
    return bid


def plan_sozluk(plan: Plan, ad: str = "Seyahat planı") -> dict:
    """Plan nesnesi → arayüz biçimi."""
    n = len(plan.duraklar) - 1
    d = {"surum": SURUM, "ad": ad,
         "yolcular": [{"id": y.id, "ad": y.ad, "uyruklar": list(y.uyruklar), "izinler": [dict(i) for i in y.izinler]}
                      for y in plan.yolcular],
         "duraklar": [{"sehir": x.sehir, "noktalar": None, "tarih": x.tarih.isoformat() if x.tarih else None,
                       "esneklik": x.esneklik, "kalis_gece": x.kalis_gece, "gecis_noktasi": x.gecis_noktasi}
                      for x in plan.duraklar],
         "bacaklar": [], "yan_turlar": []}
    for i in range(n):
        d["bacaklar"].append({"id": plan.bacak_idleri.get(i), "yolcular": plan.bacak_yolculari.get(i) or [y.id for y in plan.yolcular],
                              "araclar": plan.bacak_araclari.get(i) or varsayilan_araclar(plan.duraklar[i].sehir, plan.duraklar[i + 1].sehir)})
    for yt in plan.yan_turlar:
        d["yan_turlar"].append({"ana_sehir": yt.ana_sehir, "hedef_sehir": yt.hedef_sehir, "yolcular": list(yt.yolcu_idler),
                                "kalis_gece": yt.kalis_gece, "kalis_esneklik": yt.kalis_esneklik,
                                "en_erken_gidis": yt.en_erken_gidis.isoformat() if yt.en_erken_gidis else None,
                                "en_gec_donus": yt.en_gec_donus.isoformat() if yt.en_gec_donus else None,
                                "gidis_id": yt.gidis_bacak_id, "donus_id": yt.donus_bacak_id})
    return d


def plan_nesnesi(d: dict) -> Plan:
    """Arayüz biçimi → Plan. Önce `dogrula` çağrılmalı; eksik id'ler burada üretilir."""
    yolcular = [Yolcu(y["id"], y.get("ad") or y["id"], list(y.get("uyruklar") or []), [dict(i) for i in y.get("izinler") or []])
                for y in d.get("yolcular") or []]
    duraklar = [Durak(x["sehir"], _tarih(x.get("tarih")), int(x.get("esneklik") or 0), x.get("kalis_gece"),
                      bool(x.get("gecis_noktasi"))) for x in d.get("duraklar") or []]
    kullanilan: set[str] = set()
    by, ba, bi = {}, {}, {}
    for i, b in enumerate(d.get("bacaklar") or []):
        if i >= len(duraklar) - 1:
            break
        bid = b.get("id") or _bacak_id(duraklar[i].sehir, duraklar[i + 1].sehir, kullanilan)
        kullanilan.add(bid)
        by[i], ba[i], bi[i] = list(b.get("yolcular") or []), list(b.get("araclar") or []), bid
    yan = []
    for t in d.get("yan_turlar") or []:
        gid = t.get("gidis_id") or _bacak_id(t["ana_sehir"], t["hedef_sehir"], kullanilan)
        did = t.get("donus_id") or _bacak_id(t["hedef_sehir"], t["ana_sehir"], kullanilan)
        kullanilan.update((gid, did))
        yan.append(YanTur(t["ana_sehir"], t["hedef_sehir"], list(t.get("yolcular") or []), int(t.get("kalis_gece") or 2),
                          int(t.get("kalis_esneklik") or 0), _tarih(t.get("en_erken_gidis")), _tarih(t.get("en_gec_donus")), gid, did))
    plan = Plan(duraklar=duraklar, yolcular=yolcular, bacak_yolculari=by, bacak_araclari=ba, bacak_idleri=bi, yan_turlar=yan)
    return plan


# ------------------------------------------------------------------ doğrulama + ön izleme
def dogrula(d: dict) -> list[dict]:
    """Engelleyici hatalar: [{alan, mesaj}]. Boş liste = kaydedilebilir."""
    h: list[dict] = []

    def hata(alan, mesaj):
        h.append({"alan": alan, "mesaj": mesaj})

    ids = [y.get("id") for y in d.get("yolcular") or []]
    if not ids:
        hata("yolcular", "En az bir yolcu gerekli")
    if len(set(ids)) != len(ids) or any(not i for i in ids):
        hata("yolcular", "Yolcu kimlikleri boş olamaz ve tekil olmalı")
    for i, y in enumerate(d.get("yolcular") or []):
        if not (y.get("ad") or "").strip():
            hata(f"yolcular.{i}.ad", "Yolcu adı boş")
        if not y.get("uyruklar"):
            hata(f"yolcular.{i}.uyruklar", f"{y.get('ad') or i}: en az bir uyruk seçilmeli")
        for k, iz in enumerate(y.get("izinler") or []):
            if not re.fullmatch(r"[A-Z]{2}", str(iz.get("ulke") or "")):
                hata(f"yolcular.{i}.izinler.{k}.ulke", "İzin ülkesi 2 harfli ISO kodu olmalı (ör. JP)")
            if iz.get("durum") not in ("gercek", "varsayim"):
                hata(f"yolcular.{i}.izinler.{k}.durum", "İzin durumu 'gercek' ya da 'varsayim' olmalı")
            try:
                _tarih(iz.get("bitis"))
            except ValueError:
                hata(f"yolcular.{i}.izinler.{k}.bitis", "Bitiş tarihi YYYY-AA-GG olmalı")

    dur = d.get("duraklar") or []
    if len(dur) < 2:
        hata("duraklar", "En az iki şehir gerekli (nereden → nereye)")
    bilinen = {y["sehir"] for y in yerler()}
    onceki: date | None = None
    for i, x in enumerate(dur):
        if x.get("sehir") not in bilinen:
            hata(f"duraklar.{i}.sehir", f"Şehir tanınmıyor: {x.get('sehir')!r} (listeden seç)")
        try:
            t = _tarih(x.get("tarih"))
        except ValueError:
            hata(f"duraklar.{i}.tarih", "Tarih YYYY-AA-GG olmalı")
            continue
        if i == 0 and t is None:
            hata("duraklar.0.tarih", "Yolculuk başlangıç tarihi gerekli")
        if not 0 <= int(x.get("esneklik") or 0) <= 3:
            hata(f"duraklar.{i}.esneklik", "Esneklik 0–3 gün olmalı")
        if t and onceki and t < onceki:
            hata(f"duraklar.{i}.tarih", "Tarihler ileriye doğru sıralanmalı")
        if t:
            onceki = t
        if i > 0 and dur[i - 1].get("sehir") == x.get("sehir"):
            hata(f"duraklar.{i}.sehir", "Art arda aynı şehir olamaz")
    bac = d.get("bacaklar") or []
    if dur and len(bac) != len(dur) - 1:
        hata("bacaklar", f"Bacak sayısı {len(bac)}, şehir sayısının bir eksiği ({max(len(dur) - 1, 0)}) olmalı")
    for i, b in enumerate(bac):
        if not b.get("yolcular"):
            hata(f"bacaklar.{i}.yolcular", "Bu bacakta en az bir yolcu olmalı")
        if any(y not in ids for y in b.get("yolcular") or []):
            hata(f"bacaklar.{i}.yolcular", "Bacakta tanımsız yolcu var")
        if not b.get("araclar") or any(a not in ARACLAR for a in b["araclar"]):
            hata(f"bacaklar.{i}.araclar", "Araç seçimi: ucak / yht / otobus")
    for i, t in enumerate(d.get("yan_turlar") or []):
        if t.get("ana_sehir") not in bilinen or t.get("hedef_sehir") not in bilinen:
            hata(f"yan_turlar.{i}", "Yan tur şehirleri listeden seçilmeli")
        if t.get("ana_sehir") not in {x.get("sehir") for x in dur}:
            hata(f"yan_turlar.{i}.ana_sehir", "Yan tur ana rotadaki bir şehirden başlamalı")
        if not t.get("yolcular") or any(y not in ids for y in t["yolcular"]):
            hata(f"yan_turlar.{i}.yolcular", "Yan tur yolcuları tanımlı olmalı")
        a, b = _tarih(t.get("en_erken_gidis")), _tarih(t.get("en_gec_donus"))
        if not a or not b or b <= a:
            hata(f"yan_turlar.{i}", "Yan tur penceresi: en erken gidiş < en geç dönüş")
        elif (b - a).days < int(t.get("kalis_gece") or 0):
            hata(f"yan_turlar.{i}.kalis_gece", "Kalış süresi pencereye sığmıyor")
    return h


def _yolcu_denetimi(y: Yolcu, varis_sehir: str, gun: date | None, kurallar: list[dict]) -> dict:
    """Varış ülkesine giriş ön denetimi (tarama öncesi; aktarmalar uçuş seçeneklerinde ayrıca denetlenir)."""
    nk = sehir_noktalari(varis_sehir)
    ulke = nk[0].get("ulke") if nk and "ulke" in nk[0] else next((v["ulke"] for v in yerler() if v["sehir"] == varis_sehir), None)
    if not ulke or ulke in y.uyruklar:
        return {"yolcu": y.id, "durum": "ok", "mesaj": ""}
    iz, dolmus = gecerli_izin(y, ulke, gun or date.today())
    k = kural(y, ulke, kurallar)["durum"]
    if iz and iz.get("durum") == "varsayim":
        return {"yolcu": y.id, "durum": "uyari", "mesaj": f"{y.ad}: {ulke_adi(ulke)} {iz.get('tur', 'izin')} varsayım (henüz alınmadı)"}
    if iz:
        return {"yolcu": y.id, "durum": "ok", "mesaj": f"{y.ad}: {ulke_adi(ulke)} {iz.get('tur', 'izin')} var"}
    if k in ("yasak", "vize_gerekli"):
        ek = f" (izin {dolmus['bitis']} tarihinde bitiyor)" if dolmus else ""
        return {"yolcu": y.id, "durum": "engel",
                "mesaj": f"{y.ad}: {ulke_adi(ulke)} girişi için vize/izin yok{ek} — bu bacağın tüm seçenekleri elenir"}
    if k == "serbest":
        return {"yolcu": y.id, "durum": "ok", "mesaj": f"{y.ad}: {ulke_adi(ulke)} vizesiz"}
    return {"yolcu": y.id, "durum": "uyari", "mesaj": f"{y.ad}: {ulke_adi(ulke)} giriş kuralı tabloda yok — kontrol et"}


def onizle(d: dict) -> dict:
    """Kaydetmeden önce: hatalar + bacak bacak hesaplanan tarih pencereleri, noktalar, araçlar, yolcu giriş denetimi."""
    hatalar = dogrula(d)
    sonuc: dict[str, Any] = {"hatalar": hatalar, "bacaklar": [], "yan_turlar": [], "uyarilar": []}
    if hatalar:
        return sonuc
    plan = plan_nesnesi(d)
    kurallar = kurallar_yukle()
    for b in bacaklar(plan):
        sonuc["bacaklar"].append({
            "id": b.id, "sira": b.sira, "baslik": f"{b.kalkis_sehir} → {b.varis_sehir}",
            "kalkis_sehir": b.kalkis_sehir, "varis_sehir": b.varis_sehir,
            "en_erken": b.en_erken.isoformat() if b.en_erken else None, "en_gec": b.en_gec.isoformat() if b.en_gec else None,
            "yolcular": [y.id for y in b.yolcular], "araclar": b.araclar,
            "olasi_araclar": varsayilan_araclar(b.kalkis_sehir, b.varis_sehir),
            "kalkis_noktalari": sehir_noktalari(b.kalkis_sehir), "varis_noktalari": sehir_noktalari(b.varis_sehir),
            "gecis_sonrasi": b.gecis_sonrasi,
            "yolcu_denetimi": [_yolcu_denetimi(y, b.varis_sehir, b.en_gec, kurallar) for y in b.yolcular]})
    yol = {y.id: y for y in plan.yolcular}
    for yt in plan.yan_turlar:
        ys = [yol[i] for i in yt.yolcu_idler]
        sonuc["yan_turlar"].append({
            "baslik": f"{yt.ana_sehir} ↔ {yt.hedef_sehir}", "gidis_id": yt.gidis_bacak_id, "donus_id": yt.donus_bacak_id,
            "pencere": [yt.en_erken_gidis.isoformat(), yt.en_gec_donus.isoformat()], "kalis_gece": yt.kalis_gece,
            "yolcu_denetimi": [_yolcu_denetimi(y, yt.hedef_sehir, yt.en_erken_gidis, kurallar) for y in ys]})
    for b in sonuc["bacaklar"] + sonuc["yan_turlar"]:
        sonuc["uyarilar"] += [f"{b['baslik']}: {x['mesaj']}" for x in b["yolcu_denetimi"] if x["durum"] != "ok"]
    if any(set(x["araclar"]) - {"ucak"} for x in sonuc["bacaklar"]):
        sonuc["uyarilar"].append("Hızlı tren ve otobüs taraması v0.0.2'de (A9); şimdilik yalnız uçak seçenekleri aranır")
    return sonuc


# ------------------------------------------------------------------ kalıcı kayıt
def yukle(yol: Path | None = None) -> dict:
    """Kayıtlı plan; yoksa eski evren-asistan planından ilk plan üretilir (kaydedilmez)."""
    yol = yol or plan_yolu()
    if yol.exists():
        return json.loads(yol.read_text(encoding="utf-8"))
    return plan_sozluk(ice_aktar_eski_plan(), "Tokyo 2026-27 (eski plandan)")


def kaydet(d: dict, yol: Path | None = None) -> dict:
    """Doğrular, eksik bacak id'lerini doldurur, önceki sürümü .yedek olarak saklar, yazar. Hatalıysa ValueError."""
    hatalar = dogrula(d)
    if hatalar:
        raise ValueError(hatalar)
    plan = plan_nesnesi(d)
    d = json.loads(json.dumps(d))
    for i, b in enumerate(d["bacaklar"]):
        b["id"] = plan.bacak_idleri[i]
    for t, yt in zip(d.get("yan_turlar") or [], plan.yan_turlar):
        t["gidis_id"], t["donus_id"] = yt.gidis_bacak_id, yt.donus_bacak_id
    d["surum"], d["guncelleme"] = SURUM, datetime.now().astimezone().isoformat(timespec="seconds")
    yol = yol or plan_yolu()
    yol.parent.mkdir(parents=True, exist_ok=True)
    if yol.exists():
        shutil.copy2(yol, yol.with_suffix(".json.yedek"))
    gecici = yol.with_suffix(".json.tmp")
    gecici.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    gecici.replace(yol)
    return d


# ------------------------------------------------------------------ eski tarayıcıya aktarım (A6'ya kadar köprü)
def _eski_kod(sehir: str) -> str | None:
    if sehir in METRO:
        return METRO[sehir]
    hl = [n for n in sehir_noktalari(sehir) if n["tur"] == "havalimani"]
    return hl[0]["kod"] if hl else None


def eski_plana_aktar(d: dict, eski_yol: Path = ESKI_PLAN) -> dict:
    """Uçak bacaklarını evren-asistan ucus/plan.json'a yazar (eski tarama döngüsü yeni rotayı tarasın).
    Diğer anahtarlar (yasak_aktarma, guvenli_aktarma…) korunur; önceki dosya .yedek-kazi olarak saklanır."""
    plan = plan_nesnesi(d)
    kurallar = kurallar_yukle()
    havalimani = veri("havalimani_ulke.json")

    def yasak_ek(yolcular: list[Yolcu]) -> list[str]:
        ulkeler = {k["ulke"] for y in yolcular for k in kurallar if k["uyruk"] in y.uyruklar and k["durum"] in ("yasak", "vize_gerekli")
                   and not any(i.get("ulke") == k["ulke"] for i in y.izinler)}
        return sorted(kod for kod, u in havalimani.items() if u in ulkeler)

    eski = json.loads(eski_yol.read_text(encoding="utf-8")) if eski_yol.exists() else {}
    yeni, atlanan = [], []
    for b in bacaklar(plan):
        k, v = _eski_kod(b.kalkis_sehir), _eski_kod(b.varis_sehir)
        if "ucak" not in b.araclar or not k or not v or not b.en_erken:
            atlanan.append(f"{b.kalkis_sehir} → {b.varis_sehir}")
            continue
        orta = b.en_erken + (b.en_gec - b.en_erken) / 2
        yeni.append({"id": b.id, "ad": f"{b.kalkis_sehir} → {b.varis_sehir}", "kalkis": k, "varis": v, "tarih": orta.isoformat(),
                     "esnek_gun": (b.en_gec - b.en_erken).days // 2, "yolcu": len(b.yolcular), "yasak_ek": yasak_ek(b.yolcular)})
    yol = {y.id: y for y in plan.yolcular}
    for yt in plan.yan_turlar:
        ys = [yol[i] for i in yt.yolcu_idler]
        k, v = _eski_kod(yt.ana_sehir), _eski_kod(yt.hedef_sehir)
        gun = (yt.en_gec_donus - yt.en_erken_gidis).days
        for bid, a, b, tarih in ((yt.gidis_bacak_id, k, v, yt.en_erken_gidis), (yt.donus_bacak_id, v, k, yt.en_erken_gidis)):
            yeni.append({"id": bid, "ad": f"{a} → {b} (yan tur)", "kalkis": a, "varis": b,
                         "tarih": (tarih + (yt.en_gec_donus - tarih) / 2).isoformat(), "esnek_gun": (gun + 1) // 2,
                         "yolcu": len(ys), "yasak_ek": yasak_ek(ys), "rotasyon": True})
    if eski_yol.exists():
        shutil.copy2(eski_yol, eski_yol.with_suffix(".json.yedek-kazi"))
    eski["bacaklar"] = yeni
    eski["not_"] = f"kazi-asistan arayüzünden yazıldı {datetime.now():%d.%m.%Y %H:%M}"
    eski_yol.write_text(json.dumps(eski, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"yazilan": [b["id"] for b in yeni], "atlanan": atlanan}
