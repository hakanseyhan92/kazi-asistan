"""Brave göster/gizle ayarı (tarayici/gorunurluk.py)."""
from kazi.tarayici import gorunurluk as g


class SahteOturum:
    def __init__(self, kayit):
        self.k = kayit

    def send(self, yontem, param=None):
        self.k.append((yontem, param))
        if yontem == "Target.getTargets":
            return {"targetInfos": [{"type": "page", "targetId": "a"}, {"type": "page", "targetId": "b"},
                                    {"type": "service_worker", "targetId": "c"}]}
        if yontem == "Browser.getWindowForTarget":
            return {"windowId": 7 if param["targetId"] == "a" else 8}
        if yontem == "Browser.getWindowBounds":
            return {"bounds": {"windowState": self.son}}
        if yontem == "Browser.setWindowBounds":
            self.son = param["bounds"]["windowState"]
        return {}

    def detach(self):
        pass


class SahteTarayici:
    def __init__(self):
        self.kayit = []

    def new_browser_cdp_session(self):
        return SahteOturum(self.kayit)


def test_ayar_varsayilan_gorunur_ve_yazma(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert g.gorunur_mu() is True
    g.ayar_yaz(brave_gorunur=False)
    assert g.gorunur_mu() is False and (tmp_path / "kazi" / "tarama.json").exists()


def test_pencereleri_kucultur_ve_geri_acar(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    g.ayar_yaz(brave_gorunur=False)
    b = SahteTarayici()
    assert g.pencereleri_ayarla(b) == ["minimized", "minimized"]
    ayarlanan = [p["windowId"] for y, p in b.kayit if y == "Browser.setWindowBounds"]
    assert sorted(ayarlanan) == [7, 8]                      # service_worker hedefi atlandı
    assert g.pencereleri_ayarla(b, gorunur=True) == ["normal", "normal"]


def test_hata_yutulur():
    class Bozuk:
        def new_browser_cdp_session(self):
            raise RuntimeError("bağlantı yok")
    assert g.pencereleri_ayarla(Bozuk(), gorunur=False) == []


def test_api_tarama_ayari(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from kazi.api import uygulama_olustur
    from kazi.ayar import Ayar
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = uygulama_olustur(Ayar(veri_dizini=tmp_path))
    import kazi.api as api_mod  # Brave'e dokunmasın
    with TestClient(app) as c:
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("yok")))
        assert c.get("/ayarlar/tarama").json()["brave_gorunur"] is True
        r = c.put("/ayarlar/tarama", json={"brave_gorunur": False}).json()
        assert r["brave_gorunur"] is False and all(not b["calisiyor"] for b in r["brave"])
        assert c.get("/ayarlar/tarama").json()["brave_gorunur"] is False
        assert c.put("/ayarlar/tarama", json={"brave_gorunur": "evet"}).status_code == 422
        # gizlilik/hız ayarları (15.11): varsayılan hepsi açık, tek tek değişir, Brave'e dokunmaz
        g = c.get("/ayarlar/tarama").json()
        assert all(g[k] is True for k in ("analiz_kapali", "bildirim_engelle", "popup_engelle", "cerez_reddet"))
        r = c.put("/ayarlar/tarama", json={"cerez_reddet": False}).json()
        assert r["cerez_reddet"] is False and r["brave"] == [] and r["brave_gorunur"] is False
        assert c.put("/ayarlar/tarama", json={"bilinmeyen": True}).status_code == 422
        assert c.put("/ayarlar/tarama", json={}).status_code == 422
        # paralel tarama (15.11)
        g = c.get("/ayarlar/tarama").json()
        assert g["sekme_sayisi"] == 3 and g["kategori_esz"] == 1 and g["reklam_engelle"] is True and g["gorsel_engelle"] is False
        assert g["sistem"]["cpu_cekirdek"] >= 1 and 2 <= g["sistem"]["onerilen_sekme"] <= 10
        assert c.put("/ayarlar/tarama", json={"sekme_sayisi": 6}).json()["sekme_sayisi"] == 6
        assert c.put("/ayarlar/tarama", json={"sekme_sayisi": 11}).status_code == 422
        assert c.put("/ayarlar/tarama", json={"sekme_sayisi": True}).status_code == 422
        assert g["chromium_yedek"] is True and c.put("/ayarlar/tarama", json={"chromium_yedek": False}).json()["chromium_yedek"] is False
        assert c.put("/ayarlar/tarama", json={"chromium_yedek": "evet"}).status_code == 422
        assert c.put("/ayarlar/tarama", json={"kategori_esz": 0}).status_code == 422
