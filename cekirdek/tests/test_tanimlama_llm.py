"""LLM sağlayıcı: sahte sağlayıcı + httpx MockTransport. Testlerde AĞ YOK."""
import json
import logging

import httpx

from kazi.tanimlama import KapaliSaglayici, Saglayici, EvrenSaglayici, ayristir, tanimla
from kazi.tanimlama.llm import SEMA, birlestir, dogrula, env_oku, yanit_ayikla

ANAHTAR = "sk-test-GIZLI-1234567890"


class Sahte:
    """Çağrıları sayan sahte sağlayıcı."""

    def __init__(self, yanit=None, hata=None):
        self.yanit, self.hata, self.cagrilar = yanit, hata, []

    def yapilandir(self, metin, ipucu):
        self.cagrilar.append((metin, ipucu))
        if self.hata:
            raise self.hata
        return self.yanit


def _env(tmp_path, base="http://evren.test", key=ANAHTAR):
    p = tmp_path / "env"
    p.write_text(f"# yorum\nEVREN_BASE_URL={base}\nexport EVREN_API_KEY=\"{key}\"\n", encoding="utf-8")
    return p


def _istemci(isleyici):
    return httpx.Client(transport=httpx.MockTransport(isleyici))


def _yanit(icerik, kod=200):
    return httpx.Response(kod, json={"choices": [{"message": {"content": icerik}}]})


# ---------------------------------------------------------------- protokol
def test_saglayici_protokolu():
    assert isinstance(KapaliSaglayici(), Saglayici) and isinstance(Sahte(), Saglayici)
    assert isinstance(EvrenSaglayici(), Saglayici)
    assert KapaliSaglayici().yapilandir("x", ayristir("x")) is None


# ---------------------------------------------------------------- güven eşiği
def test_llm_yalniz_dusuk_guvende_cagrilir():
    s = Sahte(yanit={"kategori": "psu"})
    q, _ = tanimla("rtx 5080 en ucuz", "urun", None, s)          # güven 0.75
    assert s.cagrilar == [] and q.kategori == "gpu"
    q, _ = tanimla("1000w gold atx 3.1 psu", "urun", None, s)    # 0.7
    assert s.cagrilar == []


def test_llm_dusuk_guvende_cagrilir_ve_birlesir():
    s = Sahte(yanit={"kategori": "psu", "marka": "Corsair", "model": "RM1000x", "ozellikler": {"watt": 1000}})
    q, _ = tanimla("Corsair RM1000x", "urun", None, s)           # kategori yok → 0.45
    assert len(s.cagrilar) == 1
    assert s.cagrilar[0][1].guven < 0.5                           # ipucu = kural sonucu
    assert q.kategori == "psu" and q.ozellikler["watt"] == 1000 and q.guven >= 0.5
    assert q.marka == "Corsair" and q.model == "RM1000X"          # kuralın bulduğu ezilmedi


def test_kural_sonucu_ezilmez():
    q = ayristir("Corsair RM1000x")
    yeni = birlestir(q, {"marka": "Asus", "model": "BAŞKA", "haric": ["laptop"], "butce_ust": 5000.0})
    assert yeni.marka == "Corsair" and yeni.model == "RM1000X"
    assert yeni.haric == ["laptop"] and yeni.butce_ust == 5000.0
    assert q.haric == [] and q.butce_ust is None                  # özgün nesne değişmedi


def test_llm_hatasi_tanimlamayi_dusurmez():
    s = Sahte(hata=RuntimeError("patladı"))
    q, adaylar = tanimla("garip bir istek", "urun", None, s)
    assert len(s.cagrilar) == 1 and adaylar == [] and q.kategori is None


def test_kapali_saglayici_varsayilan():
    q, a = tanimla("garip bir istek")
    assert q.guven < 0.5 and a == []


def test_birlestir_none_ve_bos():
    q = ayristir("kırmızı çorap")
    assert birlestir(q, None) == q and birlestir(q, {}) == q


# ---------------------------------------------------------------- EVREN
def test_evren_istek_biçimi_ve_basari(tmp_path):
    gorulen = {}

    def isle(req: httpx.Request):
        gorulen["url"] = str(req.url)
        gorulen["auth"] = req.headers["authorization"]
        gorulen["govde"] = json.loads(req.content)
        return _yanit(json.dumps({
            "kategori": "psu", "marka": "Corsair", "model": "RM1000x",
            "ozellikler": [{"ad": "watt", "deger": 1000}, {"ad": "80PLUS", "deger": "Gold"}],
            "haric": ["Laptop"], "butce_alt": None, "butce_ust": 9000}))

    e = EvrenSaglayici(env_yolu=_env(tmp_path), istemci=_istemci(isle))
    sonuc = e.yapilandir("Corsair RM1000x", ayristir("Corsair RM1000x"))
    assert gorulen["url"] == "http://evren.test/v1/chat/completions"
    assert gorulen["auth"] == "Bearer " + ANAHTAR
    g = gorulen["govde"]
    assert g["model"] == "deepseek-v4.1-flash" and g["reasoning_effort"] == "none"
    assert g["response_format"]["type"] == "json_schema" and g["response_format"]["json_schema"]["schema"] == SEMA
    assert g["response_format"]["json_schema"]["strict"] is True
    assert "Corsair RM1000x" in g["messages"][1]["content"]
    assert sonuc == {"kategori": "psu", "marka": "Corsair", "model": "RM1000x",
                     "ozellikler": {"watt": 1000, "80plus": "Gold"}, "haric": ["laptop"], "butce_ust": 9000.0}


def test_evren_hata_durumlari_none(tmp_path):
    q = ayristir("x")
    env = _env(tmp_path)
    assert EvrenSaglayici(env, istemci=_istemci(lambda r: _yanit("{}", 500))).yapilandir("x", q) is None
    assert EvrenSaglayici(env, istemci=_istemci(lambda r: _yanit("json değil"))).yapilandir("x", q) is None
    assert EvrenSaglayici(env, istemci=_istemci(lambda r: _yanit(""))).yapilandir("x", q) is None
    assert EvrenSaglayici(env, istemci=_istemci(lambda r: httpx.Response(200, json={"x": 1}))).yapilandir("x", q) is None

    def zaman_asimi(req):
        raise httpx.ReadTimeout("yavaş", request=req)

    assert EvrenSaglayici(env, istemci=_istemci(zaman_asimi)).yapilandir("x", q) is None


def test_evren_env_yok_veya_eksik(tmp_path):
    q = ayristir("x")

    def olmamali(req):
        raise AssertionError("ağ çağrısı yapılmamalı")

    assert EvrenSaglayici(tmp_path / "yok", istemci=_istemci(olmamali)).yapilandir("x", q) is None
    p = tmp_path / "env"
    p.write_text("EVREN_BASE_URL=http://x\n", encoding="utf-8")                 # anahtar yok
    assert EvrenSaglayici(p, istemci=_istemci(olmamali)).yapilandir("x", q) is None


def test_evren_zaman_asimi_20sn(tmp_path):
    e = EvrenSaglayici(_env(tmp_path))
    assert e.zaman_asimi == 20 and e.model == "deepseek-v4.1-flash"


def test_anahtar_loglanmaz(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    def hata(req):
        raise httpx.ConnectError(f"bağlanamadı Authorization: Bearer {ANAHTAR}", request=req)

    e = EvrenSaglayici(_env(tmp_path), istemci=_istemci(hata))
    assert e.yapilandir("x", ayristir("x")) is None
    EvrenSaglayici(_env(tmp_path), istemci=_istemci(lambda r: _yanit("{}", 401))).yapilandir("x", ayristir("x"))
    tum = caplog.text + repr(e) + str(e)
    assert ANAHTAR not in tum and "GIZLI" not in tum


def test_env_oku(tmp_path):
    p = tmp_path / "e"
    p.write_text("A=1\n#B=2\nexport C='3'\n\nD = \"4\"\nbozuk\n", encoding="utf-8")
    assert env_oku(p) == {"A": "1", "C": "3", "D": "4"}
    assert env_oku(tmp_path / "yok") == {}


# ---------------------------------------------------------------- doğrulama
def test_yanit_ayikla_cit_ve_on_soz():
    assert yanit_ayikla('```json\n{"kategori": "ram", "marka": null}\n```') == {"kategori": "ram"}
    assert yanit_ayikla('Tabii: {"kategori": "gpu"} bitti') == {"kategori": "gpu"}
    assert yanit_ayikla("[1,2]") is None and yanit_ayikla(None) is None


def test_dogrula_gecersizleri_atar():
    d = dogrula({"kategori": "uzay_gemisi", "marka": "  ", "ozellikler": {"Watt": 1000, "x": [1], "y": None},
                 "haric": ["", 5, "Laptop"], "butce_ust": -5, "butce_alt": True, "ekstra": "x"})
    assert d == {"ozellikler": {"watt": 1000}, "haric": ["laptop"]}
    assert dogrula({"kategori": "uzay_gemisi"}) is None


def test_sema_katı():
    assert SEMA["additionalProperties"] is False
    assert set(SEMA["required"]) == set(SEMA["properties"])
    assert "gpu" in SEMA["properties"]["kategori"]["enum"]
