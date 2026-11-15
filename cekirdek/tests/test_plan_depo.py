"""Seyahat planı: biçim, doğrulama, ön izleme, kayıt, eski tarayıcıya aktarım, yer arama, API."""
import json

import pytest
from fastapi.testclient import TestClient

from kazi.api import uygulama_olustur
from kazi.ayar import Ayar
from kazi.seyahat import plan_depo as pd

ALI = {"id": "ali", "ad": "Ali", "uyruklar": ["TR"], "izinler": []}
AYSE = {"id": "ayse", "ad": "Ayşe", "uyruklar": ["XX"], "izinler": [
    {"ulke": "TR", "tur": "ikamet", "bitis": "2027-06-30", "durum": "gercek"},
    {"ulke": "JP", "tur": "vize", "bitis": None, "durum": "varsayim"}]}


def plan(**ek):
    d = {"surum": 1, "ad": "Deneme", "yolcular": json.loads(json.dumps([ALI, AYSE])),
         "duraklar": [{"sehir": "Ankara", "tarih": "2026-12-26", "esneklik": 1},
                      {"sehir": "İstanbul", "gecis_noktasi": True},
                      {"sehir": "Tokyo", "tarih": "2027-01-06", "esneklik": 2},
                      {"sehir": "İstanbul"}],
         "bacaklar": [{"yolcular": ["ali", "ayse"], "araclar": ["ucak", "yht", "otobus"]},
                      {"yolcular": ["ali", "ayse"], "araclar": ["ucak"]},
                      {"yolcular": ["ali", "ayse"], "araclar": ["ucak"]}],
         "yan_turlar": [{"ana_sehir": "Tokyo", "hedef_sehir": "Seul", "yolcular": ["ali"], "kalis_gece": 2,
                         "en_erken_gidis": "2026-12-28", "en_gec_donus": "2027-01-05"}]}
    d.update(ek)
    return d


def test_yer_ara_turkce_ingilizce_ve_kod():
    assert pd.yer_ara("seul")[0]["sehir"] == "Seul"
    assert pd.yer_ara("Seoul")[0]["sehir"] == "Seul"
    assert pd.yer_ara("ICN")[0]["sehir"] == "Seul"
    assert pd.yer_ara("istanbul")[0]["sehir"] == "İstanbul"
    assert pd.yer_ara("İZMİR")[0]["sehir"] == "İzmir"
    a = pd.yer_ara("almaty")
    assert a and a[0]["ulke"] == "KZ" and a[0]["noktalar"][0]["kod"] == "ALA"
    assert pd.yer_ara("") == []


def test_eski_plandan_ilk_plan_ve_gidis_donus():
    d = pd.plan_sozluk(pd.ice_aktar_eski_plan())
    assert [x["sehir"] for x in d["duraklar"]] == ["İstanbul", "Tokyo", "İstanbul"]
    assert [b["id"] for b in d["bacaklar"]] == ["ist_tyo", "tyo_ist"]
    assert "esik" not in d["bacaklar"][0]
    assert d["yan_turlar"][0]["yolcular"] == ["ali"] and d["yan_turlar"][0]["gidis_id"] == "tyo_sel"
    assert pd.dogrula(d) == []


def test_dogrulama_hatalari():
    alanlar = {h["alan"] for h in pd.dogrula(plan(duraklar=[{"sehir": "Atlantis", "tarih": "2026-12-01"}]))}
    assert "duraklar" in alanlar and "duraklar.0.sehir" in alanlar
    d = plan()
    d["duraklar"][2]["tarih"] = "2026-12-01"                    # geriye giden tarih
    d["bacaklar"][1]["yolcular"] = []
    d["yolcular"][1]["uyruklar"] = []
    alanlar = {h["alan"] for h in pd.dogrula(d)}
    assert {"duraklar.2.tarih", "bacaklar.1.yolcular", "yolcular.1.uyruklar"} <= alanlar


def test_onizleme_pencere_arac_ve_yolcu_denetimi():
    o = pd.onizle(plan())
    assert o["hatalar"] == []
    b0, b1, b2 = o["bacaklar"]
    assert b0["baslik"] == "Ankara → İstanbul" and set(b0["olasi_araclar"]) == {"ucak", "yht", "otobus"}
    assert (b0["en_erken"], b0["en_gec"]) == ("2026-12-25", "2026-12-27")
    assert b1["gecis_sonrasi"] is True and b1["en_erken"] == "2026-12-25"   # geçiş noktası: önceki tarihten
    es_tokyo = next(x for x in b1["yolcu_denetimi"] if x["yolcu"] == "ayse")
    assert es_tokyo["durum"] == "uyari" and "varsayım" in es_tokyo["mesaj"]
    assert b0["id"] == "ank_ist" and b1["id"] == "ist_tyo" and b2["id"] == "tyo_ist"
    assert any("v0.0.2" in u for u in o["uyarilar"])


def test_es_seul_yan_turuna_eklenirse_engel():
    d = plan()
    d["yan_turlar"][0]["yolcular"] = ["ali", "ayse"]
    yt = pd.onizle(d)["yan_turlar"][0]
    es = next(x for x in yt["yolcu_denetimi"] if x["yolcu"] == "ayse")
    assert es["durum"] == "engel" and "Kore girişi" in es["mesaj"]


def test_kaydet_yedek_ve_id(tmp_path):
    yol = tmp_path / "plan.json"
    k = pd.kaydet(plan(), yol)
    assert [b["id"] for b in k["bacaklar"]] == ["ank_ist", "ist_tyo", "tyo_ist"]
    assert k["yan_turlar"][0]["gidis_id"] == "tyo_sel" and k["yan_turlar"][0]["donus_id"] == "sel_tyo"
    pd.kaydet(k, yol)
    assert (tmp_path / "plan.json.yedek").exists() and pd.yukle(yol)["ad"] == "Deneme"
    with pytest.raises(ValueError):
        pd.kaydet(plan(yolcular=[]), yol)


def test_eski_plana_aktar(tmp_path):
    eski = tmp_path / "plan.json"
    eski.write_text(json.dumps({"bacaklar": [], "yasak_aktarma": ["DXB"]}), encoding="utf-8")
    s = pd.eski_plana_aktar(pd.kaydet(plan(), tmp_path / "k.json"), eski)
    yaz = json.loads(eski.read_text(encoding="utf-8"))
    assert yaz["yasak_aktarma"] == ["DXB"] and (tmp_path / "plan.json.yedek-kazi").exists()
    b = {x["id"]: x for x in yaz["bacaklar"]}
    assert b["ank_ist"]["kalkis"] == "ESB" and b["ank_ist"]["varis"] == "IST" and b["ank_ist"]["tarih"] == "2026-12-26"
    assert b["ist_tyo"]["kalkis"] == "IST" and b["ist_tyo"]["varis"] == "TYO" and b["ist_tyo"]["yolcu"] == 2
    assert "ICN" in b["ist_tyo"]["yasak_ek"] and "DXB" in b["ist_tyo"]["yasak_ek"]      # Ayşe: Kore/BAE kuralları
    assert b["tyo_sel"]["rotasyon"] is True and b["tyo_sel"]["yolcu"] == 1 and b["tyo_sel"]["yasak_ek"] == []
    assert s["atlanan"] == []


@pytest.fixture
def istemci(tmp_path):
    app = uygulama_olustur(Ayar(veri_dizini=tmp_path))
    app.state.plan_yolu = tmp_path / "plan.json"
    app.state.eski_plan_yolu = tmp_path / "eski_plan.json"
    with TestClient(app) as c:
        yield c


def test_api_plan_akisi(istemci, tmp_path):
    r = istemci.get("/seyahat/plan").json()
    assert r["kayitli"] is False and r["plan"]["duraklar"][0]["sehir"] == "İstanbul"
    assert istemci.post("/seyahat/plan/onizle", json=plan()).json()["hatalar"] == []
    bozuk = istemci.put("/seyahat/plan", json=plan(yolcular=[]))
    assert bozuk.status_code == 422 and bozuk.json()["ayrinti"]
    r = istemci.put("/seyahat/plan", json=plan()).json()
    assert [b["id"] for b in r["plan"]["bacaklar"]] == ["ank_ist", "ist_tyo", "tyo_ist"]
    assert "ank_ist" in r["eski_tarayici"]["yazilan"]
    assert istemci.get("/seyahat/plan").json()["kayitli"] is True
    assert istemci.get("/yerler", params={"q": "tok"}).json()[0]["sehir"] == "Tokyo"
    assert istemci.get("/ulkeler").json()["JP"] == "Japonya"


def test_seyahat_onbellek_suresi_dolunca_eskiyi_hemen_doner(istemci, monkeypatch):
    """15.11: hesap uzayınca arayüz 8 sn'de zaman aşımına düşüyordu → süre dolunca eski sonuç beklemeden döner."""
    import time as _t
    from kazi import api, eski
    sayac = {"n": 0}

    def yavas(plan_sozlugu, v):
        sayac["n"] += 1
        if sayac["n"] > 1:
            _t.sleep(1.5)
        return {"surum": sayac["n"]}
    monkeypatch.setattr(eski, "seyahat", yavas)
    assert istemci.get("/seyahat").json() == {"surum": 1}          # ilk istek bekler
    gercek = _t.time
    monkeypatch.setattr(api.time, "time", lambda: gercek() + 1000)    # önbellek süresi doldu
    t0 = gercek()
    assert istemci.get("/seyahat").json() == {"surum": 1}          # eski sonuç, beklemeden
    assert gercek() - t0 < 1.0
    for _ in range(40):                                              # arka plan hesabı bitince yenisi gelir
        if istemci.get("/seyahat").json() == {"surum": 2}:
            break
        _t.sleep(0.1)
    else:
        raise AssertionError("arka plan hesabı yenilemedi")
