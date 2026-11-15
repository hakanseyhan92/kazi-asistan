"""Kategori bazlı zamanlayıcı yürütücü testleri — AĞ YOK, saat enjekte."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

from kazi.db import VeriTabani
from kazi.modeller import TZ_ISTANBUL, UrunKimligi
from kazi.zamanlayici import calistir, plan as plan_mod


def _dt(saat: int, dakika: int, saniye: int = 0) -> datetime:
    return datetime(2026, 11, 14, saat, dakika, saniye, tzinfo=TZ_ISTANBUL)


@dataclass
class _SahteUrunSonuc:
    teklifler: dict = field(default_factory=dict)
    kapsam: dict = field(default_factory=dict)


@dataclass
class _SahteUcusSonuc:
    segmentler: dict = field(default_factory=dict)
    kapsam: dict = field(default_factory=dict)
    hatalar: list = field(default_factory=list)


def test_bir_adim_urun_tara_kovala_alarm_ve_olay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    db_yolu = tmp_path / "kazi.db"
    vt = VeriTabani(db_yolu)
    vt.urun_ekle(UrunKimligi(id="gpu_5080", kanonik_ad="RTX 5080", eslesme_kurallari={"kategori": "gpu"}))
    vt.urun_ekle(UrunKimligi(id="gpu_5070", kanonik_ad="RTX 5070", eslesme_kurallari={"kategori": "gpu"}))
    vt.urun_ekle(UrunKimligi(id="cpu_9800", kanonik_ad="Ryzen 7", eslesme_kurallari={"kategori": "cpu"}))
    vt.kapat()

    tara_cagri: list[list[str]] = []
    kovala_cagri: list[tuple[str, int]] = []
    alarm_cagri: list[bool] = []

    def _sahte_tara(v, _t, urun_idler=None, **_kw):
        tara_cagri.append(list(urun_idler or []))
        return _SahteUrunSonuc(
            teklifler={"gpu_5080": [object(), object()], "gpu_5070": [object()]},
            kapsam={("gpu_5080", "n11"): {"hata": None}, ("gpu_5070", "itopya"): {"hata": "zaman aşımı"}},
        )

    def _sahte_kovala(v, _t, urun_id, azami=5, **_kw):
        kovala_cagri.append((urun_id, azami))
        return []

    def _sahte_alarm(v):
        alarm_cagri.append(True)
        return []

    sonuc = calistir.bir_adim(
        db_yolu,
        alt_surec=False,
        simdi_fn=lambda: _dt(19, 0),
        tarayici=object(),
        tara_fn=_sahte_tara,
        kovala_fn=_sahte_kovala,
        alarm_fn=_sahte_alarm,
    )

    assert sonuc is not None and sonuc["kategori"] == "gpu"
    assert tara_cagri == [["gpu_5070", "gpu_5080"]]
    assert kovala_cagri == [("gpu_5070", 3), ("gpu_5080", 3)]
    assert alarm_cagri == [True]

    vt2 = VeriTabani(db_yolu)
    try:
        durum = plan_mod.durum_oku(vt2)
        assert durum["gpu"]["son_baslangic"] == "2026-11-14T19:00:00+03:00"
        assert durum["gpu"]["son_bitis"] == "2026-11-14T19:00:00+03:00"
        assert durum["gpu"]["son_sonuc"]["teklif"] == 3
        assert durum["gpu"]["son_sonuc"]["hata"] == 1

        olaylar = vt2.son_olaylar(limit=5)
        assert olaylar and olaylar[0].tur == "tarama_bitti"
        assert olaylar[0].veri["kategori"] == "gpu"
        assert olaylar[0].veri["teklif"] == 3
        assert olaylar[0].veri["hata"] == 1
        assert "sure_sn" in olaylar[0].veri
    finally:
        vt2.kapat()


def test_bir_adim_ucus_ve_manuel_istek_boslugu_atlar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    db_yolu = tmp_path / "kazi.db"
    vt = VeriTabani(db_yolu)
    vt.urun_ekle(UrunKimligi(id="gpu_1", kanonik_ad="RTX 5080", eslesme_kurallari={"kategori": "gpu"}))
    # gpu 2 dk önce bitmiş -> normalde 10 dk boşluk var, ama ucus için elle istek mevcut
    plan_mod.tarama_basladi(vt, "gpu", _dt(18, 55).isoformat())
    plan_mod.tarama_bitti(vt, "gpu", _dt(18, 58).isoformat(), {"teklif": 1, "hata": 0, "sure_sn": 180})
    plan_mod.manuel_istek_kaydet(vt, "ucus", _dt(18, 59).isoformat())
    vt.kapat()

    # Sahte seyahat planı yaz
    monkeypatch.setattr(
        calistir.plan_depo,
        "yukle",
        lambda *_a, **_k: {
            "surum": 1,
            "ad": "Deneme",
            "yolcular": [{"id": "ali", "ad": "Ali", "uyruklar": ["TR"], "izinler": []}],
            "duraklar": [
                {"sehir": "İstanbul", "tarih": "2026-12-26", "esneklik": 0},
                {"sehir": "Tokyo", "tarih": None, "esneklik": 0},
            ],
            "bacaklar": [{"id": "ist_tyo", "yolcular": ["ali"], "araclar": ["ucak"]}],
            "yan_turlar": [],
        },
    )

    ucus_cagrilar: list[dict] = []

    def _sahte_ucus_tara(v, _t, **kw):
        ucus_cagrilar.append(kw)
        return _SahteUcusSonuc(segmentler={"ist_tyo": [object(), object()]}, kapsam={}, hatalar=[])

    sonuc = calistir.bir_adim(
        db_yolu,
        alt_surec=False,
        simdi_fn=lambda: _dt(19, 0),
        tarayici=object(),
        ucus_tara_fn=_sahte_ucus_tara,
        alarm_fn=lambda _v: [],
    )

    assert sonuc is not None and sonuc["kategori"] == "ucus"
    assert len(ucus_cagrilar) == 1 and ucus_cagrilar[0]["plan"] is not None

    vt2 = VeriTabani(db_yolu)
    try:
        durum = plan_mod.durum_oku(vt2)
        assert durum["ucus"]["manuel_istek_zamani"] is None
        assert durum["ucus"]["manuel_son"] == _dt(18, 59).isoformat()
        assert durum["ucus"]["son_sonuc"]["teklif"] == 2
    finally:
        vt2.kapat()


def test_pid_kilidi_ikinci_kopyayi_reddeder(tmp_path: Path):
    pid_yolu = tmp_path / "zamanlayici.pid"
    db_yolu = tmp_path / "kazi.db"
    VeriTabani(db_yolu).kapat()

    with calistir.tek_kopya(pid_yolu):
        assert pid_yolu.is_file()
        assert pid_yolu.read_text(encoding="utf-8").strip().isdigit()
        with pytest.raises(calistir.ZamanlayiciZatenCalisiyor):
            with calistir.tek_kopya(pid_yolu):
                pass
        assert calistir.calistir(bir_kez=True, kuru=True, pid_yolu=pid_yolu, db_yolu=db_yolu) == 3

    with calistir.tek_kopya(pid_yolu):
        pass


def test_kuru_kip_aga_cikmaz_ve_db_durumu_degistirmez(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    db_yolu = tmp_path / "kazi.db"
    vt = VeriTabani(db_yolu)
    vt.urun_ekle(UrunKimligi(id="gpu_1", kanonik_ad="RTX 5080", eslesme_kurallari={"kategori": "gpu"}))
    vt.kapat()

    def _yasak(*_a, **_k):
        raise AssertionError("kuru kipte çağrılmamalıydı!")

    monkeypatch.setattr(calistir, "urun_tara", _yasak)
    monkeypatch.setattr(calistir, "teyit_kovala", _yasak)
    monkeypatch.setattr(calistir.ucus_tara_mod, "tara", _yasak)
    monkeypatch.setattr(calistir, "alarm_denetle", _yasak)
    monkeypatch.setattr(calistir, "Tarayici", _yasak)

    pid_yolu = tmp_path / "kuru.pid"
    kod = calistir.main(["--bir-kez", "--kuru", "--pid", str(pid_yolu), "--db", str(db_yolu)])
    assert kod == 0
    cikti = capsys.readouterr().out
    assert "[KURU]" in cikti and "gpu" in cikti

    vt2 = VeriTabani(db_yolu)
    try:
        assert plan_mod.durum_oku(vt2) == {}
        assert vt2.son_olaylar() == []
    finally:
        vt2.kapat()


def _uyuyan_alt_surec(_db_yolu: Path, _kat: plan_mod.Kategori) -> None:
    time.sleep(30)


def test_bekci_zaman_asimi_sureci_sonlandirir(tmp_path: Path):
    db_yolu = tmp_path / "kazi.db"
    kat = plan_mod.Kategori("gpu", "Ekran kartı", "urun", ["gpu_1"])
    temizlendi: list[bool] = []
    t0 = time.monotonic()

    ok = calistir.bekcili_kategori_tara(
        db_yolu,
        kat,
        sure_sn=0.4,
        hedef=_uyuyan_alt_surec,
        temizle=lambda: temizlendi.append(True) or 1,
    )
    assert ok is False
    assert time.monotonic() - t0 < 5.0
    assert temizlendi == [True]


def test_dongu_sigterm_ile_temiz_kapanis(tmp_path: Path):
    db_yolu = tmp_path / "kazi.db"
    pid_yolu = tmp_path / "sigterm.pid"
    VeriTabani(db_yolu).kapat()

    # Gerçek alt süreç başlatıp SIGTERM gönder
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "cfg")
    proc = subprocess.Popen(
        [sys.executable, "-m", "kazi.zamanlayici", "--kuru", "--pid", str(pid_yolu), "--db", str(db_yolu)],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        # PID dosyası yazılana kadar kısa bekle
        for _ in range(40):
            if pid_yolu.is_file() and pid_yolu.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        kod = proc.wait(timeout=5)
        assert kod == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def _grup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    db_yolu = tmp_path / "kazi.db"
    vt = VeriTabani(db_yolu)
    vt.urun_ekle(UrunKimligi(id="gpu_1", kanonik_ad="RTX 5080", eslesme_kurallari={"kategori": "gpu"}))
    vt.urun_ekle(UrunKimligi(id="cpu_1", kanonik_ad="Ryzen 7", eslesme_kurallari={"kategori": "cpu"}))
    vt.urun_ekle(UrunKimligi(id="cpu_2", kanonik_ad="Ryzen 5", eslesme_kurallari={"kategori": "cpu"}))
    vt.urun_ekle(UrunKimligi(id="ram_1", kanonik_ad="DDR5", eslesme_kurallari={"kategori": "ram"}))
    vt.kapat()
    return db_yolu


def test_bir_adim_n2_iki_kategori_tek_tara_cagrisinda_birlesir_ve_sonuc_bolunur(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db_yolu = _grup_db(tmp_path, monkeypatch)
    tara_cagri: list[list[str]] = []
    kovala_cagri: list[str] = []
    alarm_cagri: list[bool] = []

    def _sahte_tara(v, _t, urun_idler=None, **_kw):
        tara_cagri.append(sorted(urun_idler or []))
        return _SahteUrunSonuc(
            teklifler={"gpu_1": [object(), object()], "cpu_1": [object()], "cpu_2": [object(), object(), object()]},
            kapsam={
                ("gpu_1", "n11"): {"hata": "zaman aşımı"},
                ("cpu_1", "itopya"): {"hata": None},
                ("cpu_2", "n11"): {"hata": "engel"},
                ("cpu_2", "itopya"): {"hata": "engel"},
            },
        )

    sonuc = calistir.bir_adim(
        db_yolu,
        alt_surec=False,
        kategori_esz=2,
        simdi_fn=lambda: _dt(19, 0),
        tarayici=object(),
        tara_fn=_sahte_tara,
        kovala_fn=lambda v, t, uid, azami=3, **kw: kovala_cagri.append(uid) or [],
        alarm_fn=lambda v: alarm_cagri.append(True) or [],
    )

    assert tara_cagri == [["cpu_1", "cpu_2", "gpu_1"]]  # tek çağrı
    assert sonuc["kategoriler"] == ["gpu", "cpu"]
    assert sorted(kovala_cagri) == ["cpu_1", "cpu_2", "gpu_1"]
    assert alarm_cagri == [True]

    vt = VeriTabani(db_yolu)
    try:
        durum = plan_mod.durum_oku(vt)
        assert durum["gpu"]["son_baslangic"] == durum["cpu"]["son_baslangic"] == "2026-11-14T19:00:00+03:00"
        assert durum["gpu"]["son_sonuc"]["teklif"] == 2 and durum["gpu"]["son_sonuc"]["hata"] == 1
        assert durum["cpu"]["son_sonuc"]["teklif"] == 4 and durum["cpu"]["son_sonuc"]["hata"] == 2
        assert durum["gpu"]["son_bitis"] and durum["cpu"]["son_bitis"]
        assert "ram" not in durum
        kategoriler = {o.veri["kategori"] for o in vt.son_olaylar(limit=5) if o.tur == "tarama_bitti"}
        assert kategoriler == {"gpu", "cpu"}
    finally:
        vt.kapat()


def test_bir_adim_n1_tek_kategori_eskisi_gibi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_yolu = _grup_db(tmp_path, monkeypatch)
    tara_cagri: list[list[str]] = []

    def _sahte_tara(v, _t, urun_idler=None, **_kw):
        tara_cagri.append(sorted(urun_idler or []))
        return _SahteUrunSonuc()

    sonuc = calistir.bir_adim(
        db_yolu, alt_surec=False, kategori_esz=1, simdi_fn=lambda: _dt(19, 0), tarayici=object(),
        tara_fn=_sahte_tara, kovala_fn=lambda *a, **k: [], alarm_fn=lambda v: [],
    )
    assert sonuc["kategori"] == "gpu" and "kategoriler" not in sonuc
    assert tara_cagri == [["gpu_1"]]


def test_bir_adim_n2_kuyruk_onceligi_tek_basina_ve_ucus_birlesmez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db_yolu = _grup_db(tmp_path, monkeypatch)
    vt = VeriTabani(db_yolu)
    plan_mod.manuel_istek_kaydet(vt, "ram", _dt(18, 59).isoformat())
    vt.kapat()
    tara_cagri: list[list[str]] = []

    def _sahte_tara(v, _t, urun_idler=None, **_kw):
        tara_cagri.append(sorted(urun_idler or []))
        return _SahteUrunSonuc()

    sonuc = calistir.bir_adim(
        db_yolu, alt_surec=False, kategori_esz=3, simdi_fn=lambda: _dt(19, 0), tarayici=object(),
        tara_fn=_sahte_tara, kovala_fn=lambda *a, **k: [], alarm_fn=lambda v: [],
    )
    assert sonuc["kategori"] == "ram" and "kategoriler" not in sonuc
    assert tara_cagri == [["ram_1"]]

    # ram taranmış; kalan gpu/cpu/ucus hiç taranmamış: kuru kipte grup ucus içermez
    sonuc2 = calistir.bir_adim(db_yolu, kuru=True, kategori_esz=3, simdi_fn=lambda: _dt(19, 30))
    assert "ucus" not in (sonuc2.get("kategoriler") or [])


def test_yetim_tarama_kaydi_acilista_kapatilir(tmp_path):
    """15.11: yeniden başlatmada yarım kalan tarama 90 dk 'taranıyor' görünmesin."""
    from datetime import datetime as _dt
    from kazi.db import VeriTabani as _VT
    from kazi.modeller import TZ_ISTANBUL as _TZ
    from kazi.zamanlayici import calistir as cal
    from kazi.zamanlayici import plan as pm
    yol = tmp_path / "k.db"
    v = _VT(yol)
    pm.kur(v)
    pm.tarama_basladi(v, "ucus", "2026-11-15T02:36:13+03:00")                     # yarım
    pm.tarama_basladi(v, "gpu", "2026-11-15T02:10:00+03:00")
    pm.tarama_bitti(v, "gpu", "2026-11-15T02:11:00+03:00", {"teklif": 5, "hata": 0, "sure_sn": 60})
    v.kapat()
    simdi = _dt(2026, 11, 15, 2, 50, tzinfo=_TZ)
    assert cal.yetim_taramalari_kapat(yol, simdi) == ["ucus"]
    v = _VT(yol)
    d = pm.durum_oku(v)
    assert d["ucus"]["son_bitis"] == "2026-11-15T02:50:00+03:00" and d["ucus"]["son_sonuc"]["kesildi"] is True
    assert d["gpu"]["son_sonuc"]["teklif"] == 5
    calisan, _ = pm.aktif_durumlar_coz(d, simdi=simdi)
    assert calisan == []
    v.kapat()
    assert cal.yetim_taramalari_kapat(yol, simdi) == []


def test_otomatik_tarayici_kapaliyken_yalniz_elle_istek_taranir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """15.11: tarayici_otomatik=False → vadesi gelen kategori başlamaz; elle 'Şimdi tara' isteği başlar."""
    from kazi.tarayici import gorunurluk
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    gorunurluk.ayar_yaz(tarayici_otomatik=False)
    db_yolu = tmp_path / "kazi.db"
    vt = VeriTabani(db_yolu)
    vt.urun_ekle(UrunKimligi(id="gpu_5080", kanonik_ad="RTX 5080", eslesme_kurallari={"kategori": "gpu"}))
    vt.kapat()
    cagri: list = []

    def _tara(v, _t, urun_idler=None, **_kw):
        cagri.append(urun_idler)
        return _SahteUrunSonuc()

    kw = dict(alt_surec=False, simdi_fn=lambda: _dt(19, 0), tarayici=object(), tara_fn=_tara,
              kovala_fn=lambda *a, **k: [], alarm_fn=lambda v: [])
    assert calistir.bir_adim(db_yolu, **kw) is None and cagri == []
    vt = VeriTabani(db_yolu)
    plan_mod.manuel_istek_kaydet(vt, "gpu", plan_mod.iso_bicim(_dt(18, 59)))
    vt.kapat()
    sonuc = calistir.bir_adim(db_yolu, **kw)
    assert sonuc is not None and sonuc["kategori"] == "gpu" and cagri == [["gpu_5080"]]
