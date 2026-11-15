"""Tarama döngüsü, zamanlama (`planla`), bekçi ve PID kilidi testleri — AĞ YOK."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time

import pytest

from kazi.ayar import Ayar
from kazi.db import VeriTabani
from kazi.modeller import UrunKimligi
from kazi.tarama import dongu


def test_planla_saf_fonksiyonu():
    # RTX: her saatin :05 ve :35 dakikalarında
    assert dongu.planla(datetime(2026, 11, 14, 10, 5)) == ["rtx"]
    assert dongu.planla(datetime(2026, 11, 14, 10, 35)) == ["rtx"]
    assert dongu.planla(datetime(2026, 11, 14, 10, 7)) == []

    # Parçalar hızlı: 3 saatte bir :20
    assert dongu.planla(datetime(2026, 11, 14, 0, 20)) == ["hizli"]
    assert dongu.planla(datetime(2026, 11, 14, 3, 20)) == ["hizli"]
    assert dongu.planla(datetime(2026, 11, 14, 9, 20)) == ["hizli"]
    assert dongu.planla(datetime(2026, 11, 14, 10, 20)) == []

    # Günlük tam tarama: 04:15
    assert dongu.planla(datetime(2026, 11, 14, 4, 15)) == ["tam"]
    assert dongu.planla(datetime(2026, 11, 14, 5, 15)) == []

    # Uçuş zamanlaması: 3 saatte bir :50 (03:50 hariç) ve 02:50 tam
    assert "ucus" in dongu.planla(datetime(2026, 11, 14, 9, 50))
    assert "ucus_tam" in dongu.planla(datetime(2026, 11, 15, 2, 50))
    assert "ucus" not in dongu.planla(datetime(2026, 11, 15, 3, 50))


def test_planla_aralik_kacan_dakikalari_yakalar():
    baslangic = datetime(2026, 11, 14, 4, 4)
    bitis = datetime(2026, 11, 14, 4, 20)
    isler = dongu.planla_aralik(baslangic, bitis)
    assert isler == ["rtx", "tam"]


def test_ikinci_kopya_pid_engeli(tmp_path: Path):
    pid_yolu = tmp_path / "tarama.pid"
    with dongu.tek_kopya(pid_yolu):
        assert pid_yolu.is_file()
        assert pid_yolu.read_text(encoding="utf-8").strip().isdigit()
        with pytest.raises(dongu.DonguZatenCalisiyor):
            with dongu.tek_kopya(pid_yolu):
                pass
        # CLI calistir ikinci kopyada 3 döndürür
        assert dongu.calistir(bir_kez=True, kuru=True, pid_yolu=pid_yolu) == 3

    # Kilit bırakıldıktan sonra tekrar alınabilir
    with dongu.tek_kopya(pid_yolu):
        pass


def _uyuyan_hedef(isler, kuru, bildirim_fn, simdi):
    time.sleep(30)


def _hizli_hedef(isler, kuru, bildirim_fn, simdi):
    return None


def test_bekci_takilan_turu_sonlandirir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dongu, "BEKCI_DURUM", tmp_path / "bekci.json")
    monkeypatch.setattr(dongu, "log", lambda *_a, **_k: None)

    bildirimler: list[dict] = []
    temizlenen: list[int] = []
    t0 = time.monotonic()
    ok = dongu.bekcili_tur(
        ["rtx"],
        kuru=False,
        bildirim_fn=bildirimler.append,
        sure_sn=0.5,
        hedef=_uyuyan_hedef,
        temizle=lambda: temizlenen.append(1) or 2,
    )
    assert ok is False
    assert time.monotonic() - t0 < 10
    assert temizlenen == [1]
    assert len(bildirimler) == 1
    assert bildirimler[0]["tur"] == "bekci_zaman_asimi"

    # Aynı gün içinde ikinci kez bildirim gönderilmez
    dongu.bekcili_tur(
        ["rtx"],
        kuru=False,
        bildirim_fn=bildirimler.append,
        sure_sn=0.5,
        hedef=_uyuyan_hedef,
        temizle=lambda: 0,
    )
    assert len(bildirimler) == 1


def test_bekci_zamaninda_biten_tur():
    assert dongu.bekcili_tur(["rtx"], sure_sn=10, hedef=_hizli_hedef, temizle=None) is True


def test_bir_tur_hata_yalitimi_ve_kuru_kip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dongu, "log", lambda *_a, **_k: None)
    vt = VeriTabani(Ayar(veri_dizini=tmp_path).db_yolu)
    vt.urun_ekle(UrunKimligi(id="gpu_rtx5080", kanonik_ad="RTX 5080"))

    def _patla(*_a, **_k):
        raise RuntimeError("tarama hatası")

    monkeypatch.setattr(dongu, "tara", _patla)
    ozet = dongu.bir_tur(isler=["rtx"], kuru=True, v=vt, tarayici=object())
    vt.kapat()

    assert ozet["isler"] == ["rtx"]
    assert ozet["hatalar"] and "tarama hatası" in ozet["hatalar"][0]
    assert ozet["sure"] >= 0


def test_cli_main_bir_kez_ve_kuru(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cagrilar: list[tuple[list[str], bool]] = []

    def _sahte_bir_tur(isler=None, kuru=False, **_kw):
        cagrilar.append((list(isler or []), bool(kuru)))
        return {"isler": list(isler or []), "sure": 0.1, "kapsam": ["rtx ok"], "hatalar": []}

    monkeypatch.setattr(dongu, "bir_tur", _sahte_bir_tur)
    pid_yolu = tmp_path / "cli_tarama.pid"
    kod = dongu.main(["dongu", "--bir-kez", "--kuru", "--pid", str(pid_yolu)])
    assert kod == 0
    assert cagrilar == [(["rtx"], True)]

