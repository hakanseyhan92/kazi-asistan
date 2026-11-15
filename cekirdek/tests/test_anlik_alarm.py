"""15.11 kullanıcı: alarm Telegram'a taramanın bitmesini beklemeden düşsün."""
from __future__ import annotations

from datetime import datetime, timedelta

from kazi.bildirim import telegram as tg
from kazi.db import VeriTabani
from kazi.modeller import TZ_ISTANBUL, UrunKimligi
from kazi.siteler._kart import Kart

SIMDI = datetime(2026, 11, 15, 3, 0, tzinfo=TZ_ISTANBUL)


def _vt(tmp_path):
    v = VeriTabani(tmp_path / "k.db")
    v.urun_ekle(UrunKimligi(id="gpu", kanonik_ad="RTX 5080", eslesme_kurallari={"kategori": "gpu"}))
    v.alarm_yaz("gpu", 110000, SIMDI.isoformat())
    return v


def _k(fiyat, site="trendyol", url="https://t/1", **kw):
    return Kart(site=site, baslik="RTX 5080", url=url, sepet_fiyati=fiyat, zaman=SIMDI.isoformat(), **kw)


def test_alarm_altindaki_en_ucuz_kart_hemen_gider_tekrar_gitmez(tmp_path):
    v = _vt(tmp_path)
    giden = []
    sonuc = tg.anlik_alarm(v, "gpu", [_k(115000, url="u1"), _k(108000, url="u2"), _k(109000, url="u3")],
                           gonder_fn=lambda m: giden.append(m) or True, simdi=SIMDI)
    assert sonuc == [{"urun_id": "gpu", "fiyat": 108000.0, "alarm": 110000, "site": "trendyol"}]
    assert len(giden) == 1 and "RTX 5080" in giden[0] and "108.000 TL" in giden[0] and "Alarm fiyatı 110.000 TL" in giden[0]
    assert tg.anlik_alarm(v, "gpu", [_k(108000, url="u2")], gonder_fn=lambda m: giden.append(m) or True,
                          simdi=SIMDI + timedelta(minutes=5)) == []
    assert len(giden) == 1
    v.kapat()


def test_alarm_ustu_stoksuz_alarm_yok_ve_gonderim_hatasi(tmp_path):
    v = _vt(tmp_path)
    giden = []
    f = lambda m: giden.append(m) or True  # noqa: E731
    assert tg.anlik_alarm(v, "gpu", [_k(111000)], gonder_fn=f, simdi=SIMDI) == []
    assert tg.anlik_alarm(v, "gpu", [_k(100000, stok=False)], gonder_fn=f, simdi=SIMDI) == []
    assert tg.anlik_alarm(v, "yok", [_k(1)], gonder_fn=f, simdi=SIMDI) == []
    assert giden == []
    # gönderilemezse kayıt düşülmez → sonra yeniden denenir
    assert tg.anlik_alarm(v, "gpu", [_k(100000)], gonder_fn=lambda m: False, simdi=SIMDI) == []
    assert tg.anlik_alarm(v, "gpu", [_k(100000)], gonder_fn=f, simdi=SIMDI) != []
    v.kapat()


def test_piyasanin_cok_altindaki_supheli_fiyat_anlik_gitmez(tmp_path, monkeypatch):
    v = _vt(tmp_path)
    import kazi.pano as pano_mod
    import kazi.kurallar.piyasa as piyasa
    monkeypatch.setattr(pano_mod, "_taze_teklifler", lambda *a, **k: [object()])
    monkeypatch.setattr(piyasa, "piyasa_ozeti", lambda *a, **k: {"medyan": 130000})
    giden = []
    assert tg.anlik_alarm(v, "gpu", [_k(90000)], gonder_fn=lambda m: giden.append(m) or True, simdi=SIMDI) == []
    assert tg.anlik_alarm(v, "gpu", [_k(105000)], gonder_fn=lambda m: giden.append(m) or True, simdi=SIMDI) != []
    v.kapat()
