"""Kazı Brave profili (15.11): kalıcı yer, açılış öncesi gizlilik tercihleri, çerez penceresinde 'Tümünü reddet'."""
import json

import pytest

from kazi.tarayici import profil as pf


def test_flatpakta_kalici_veri_klasoru(tmp_path):
    assert pf.profil_dizini(["flatpak", "run", "com.brave.Browser"], tmp_path) == pf.FLATPAK_PROFIL
    assert pf.profil_dizini(["/usr/bin/brave"], tmp_path) == tmp_path
    assert str(pf.FLATPAK_PROFIL).endswith(".var/app/com.brave.Browser/data/kazi_brave_profil")


def test_tohumlama_mevcut_icerigi_korur_ve_tercihleri_yazar(tmp_path):
    (tmp_path / "Default").mkdir()
    (tmp_path / "Local State").write_text(json.dumps({"brave": {"p3a": {"enabled": True}, "baska": 1}, "x": 2}))
    (tmp_path / "Default" / "Preferences").write_text(json.dumps(
        {"profile": {"default_content_setting_values": {"geolocation": 2, "popups": 1}}, "y": 3}))
    pf.profil_tohumla(tmp_path, dict(pf.VARSAYILAN))
    ls = json.loads((tmp_path / "Local State").read_text())
    pr = json.loads((tmp_path / "Default" / "Preferences").read_text())
    assert ls["brave"]["p3a"] == {"enabled": False, "notice_acknowledged": True}
    assert ls["brave"]["stats"]["reporting_enabled"] is False and ls["brave"]["baska"] == 1 and ls["x"] == 2
    assert ls["user_experience_metrics"]["reporting_enabled"] is False
    dcs = pr["profile"]["default_content_setting_values"]
    assert dcs == {"geolocation": 2, "popups": 2, "notifications": 2} and pr["y"] == 3


def test_ayar_kapaliysa_varsayilana_doner_ve_bos_profil(tmp_path):
    ayar = {**pf.VARSAYILAN, "bildirim_engelle": False, "popup_engelle": False, "analiz_kapali": False}
    pf.profil_tohumla(tmp_path, {**pf.VARSAYILAN})
    pf.profil_tohumla(tmp_path, ayar)
    pr = json.loads((tmp_path / "Default" / "Preferences").read_text())
    assert pr["profile"]["default_content_setting_values"] == {}
    assert pf.gizlilik_ayarlari({}) == pf.VARSAYILAN and pf.gizlilik_ayarlari({"cerez_reddet": False})["cerez_reddet"] is False


@pytest.fixture(scope="module")
def sayfa():
    sync = pytest.importorskip("playwright.sync_api")
    try:
        pw = sync.sync_playwright().start()
        tarayici = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"Chromium yok: {e}")
    pg = tarayici.new_page()
    yield pg
    tarayici.close()
    pw.stop()


def _kur(pg, govde):
    pg.set_content("<html><body>" + govde + "<div id=s></div></body></html>")
    pg.evaluate("document.querySelectorAll('button,a').forEach(b => b.addEventListener('click', e => {"
                "e.preventDefault(); document.getElementById('s').textContent = b.id; }))")


@pytest.mark.parametrize("govde,beklenen", [
    ("<button id=k>Tümünü Kabul Et</button><button id=r>Tümünü Reddet</button>", "r"),
    ("<button id=k>Kabul et</button><a id=r href=#> Sadece gerekli </a>", "r"),
    ("<button id=onetrust-reject-all-handler>Reject</button>", "onetrust-reject-all-handler"),
    ("<button id=k>Tümünü kabul et</button><button id=r style='display:none'>Reddet</button>", ""),
    ("<button id=k>Kabul et</button><a id=x href=#>Siparişi reddet ve iade et</a>", ""),
])
def test_cerez_reddi_yalniz_ret_dugmesine_basar(sayfa, govde, beklenen):
    _kur(sayfa, govde)
    basilan = pf.cerezleri_reddet(sayfa, bekle_ms=0)
    assert sayfa.inner_text("#s") == beklenen
    assert (basilan is not None) == bool(beklenen)


def test_tohumlama_oturum_geri_yuklemeyi_kapatir(tmp_path):
    """15.11: kazı profili her açılışta önceki taramanın pencerelerini geri getiriyordu (about:blank birikmesi)."""
    import json
    from kazi.tarayici import profil as P
    (tmp_path / "Default" / "Sessions").mkdir(parents=True)
    (tmp_path / "Default" / "Sessions" / "Session_1").write_text("x")
    P.profil_tohumla(tmp_path)
    pr = json.loads((tmp_path / "Default" / "Preferences").read_text())
    assert pr["session"]["restore_on_startup"] == 5
    assert not (tmp_path / "Default" / "Sessions").exists()


def test_kisisel_profil_tohumlanmaz(monkeypatch, tmp_path):
    import pytest
    from kazi.tarayici import profil as P
    monkeypatch.setattr(P, "KISISEL_PROFIL", tmp_path)
    with pytest.raises(ValueError):
        P.profil_tohumla(tmp_path)


def test_brave_hazirla_paralel_cagrida_tek_baslatma(monkeypatch, tmp_path):
    """5 işçi aynı anda çağırınca Brave yalnız BİR kez başlatılır (önceden her biri ayrı about:blank açıyordu)."""
    import threading
    import time
    from kazi.tarayici import cekirdek as C
    durum = {"acik": False, "baslatma": 0}
    monkeypatch.setattr(C, "_cdp_hazir", lambda *a, **k: durum["acik"])
    monkeypatch.setattr(C, "brave_komutu", lambda **k: ["brave"])

    def popen(*a, **k):
        durum["baslatma"] += 1
        threading.Timer(0.3, lambda: durum.update(acik=True)).start()
    monkeypatch.setattr(C.subprocess, "Popen", popen)
    uyku = time.sleep
    monkeypatch.setattr(C.time, "sleep", lambda s: uyku(0.05))
    sonuc = []
    th = [threading.Thread(target=lambda: sonuc.append(C.brave_hazirla(bekle=5, profil_dizini=tmp_path)))
          for _ in range(5)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert sonuc == [True] * 5 and durum["baslatma"] == 1
