"""Telegram'a düzenli fiyat/uçuş özeti (kullanıcı 14.11: "eski botu kapat; bu sistem fiyatları kazıyıp Telegram'dan bana atsın").

- Sıklık: telegram ayarında `rapor_aralik_dk` (0 = kapalı; 30/60/120/180/360), varsayılan 60.
- Sessiz saatler: `sessiz_saatler` ["01:00", "07:00"] arasında özet gönderilmez (anlık ALARM mesajları yine gider).
- İçerik: takipteki her ürünün en iyi fiyatı (site/satıcı, teyit), önceki rapora göre değişim, alarm fiyatına uzaklık; seçili ürünler toplamı;
  her uçuş bacağının ve yan turun en ucuzu. Yıldız/akıllı maliyet YOK.
"""
from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone

from .telegram import _esc, _tl, ayar_oku, gonder

TR = timezone(timedelta(hours=3))
ARALIK_SECENEKLERI = (0, 30, 60, 120, 180, 360)
VARSAYILAN_ARALIK = 60
VARSAYILAN_SESSIZ = ("01:00", "07:00")


def _kur(v) -> None:
    v.baglanti.execute("CREATE TABLE IF NOT EXISTS rapor_durum (k INTEGER PRIMARY KEY CHECK (k = 1), son_gonderim TEXT, "
                       "son_fiyatlar TEXT NOT NULL DEFAULT '{}')")
    v.baglanti.commit()


def rapor_ayari(a: dict | None = None) -> dict:
    a = a or ayar_oku()
    aralik = a.get("rapor_aralik_dk", VARSAYILAN_ARALIK)
    sessiz = a.get("sessiz_saatler") or list(VARSAYILAN_SESSIZ)
    return {"rapor_aralik_dk": aralik if aralik in ARALIK_SECENEKLERI else VARSAYILAN_ARALIK,
            "sessiz_saatler": sessiz, "aralik_secenekleri": list(ARALIK_SECENEKLERI)}


def sessiz_mi(simdi: datetime, sessiz: list[str]) -> bool:
    try:
        b, s = (time.fromisoformat(x) for x in sessiz)
    except (ValueError, TypeError):
        return False
    t = simdi.timetz().replace(tzinfo=None)
    return b <= t < s if b <= s else (t >= b or t < s)


def _degisim(simdi: float, once: float | None) -> str:
    if once is None or abs(simdi - once) < 1:
        return ""
    return f" ↓{_tl(once - simdi)}" if simdi < once else f" ↑{_tl(simdi - once)}"


# ---------------------------------------------------------------- biçim yardımcıları (15.11 kullanıcı biçimi)
KATEGORI_SIMGE = {"gpu": "🎮", "cpu": "🧠", "ram": "💾", "anakart": "🧩", "ssd": "💽", "psu": "🔌", "kasa": "🗄",
                  "sogutucu": "❄️"}
KATEGORI_AD = {"gpu": "Ekran kartı", "cpu": "İşlemci", "ram": "Bellek (RAM)", "anakart": "Anakart", "ssd": "SSD",
               "psu": "Güç kaynağı", "kasa": "Kasa", "sogutucu": "Soğutucu"}
# ürün adının başındaki kategori sözcüğü atılır → "Anakart Gigabyte X870…" → "Gigabyte X870…"
_ON_EKLER = ("Ekran kartı", "İşlemci", "Bellek", "RAM", "Anakart", "SSD", "Güç kaynağı", "Kasa", "Sıvı soğutucu",
             "Soğutucu")
GUNLER = ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")
AYLAR = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")
ARAC_SIMGE = {"otobus": "🚌", "tren": "🚆", "ucak": "✈️"}
PARCA_SINIRI = 3800                     # Telegram 4096; HTML etiketi ortadan bölünmesin diye blok sınırında böl


def marka_model(ad: str) -> str:
    for on in _ON_EKLER:
        if ad.lower().startswith(on.lower() + " "):
            return ad[len(on) + 1:].strip()
    return ad


def _kisalt(m: str, n: int = 90) -> str:
    m = " ".join(str(m or "").split())
    return m if len(m) <= n else m[: n - 1].rstrip() + "…"


def _dt(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def _gun(d: datetime) -> str:
    return f"{d.day} {AYLAR[d.month - 1]} {GUNLER[d.weekday()]}"


def _sure(dk: int | None) -> str:
    if not dk:
        return ""
    return f"{dk // 60} sa {dk % 60:02d} dk" if dk >= 60 else f"{dk} dk"


def _pencere(b: dict) -> str:
    e, g = _dt(b.get("en_erken") or ""), _dt(b.get("en_gec") or "")
    if not (e and g):
        return ""
    return f"{e.day}–{g.day} {AYLAR[g.month - 1]}" if (e.month, e.year) == (g.month, g.year) else \
        f"{e.day} {AYLAR[e.month - 1]} – {g.day} {AYLAR[g.month - 1]}"


def _parcala(bloklar: list[str], sinir: int = PARCA_SINIRI) -> list[str]:
    parcalar: list[str] = []
    cari = ""
    for b in bloklar:
        if cari and len(cari) + 2 + len(b) > sinir:
            parcalar.append(cari)
            cari = b
        else:
            cari = f"{cari}\n\n{b}" if cari else b
    if cari:
        parcalar.append(cari)
    return parcalar


def _urun_blogu(u: dict, onceki: dict[str, float]) -> tuple[str, float | None]:
    """Marka/model (kalın) · Fiyat (kalın) + vade farksız taksit (kalın) + satıcı linki · ilan adı."""
    satir = [f"<b>{_esc(marka_model(u['ad']))}</b>"]
    t = u.get("en_iyi")
    if not t or t.get("sepet_fiyati") is None:
        satir.append("Fiyat: — (henüz teklif yok)")
        return "\n".join(satir), None
    f = t["sepet_fiyati"]
    fiyat = f"<b>Fiyat: {_tl(f)}</b>{_degisim(f, onceki.get(u['id']))}"
    taksit = (t.get("taksit") or {}).get("pesin_fiyatina")
    if taksit:
        fiyat += f" · <b>{taksit} ay vade farksız</b>"
    kim = " · ".join(x for x in (t.get("site"), t.get("satici")) if x) or "satıcı"
    fiyat += f' · <a href="{_esc(t["url"])}">{_esc(kim)}</a>' if t.get("url") else f" · {_esc(kim)}"
    satir.append(fiyat)
    if t.get("baslik"):
        satir.append(f"📦 <i>{_esc(_kisalt(t['baslik']))}</i>")
    # 15.11 kullanıcı: teyit ve alarm bilgisi raporda yok (alarm ayrı anlık mesajla gider)
    return "\n".join(satir), f


def _secenek_satirlari(s: dict, yolcu: int) -> list[str]:
    """Bir uçuş/otobüs seçeneğinin okunur satırları."""
    k, v_ = _dt(s.get("kalkis") or ""), _dt(s.get("varis") or "")
    simge = ARAC_SIMGE.get(s.get("arac") or "ucak", "✈️")
    fiyat = f"💰 <b>{_tl(s['kisi_basi'])}</b> kişi başı"
    if yolcu > 1 and s.get("toplam"):
        fiyat += f" · {yolcu} kişi {_tl(s['toplam'])}"
    satirlar = [fiyat]
    if k:
        saat = "saat ?" if s.get("saat_bilinmiyor") else f"{k:%H:%M}"
        var = ""
        if v_ and not s.get("saat_bilinmiyor"):
            gun_farki = (v_.date() - k.date()).days
            var = f" → {v_:%H:%M}" + (f" (+{gun_farki})" if gun_farki > 0 else "")
        satirlar.append(f"📅 {_gun(k)} · {saat}{var}")
    aktarma = s.get("aktarma_sayisi") or 0
    rota = " → ".join([s.get("kalkis_nokta") or "?", *(s.get("aktarmalar") or []), s.get("varis_nokta") or "?"])
    akt = "direkt" if not aktarma else f"{aktarma} aktarma"
    sure = "" if s.get("saat_bilinmiyor") else _sure(s.get("sure_dk"))     # saat yoksa süre de varsayım (3 sa)
    satirlar.append(f"{simge} {_esc(rota)} · {akt}" + (f" · {sure}" if sure else ""))
    if s.get("firma"):
        satirlar.append(f"🏢 {_esc(s['firma'])}")
    uyarilar = [u for u in dict.fromkeys(s.get("uyarilar") or []) if "saati kaynakta yok" not in u]   # "saat ?" zaten söylüyor
    for u in uyarilar[:2]:
        satirlar.append(f"<i>{_esc(_kisalt(u, 110))}</i>")
    if s.get("link"):
        satirlar.append(f'🔗 <a href="{_esc(s["link"])}">{_esc(s.get("kaynak") or "bilete git")}\'ta aç</a>')
    return satirlar


def rapor_metni(v, onceki: dict[str, float], simdi: datetime, seyahat: dict | None = None) -> tuple[list[str], dict[str, float]]:
    """(mesaj parçaları, yeni fiyat anlık görüntüsü). Biçim (15.11 kullanıcı):
    Fiyat Raporu (tarih) → kategori başlığı → marka/model (kalın) → Fiyat (kalın) · vade farksız taksit (kalın) · satıcı linki."""
    from ..pano import pano
    p = pano(v)
    yeni: dict[str, float] = {}
    bloklar = [f"📊 <b>Fiyat Raporu ({simdi:%d.%m.%Y %H:%M})</b>"]
    gruplar: dict[str, list[dict]] = {}
    for u in p["urunler"]:
        gruplar.setdefault(u.get("kategori") or "diger", []).append(u)
    sira = [k for k in KATEGORI_AD if k in gruplar] + [k for k in gruplar if k not in KATEGORI_AD]
    for kat in sira:
        baslik = f"{KATEGORI_SIMGE.get(kat, '•')} <b>{_esc(KATEGORI_AD.get(kat, kat.capitalize()))}</b>"
        urun_bloklari = []
        for u in gruplar[kat]:
            metin, f = _urun_blogu(u, onceki)
            if f is not None:
                yeni[u["id"]] = f
            urun_bloklari.append(metin)
        bloklar.append(baslik + "\n" + "\n\n".join(urun_bloklari))
    st = p.get("sistem_toplami") or {}
    if st.get("adet"):
        yeni["_toplam"] = st["tutar"]
        bloklar.append(f"Σ <b>Seçili toplam ({st['adet']} ürün): {_tl(st['tutar'])}</b>{_degisim(st['tutar'], onceki.get('_toplam'))}")
    parcalar = _parcala(bloklar)

    if seyahat and seyahat.get("bacaklar"):
        ub = ["✈️ <b>UÇUŞLAR</b> — her bacağın en ucuzu"]
        ana_toplam = 0.0
        eksik = False
        for b in seyahat["bacaklar"]:
            yolcu = len(b.get("yolcular") or []) or 1
            pen = _pencere(b)
            bas = f"🛫 <b>{_esc(b['baslik'])}</b>" + (f" · {pen}" if pen else "") + (f" · {yolcu} kişi" if yolcu > 1 else "")
            s = (b.get("secenekler") or [None])[0]
            if not s:
                ub.append(bas + "\nseçenek yok")
                eksik = True
                continue
            anahtar = f"ucus:{b['id']}"
            yeni[anahtar] = s["kisi_basi"]
            sat = _secenek_satirlari(s, yolcu)
            sat[0] += _degisim(s["kisi_basi"], onceki.get(anahtar))
            ana_toplam += float(s.get("toplam") or s["kisi_basi"] * yolcu)
            ub.append(bas + "\n" + "\n".join(sat))
        if ana_toplam:
            ub.append(f"Σ <b>Yolculuk toplamı: {_tl(ana_toplam)}</b>" + (" (eksik bacak var)" if eksik else ""))
        for yt in seyahat.get("yan_turlar") or []:
            c = (yt.get("ciftler") or [None])[0]
            if not c:
                continue
            sat = [f"🔁 <b>{_esc(yt['baslik'])}</b>", f"💰 <b>{_tl(c['kisi_basi_toplam'])}</b> gidiş-dönüş"]
            for yon, s in (("➡ Gidiş", c.get("gidis")), ("⬅ Dönüş", c.get("donus"))):
                if not s:
                    continue
                k = _dt(s.get("kalkis") or "")
                akt = "direkt" if not s.get("aktarma_sayisi") else f"{s['aktarma_sayisi']} aktarma"
                sat.append(f"{yon}: {_gun(k) + ' ' + format(k, '%H:%M') if k else '?'} · "
                           f"{_esc(s.get('kalkis_nokta'))} → {_esc(s.get('varis_nokta'))} · {_esc(s.get('firma'))} · {akt}")
            ub.append("\n".join(sat))
        parcalar += _parcala(ub)
    return parcalar, yeni


def rapor_gonder_gerekirse(v, simdi: datetime | None = None, gonder_fn=None, zorla: bool = False, seyahat_fn=None) -> bool:
    """Zamanı geldiyse (ve sessiz saatte değilse) özeti gönderir. `zorla` = şimdi gönder (arayüz/test)."""
    simdi = simdi or datetime.now(TR)
    gonder_fn = gonder_fn or gonder
    a = ayar_oku()
    r = rapor_ayari(a)
    if not zorla and (r["rapor_aralik_dk"] == 0 or sessiz_mi(simdi, r["sessiz_saatler"])):
        return False
    _kur(v)
    satir = v.baglanti.execute("SELECT son_gonderim, son_fiyatlar FROM rapor_durum WHERE k = 1").fetchone()
    if satir and satir[0] and not zorla:
        if datetime.fromisoformat(satir[0]) + timedelta(minutes=r["rapor_aralik_dk"]) > simdi:
            return False
    onceki = json.loads(satir[1]) if satir else {}
    seyahat = None
    try:
        if seyahat_fn is not None:
            seyahat = seyahat_fn()
        else:
            from .. import eski
            from ..seyahat import plan_depo
            seyahat = eski.seyahat(plan_sozlugu=plan_depo.yukle(), v=v)
    except Exception:                                   # uçuş verisi yoksa yalnız ürünler
        seyahat = None
    parcalar, yeni = rapor_metni(v, onceki, simdi, seyahat)
    if not all(gonder_fn(p, a) for p in parcalar):
        return False
    v.baglanti.execute("INSERT INTO rapor_durum (k, son_gonderim, son_fiyatlar) VALUES (1, ?, ?) "
                       "ON CONFLICT(k) DO UPDATE SET son_gonderim=excluded.son_gonderim, son_fiyatlar=excluded.son_fiyatlar",
                       (simdi.isoformat(timespec="seconds"), json.dumps(yeni)))
    v.baglanti.commit()
    return True
