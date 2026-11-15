"""Uçuş kaynakları ayrıştırma → Segment şeması testleri (AĞ YOK)."""
from __future__ import annotations

from datetime import timedelta, timezone
import json
import os
from pathlib import Path

import pytest

from kazi.seyahat.segment import Aktarma, Segment
from kazi.ucus.kaynaklar import (
    DESTEKLENMIYOR,
    KAYNAKLAR,
    _oturum,
    enuygun,
    google,
    kayak,
    qatar,
    skyscanner,
    thy,
    tripcom,
    turkmenistan,
)
from kazi.ucus.model import BotEngeli, Ucus, ucus_segment

FIX = Path(os.environ.get("KAZI_ESKI_KOK") or Path.home() / "evren-asistan") / "ucus" / "tests" / "fixtures"   # yoksa ilgili testler atlanır


def _fix(ad: str) -> Path:
    p = FIX / ad
    if not p.exists():
        pytest.skip(f"isteğe bağlı eski fikstür yok: {ad}")
    return p
TZ_TR = timezone(timedelta(hours=3))
TZ_JP = timezone(timedelta(hours=9))
TZ_KR = timezone(timedelta(hours=9))


# ── Google Flights ──────────────────────────────────────────────────────────


def test_google_fixture_segment_ve_saat_dilimi():
    html = _fix("google_ist_tyo.html").read_text(encoding="utf-8")
    segler = google.ayristir(html, "ist_tyo", "2026-12-27", 2)
    assert len(segler) >= 5
    assert all(isinstance(s, Segment) for s in segler)
    assert [s.kisi_basi for s in segler] == sorted(s.kisi_basi for s in segler)

    ilk = segler[0]
    assert ilk.arac == "ucak" and ilk.kaynak == "google"
    assert ilk.kalkis_nokta == "IST" and ilk.varis_nokta == "NRT"
    assert ilk.kalkis_dt.tzinfo == TZ_TR
    assert ilk.varis_dt.tzinfo == TZ_JP
    assert ilk.sure_dk > 0
    assert ilk.aktarma_sayisi == len(ilk.aktarmalar)

    # Canlı fixture'da MIAT IST -> UBN -> NRT, 2 kişi toplam 74.751 TL
    miat = [s for s in segler if "MIAT" in (s.firma or "")]
    assert miat
    assert [a.nokta for a in miat[0].aktarmalar] == ["UBN"]
    assert miat[0].toplam == 74751.0
    assert miat[0].kisi_basi == 74751.0 / 2

    # 2+ aktarmalı uçuşlarda aktarma sayısı rota uzunluğuyla uyumlu
    uzun = [s for s in segler if len(s.aktarmalar) >= 2]
    assert uzun
    for s in uzun:
        assert s.aktarma_sayisi == len(s.aktarmalar) >= 2


def test_google_yardimcilar_ve_ayri_bilet_tahmini():
    assert google.havalimanlari("TYO") == ("NRT", "HND")
    assert google.havalimanlari("SEL") == ("ICN", "GMP")
    assert google.havalimanlari(" IST ") == ("IST",)
    assert "IST" in google.link_olustur("IST", "NRT", "2026-12-27")
    assert (
        google.ayristir(
            '<html><body><script class="ds:1">AF_initDataCallback({key: "ds:1", '
            'data:[[null,null,null,null,[null]]], sideChannel: {}});</script></body></html>'
        )
        == []
    )
    # Ayrı bilet tahmini: Scoot / Spring / birden fazla havayolu aktarmalı uçuşta ayri_bilet=True
    seg = ucus_segment(
        Ucus(
            kaynak="google",
            bacak_id="ist_icn",
            tarih="2026-12-27",
            fiyat_toplam=42000,
            havayolu="Scoot",
            rota=["IST", "SIN", "ICN"],
            aktarma=1,
            sure_dk=900,
            kalkis_saat="14:00",
            varis_saat="11:00",
            yolcu=2,
        )
    )
    assert seg is not None
    assert seg.kalkis_dt.tzinfo == TZ_TR and seg.varis_dt.tzinfo == TZ_KR
    assert seg.ayri_bilet is True
    assert seg.aktarmalar == [Aktarma("SIN", ulke="SG", ayri_bilet=True)]


def test_google_fast_flights_paketi():
    pytest.importorskip("fast_flights")
    html = _fix("google_ist_tyo.html").read_text(encoding="utf-8")
    segler = google.ayristir(html, "ist_tyo", "2026-12-27", 2)
    assert len(segler) >= 5


# ── Skyscanner ──────────────────────────────────────────────────────────────


def test_skyscanner_api_ve_metin_segment():
    j = json.loads(_fix("skyscanner_ist_nrt.json").read_text(encoding="utf-8"))
    yanitlar = [
        {
            "url": "https://www.skyscanner.com.tr/g/radar/api/v2/web-unified-search/x",
            "durum": 200,
            "json": j,
        }
    ]
    api_seg = skyscanner.ayristir_api(yanitlar, "ist_tyo", "2026-12-27", 2)
    assert len(api_seg) >= 5
    assert all(isinstance(s, Segment) for s in api_seg)
    assert api_seg == sorted(api_seg, key=lambda s: s.kisi_basi)

    ilk = api_seg[0]
    assert ilk.kaynak == "skyscanner"
    assert ilk.kalkis_nokta == "IST" and ilk.varis_nokta == "NRT"
    assert ilk.kalkis_dt.utcoffset() == timedelta(hours=3)
    assert ilk.varis_dt.utcoffset() == timedelta(hours=9)
    assert ilk.kisi_basi == ilk.toplam / 2
    assert "skyscanner.com.tr/transport/flights/ist/nrt/" in (ilk.link or "")

    # Çin aktarmalı uçuş
    cin = [s for s in api_seg if any(a.nokta == "CAN" for a in s.aktarmalar)]
    assert cin
    assert cin[0].aktarmalar[0].ulke == "CN"

    # Tekilleştirme
    assert len(skyscanner.ayristir_api(yanitlar + yanitlar, "ist_tyo", "2026-12-27", 2)) == len(api_seg)

    # Gövde metni yedeği
    metin = _fix("skyscanner_body.txt").read_text(encoding="utf-8")
    metin_seg = skyscanner.ayristir_metin(metin, "ist_tyo", "2026-12-27", 2)
    assert len(metin_seg) >= 4
    cs = [s for s in metin_seg if "China Southern" in (s.firma or "")]
    assert cs
    assert cs[0].toplam == 59735.0
    assert [a.nokta for a in cs[0].aktarmalar] == ["CAN"]
    assert cs[0].kalkis_dt.hour == 14 and cs[0].kalkis_dt.minute == 25

    thy_s = [s for s in metin_seg if "Türk Hava Yolları" in (s.firma or "")]
    assert thy_s and thy_s[0].aktarma_sayisi == 0


# ── Kayak ───────────────────────────────────────────────────────────────────


def test_kayak_metin_ve_api_segment():
    metin = _fix("kayak_ist_tyo.txt").read_text(encoding="utf-8")
    segler = kayak.ayristir_metin(metin, "ist_tyo", "2026-12-27", 2)
    assert len(segler) >= 10
    assert all(isinstance(s, Segment) for s in segler)
    assert segler == sorted(segler, key=lambda s: s.kisi_basi)

    cs = [s for s in segler if "China Southern" in (s.firma or "")]
    assert cs
    assert cs[0].kalkis_nokta == "IST" and cs[0].varis_nokta == "NRT"
    assert cs[0].aktarmalar[0].nokta == "CAN"
    assert cs[0].aktarmalar[0].ulke == "CN"
    assert cs[0].toplam == 59692.0 and cs[0].kisi_basi == 29846.0
    assert cs[0].kalkis_dt.utcoffset() == timedelta(hours=3)
    assert cs[0].varis_dt.utcoffset() == timedelta(hours=9)

    # Kendi kendine aktarma -> ayri_bilet True
    assert any(s.ayri_bilet for s in segler)

    j = json.loads(_fix("kayak_ist_tyo.json").read_text(encoding="utf-8"))
    yanitlar = [
        {
            "url": "https://www.kayak.com.tr/i/api/search/dynamic/flights/poll",
            "durum": 200,
            "json": j,
        }
    ]
    api_seg = kayak.ayristir_api(yanitlar, "ist_tyo", "2026-12-27", 2)
    assert api_seg
    assert any(s.toplam == 59692.0 for s in api_seg)
    assert all(s.kalkis_nokta == "IST" and s.varis_nokta == "NRT" for s in api_seg)


# ── Enuygun ─────────────────────────────────────────────────────────────────


def test_enuygun_metin_segment():
    metin = _fix("enuygun_ist_tyo.txt").read_text(encoding="utf-8")
    segler = enuygun.ayristir_metin(metin, "ist_tyo", "2026-12-27", 2)
    assert len(segler) >= 10
    assert all(isinstance(s, Segment) for s in segler)

    ce = [s for s in segler if "China Eastern" in (s.firma or "")]
    assert ce
    assert ce[0].kalkis_nokta == "IST" and ce[0].varis_nokta == "NRT"
    assert [a.nokta for a in ce[0].aktarmalar] == ["PVG"]
    assert ce[0].aktarmalar[0].ulke == "CN"
    assert ce[0].toplam == 53734.0 and ce[0].kisi_basi == 26867.0
    assert ce[0].kalkis_dt.tzinfo == TZ_TR and ce[0].varis_dt.tzinfo == TZ_JP

    thy_s = [s for s in segler if "Türk Hava Yolları" in (s.firma or "")]
    assert thy_s and thy_s[0].aktarma_sayisi == 0
    assert thy_s[0].toplam == 115560.0

    qr = [s for s in segler if "Qatar" in (s.firma or "")]
    assert qr and qr[0].aktarmalar[0].nokta == "DOH"
    assert qr[0].aktarmalar[0].ulke == "QA"
    assert qr[0].toplam == 66303.0

    # Tekilleştirme
    assert len(enuygun.ayristir_metin(metin + metin, "ist_tyo", "2026-12-27", 2)) == len(segler)


# ── Trip.com ────────────────────────────────────────────────────────────────


def test_tripcom_api_ve_metin_segment():
    j = json.loads(_fix("tripcom_ist_tyo.json").read_text(encoding="utf-8"))
    yanitlar = [
        {
            "url": "https://tr.trip.com/restapi/soa2/27015/FlightMiddleSearch",
            "durum": 200,
            "json": j,
        }
    ]
    api_seg = tripcom.ayristir_api(yanitlar, "ist_tyo", "2026-12-27", 2)
    assert len(api_seg) >= 5
    assert all(isinstance(s, Segment) for s in api_seg)

    qr = [s for s in api_seg if "Qatar" in (s.firma or "")]
    assert qr
    assert qr[0].kalkis_nokta == "IST" and qr[0].varis_nokta == "NRT"
    assert [a.nokta for a in qr[0].aktarmalar] == ["DOH"]
    assert qr[0].sure_dk == 965
    assert qr[0].kalkis_dt.hour == 13 and qr[0].kalkis_dt.minute == 50
    assert qr[0].kalkis_dt.utcoffset() == timedelta(hours=3)
    assert qr[0].varis_dt.utcoffset() == timedelta(hours=9)
    assert any(abs((s.toplam or 0) - 62973.74) < 0.01 for s in qr)

    metin = _fix("tripcom_ist_tyo.txt").read_text(encoding="utf-8")
    metin_seg = tripcom.ayristir_metin(metin, "ist_tyo", "2026-12-27", 2)
    assert len(metin_seg) >= 5
    cz = [s for s in metin_seg if "China Southern" in (s.firma or "")]
    assert cz and cz[0].aktarmalar[0].nokta == "CAN"
    assert cz[0].toplam == 59810.0


# ── Desteklenmeyen Havayolu Kaynakları (THY, Turkmenistan, Qatar) ───────────


def test_desteklenmeyen_kaynaklar_sozlesmesi():
    for ad in ("thy", "turkmenistan", "qatar"):
        assert ad in KAYNAKLAR
        assert ad in DESTEKLENMIYOR
        assert KAYNAKLAR[ad].DESTEKLENIYOR is False

    bacak_tyo = {"id": "ist_tyo", "kalkis": "IST", "varis": "TYO", "yolcu": 2}
    with pytest.raises(BotEngeli) as e_thy:
        thy.ara(None, bacak_tyo, "2026-12-27")
    assert e_thy.value.site == "thy"
    assert "px-captcha" in thy.NEDEN

    with pytest.raises(NotImplementedError) as e_qr:
        qatar.ara(None, bacak_tyo, "2027-01-06")
    assert "interrupted" in str(e_qr.value)

    # Turkmenistan: Tokyo hattı yok -> []
    bacak_nrt = {"id": "ist_nrt", "kalkis": "IST", "varis": "NRT", "yolcu": 2}
    assert turkmenistan.ara(None, bacak_nrt, "2026-12-27") == []

    # Turkmenistan: Seul hattı var ama CAPTCHA zorunlu -> BotEngeli
    bacak_icn = {"id": "ist_icn", "kalkis": "IST", "varis": "ICN", "yolcu": 2}
    with pytest.raises(BotEngeli) as e_tm:
        turkmenistan.ara(None, bacak_icn, "2026-12-27")
    assert "captcha" in str(e_tm.value).lower() or "turkmenistan" in str(e_tm.value)

    html = _fix("turkmenistan_ana.html").read_text(encoding="utf-8", errors="replace")
    assert turkmenistan.captcha_var(html)


# ── Oturum Yardımcısı (_oturum.py) ──────────────────────────────────────────


class _SahteSekme:
    def __init__(self):
        self.olaylar = []
        self.url = "about:blank"

    def goto(self, url, **kw):
        self.olaylar.append(("goto", url))
        self.url = url

    def evaluate(self, ifade, *a):
        if "location.assign" in ifade:
            url = a[0] if a else ""
            self.olaylar.append(("assign", url))
            self.url = url
        return None

    def wait_for_timeout(self, ms):
        return None

    def wait_for_load_state(self, *a, **kw):
        return None

    def get_by_role(self, *a, **kw):
        class _Bos:
            def count(self):
                return 0

        return _Bos()


class _SahteTarayici:
    def __init__(self):
        self.sekme = _SahteSekme()

    def _sekme(self, site):
        return self.sekme


def test_oturum_yardimcisi_ilk_arama_ana_sayfa():
    ana = "https://ornek.test/"
    arama = "https://ornek.test/arama?x=1"

    t1 = _SahteTarayici()
    kayit1 = _oturum.oturum_ac(t1, "ornek.test", ana, lambda pg: True, arama)
    assert kayit1["form"] is True
    assert t1.sekme.olaylar == [("goto", ana)]

    # İkinci aramada yeniden ana sayfaya gitmez
    _oturum.oturum_ac(t1, "ornek.test", ana, lambda pg: True, arama)
    assert t1.sekme.olaylar == [("goto", ana)]

    # Form başarısız olursa ana sayfadan location.assign ile geçer
    t2 = _SahteTarayici()
    kayit2 = _oturum.oturum_ac(t2, "ornek.test", ana, lambda pg: False, arama)
    assert kayit2["form"] is False
    assert t2.sekme.olaylar == [("goto", ana), ("assign", arama)]


def test_kayak_coklu_havalimani_url_ve_tamam_sinyali():
    from kazi.ucus.kaynaklar import kayak
    assert kayak.url_olustur(["hnd", "NRT"], ["ICN", "GMP"], "2027-01-01", 1) == \
        "https://www.kayak.com.tr/flights/HND,NRT-ICN,GMP/2027-01-01/1adults?sort=price_a"
    assert kayak.url_olustur("IST", "HND", "2026-12-26", 2).endswith("/IST-HND/2026-12-26/2adults?sort=price_a")
    assert not kayak.arama_bitti_mi([{"json": {"status": "first-phase"}}, {"json": None}])
    assert kayak.arama_bitti_mi([{"json": {"status": "first-phase"}}, {"json": {"status": "complete"}}])
    assert not kayak.arama_bitti_mi([])


def test_enuygun_tek_yolcu_toplamsiz_fiyat():
    """15.11 canlı: tek yolcuda "Toplam" satırı yok ("16.735 TL Seç") → önceden 0 sonuç + 60 sn bekleme."""
    import re
    metin = (Path(__file__).parent / "veri" / "enuygun_tek_yolcu.txt").read_text(encoding="utf-8")
    assert re.search(enuygun.HAZIR_DESEN, metin)
    segler = enuygun.ayristir_metin(metin, "tyo_sel", "2026-12-29", 1)
    assert len(segler) > 40
    ilk = segler[0]
    assert ilk.kisi_basi == 16735.0 and ilk.kalkis_nokta == "HND" and ilk.varis_nokta == "ICN"
    assert "Peach" in (ilk.firma or "")


def test_firma_tasima_turu_tahmini():
    """15.11: Kayak ESB↔IST otobüs seferini firma "Otobüs" ile veriyordu, kayıt uçak sanılıyordu."""
    from kazi.ucus.model import arac_tahmini
    assert arac_tahmini("Otobüs") == "otobus" and arac_tahmini("Kamil Koç Bus") == "otobus"
    assert arac_tahmini("Metro Turizm") == "otobus" and arac_tahmini("Kamil Koç") == "otobus"
    assert arac_tahmini("Pamukkale") == "otobus" and arac_tahmini("AJet") == "ucak" and arac_tahmini("Pegasus") == "ucak"
    assert arac_tahmini("TCDD YHT") == "yht" and arac_tahmini("Tren") == "yht"
    assert arac_tahmini("Türk Hava Yolları") == "ucak" and arac_tahmini("Busan Air") == "ucak" and arac_tahmini(None) == "ucak"


def test_havayolu_kodu_tam_ad():
    """15.11: Kayak API yolu firma yerine IATA kodu veriyordu (VF, TK, ET…) → gösterimde tam ad."""
    from kazi.ucus.havayollari import havayolu_adi, HAVAYOLLARI
    assert havayolu_adi("VF") == "AJet" and havayolu_adi("TK") == "Türk Hava Yolları"
    assert havayolu_adi("A3, ET, ZG") == "Aegean Airlines, Ethiopian Airlines, ZIPAIR"
    assert havayolu_adi("GK, D7") == "Jetstar Japan, AirAsia X"
    assert havayolu_adi("TK, TK") == "Türk Hava Yolları"
    assert havayolu_adi("HH") == "HH"                                     # emin olunmayan kod: olduğu gibi
    assert havayolu_adi("Türk Hava Yolları, AJet") == "Türk Hava Yolları, AJet"   # ad zaten yazılı
    assert havayolu_adi("") == "" and havayolu_adi(None) == ""
    # Tam ad otobüs/tren sanılmamalı (Air Busan, Spring Japan vb.)
    from kazi.ucus.model import arac_tahmini
    assert all(arac_tahmini(ad) == "ucak" for ad in HAVAYOLLARI.values())


def test_tripcom_sse_akisi():
    """15.11: Trip.com uçuş listesini FlightListSearchSSE akışıyla veriyor (IST↔TYO 0 sonuç)."""
    import json as _j
    from pathlib import Path as _P
    from kazi.ucus.kaynaklar import tripcom as T
    j = _j.loads((_P(__file__).parent / "veri" / "tripcom_sse.json").read_text(encoding="utf-8"))
    assert T.arama_bitti_mi([{"json": j}]) and not T.arama_bitti_mi([{"json": {"x": 1}}])
    segler = T.ayristir_api([{"json": j}], "ist_tyo", "2026-12-26", 2)
    assert len(segler) == 6
    ilk = segler[0]
    assert ilk.kalkis_nokta == "IST" and ilk.varis_nokta in ("NRT", "HND") and ilk.kisi_basi > 1000
    assert any(s.firma.startswith("Türk Hava Yolları") for s in segler)          # kod değil tam ad (airlineList)
    assert any(s.aktarmalar and s.aktarmalar[0].nokta for s in segler)          # aktarmalı rota noktası


def test_yanit_jsonlari_sse_ve_json():
    from kazi.tarayici.cekirdek import yanit_jsonlari

    class R:
        def __init__(self, ct, govde):
            self.headers, self._g = {"content-type": ct}, govde

        def text(self):
            return self._g

        def json(self):
            import json as _j
            return _j.loads(self._g)

    assert yanit_jsonlari(R("application/json", '{"a": 1}')) == [{"a": 1}]
    assert yanit_jsonlari(R("text/event-stream;charset=UTF-8", 'data:{"a":1}\n\ndata: {"b":2}\n: yorum\n')) == [{"a": 1}, {"b": 2}]
    assert yanit_jsonlari(R("text/html", "<html>")) == []
