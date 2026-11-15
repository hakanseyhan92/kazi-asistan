"""Ortak test ayarları."""
from pathlib import Path

import pytest

ORNEK_ESKI_PLAN = Path(__file__).parent / "veri" / "eski_plan_ornek.json"


@pytest.fixture(autouse=True)
def _secici_hafiza_gecici(monkeypatch, tmp_path):
    """Testler kullanıcının gerçek arama kutusu seçici hafızasını okumasın/yazmasın."""
    monkeypatch.setenv("KAZI_SECICI_HAFIZA", str(tmp_path / "secici_hafiza.json"))
    monkeypatch.setenv("KAZI_GUNLUK", str(tmp_path / "gunluk.db"))      # kayıt defteri gerçek dosyaya yazılmasın
    monkeypatch.setenv("KAZI_DOGRULAMA_ISTEK", str(tmp_path / "dogrulama_istek.json"))
    # transit kural motoru: kullanıcının kişisel kuralları yerine uydurma "XX" uyruğu kuralları (yasak/airside/vize)
    # eşleştirme testleri: sabit örnek ürün kuralları (kullanıcının dosyası değil)
    from kazi import eslestir
    monkeypatch.setattr(eslestir, "ESKI_URUNLER_DOSYA", Path(__file__).parent / "veri" / "urunler_ornek.json")
    eslestir._onbellek.clear()
    monkeypatch.setenv("KAZI_TRANSIT_KURALLARI", str(Path(__file__).parent / "veri" / "transit_test_kurallari.json"))


@pytest.fixture(autouse=True)
def _eski_plan_sabit(monkeypatch):
    """Testler kullanıcının GERÇEK ~/evren-asistan/ucus/plan.json dosyasını okumasın: arayüzden plan değişince
    (15.11: Ankara bacağı eklendi) testler kırılıyordu. 14.11 ilk aktarım yedeği sabit örnek olarak kullanılır."""
    from kazi.seyahat import plan, plan_depo
    monkeypatch.setattr(plan, "ESKI_PLAN", ORNEK_ESKI_PLAN)
    monkeypatch.setattr(plan_depo, "ESKI_PLAN", ORNEK_ESKI_PLAN)
