"""SARTNAME §13: plan, yolcular/transit, bağlantı payı, zincirleme."""
from datetime import date

import pytest

from kazi.seyahat.baglanti import BaglantiAyar, gerekli_sure_dk, transfer_dk, uygun_mu
from kazi.seyahat.plan import (Durak, Plan, Yolcu, bacaklar, ice_aktar_eski_plan, ters_rota, varsayilan_araclar,
                               varsayilan_yolcular)
from kazi.seyahat.segment import Aktarma, Segment, ayri_bilet_tahmini, havayolu_ulkeleri, nokta_ulke
from kazi.seyahat.transit import denetle
from kazi.seyahat.zincir import bacak_suz, en_iyi_zincirler

ALI = varsayilan_yolcular()[0]
# kural motoru testi: uydurma "XX" uyruklu yolcu (tests/veri/transit_test_kurallari.json: BAE/Çin/Kore yasak, Katar airside, JP vize)
AYSE = Yolcu("ayse", "Ayşe", ["XX"], [
    {"ulke": "TR", "tur": "ikamet", "bitis": "2027-06-30", "durum": "gercek", "not": "örnek"},
    {"ulke": "JP", "tur": "vize", "bitis": None, "durum": "varsayim", "not": "örnek: henüz alınmadı"}])


def seg(id, kalkis, varis, k, v, fiyat=10000, arac="ucak", aktarmalar=(), firma=None, aktarma_sayisi=None):
    return Segment(id=id, arac=arac, kalkis_nokta=kalkis, varis_nokta=varis, kalkis=k, varis=v, kisi_basi=fiyat,
                   aktarmalar=list(aktarmalar), firma=firma, aktarma_sayisi=aktarma_sayisi)


def ucus(id, aktarmalar=(), firma=None, aktarma_sayisi=None, fiyat=30000, k="2026-12-27T10:00:00+03:00",
         v="2026-12-28T12:00:00+09:00", kalkis="IST", varis="NRT"):
    return seg(id, kalkis, varis, k, v, fiyat, aktarmalar=aktarmalar, firma=firma, aktarma_sayisi=aktarma_sayisi)


# ---------------- veri
def test_nokta_ulkeleri():
    assert nokta_ulke("DXB") == "AE" and nokta_ulke("PVG") == "CN" and nokta_ulke("SOGUTLUCE") == "TR"
    assert nokta_ulke("NRT") == "JP" and nokta_ulke("ZZZ") is None


def test_havayolu_ulkeleri_ad_ve_kod():
    assert havayolu_ulkeleri("China Eastern") == {"CN"} and havayolu_ulkeleri("Cathay Pacific") == {"HK"}
    assert havayolu_ulkeleri("EK, QR") == {"AE", "QA"} and havayolu_ulkeleri("Turkish Airlines") == set()


def test_ayri_bilet_tahmini():
    assert ayri_bilet_tahmini("Peach + Jetstar", 1) in ("peach", "jetstar")
    assert ayri_bilet_tahmini("Peach", 0) is None and ayri_bilet_tahmini("Qatar Airways", 1) is None


# ---------------- plan
def test_varsayilan_araclar():
    assert varsayilan_araclar("Ankara", "İstanbul") == ["ucak", "yht", "otobus"]
    assert varsayilan_araclar("İstanbul", "Tokyo") == ["ucak"]
    assert varsayilan_araclar("Eskişehir", "İstanbul") == ["yht", "otobus"]  # Eskişehir'de havalimanı kaydı yok


def test_bacaklar_esneklik_ve_gecis_noktasi():
    p = Plan([Durak("Ankara", date(2026, 12, 27), 1), Durak("İstanbul", gecis_noktasi=True), Durak("Tokyo")],
             [ALI, AYSE])
    b = bacaklar(p)
    assert [(x.kalkis_sehir, x.varis_sehir) for x in b] == [("Ankara", "İstanbul"), ("İstanbul", "Tokyo")]
    assert b[0].en_erken == date(2026, 12, 26) and b[0].en_gec == date(2026, 12, 28)
    assert b[1].en_erken == date(2026, 12, 26)  # geçiş noktası: aynı gün aralığı
    assert b[1].gecis_sonrasi is True and b[0].gecis_sonrasi is False
    assert b[0].araclar == ["ucak", "yht", "otobus"] and [y.id for y in b[1].yolcular] == ["ali", "ayse"]


def test_ters_rota_aynalar():
    p = Plan([Durak("Ankara", date(2026, 12, 27)), Durak("İstanbul", gecis_noktasi=True), Durak("Tokyo")],
             [ALI, AYSE], bacak_yolculari={0: ["ali"], 1: ["ali", "ayse"]}, bacak_araclari={0: ["yht"]})
    t = ters_rota(p, date(2027, 1, 6), 2)
    assert [d.sehir for d in t.duraklar] == ["Tokyo", "İstanbul", "Ankara"]
    assert t.duraklar[0].tarih == date(2027, 1, 6) and t.duraklar[1].gecis_noktasi
    assert t.bacak_yolculari == {1: ["ali"], 0: ["ali", "ayse"]} and t.bacak_araclari == {1: ["yht"]}
    b = bacaklar(t)
    assert b[0].en_erken == date(2027, 1, 4) and b[1].en_erken == date(2027, 1, 4)


def test_eski_plan_ice_aktarma_yan_tur():
    p = ice_aktar_eski_plan()
    b = bacaklar(p)
    assert [x.id for x in b] == ["ist_tyo", "tyo_ist"]
    assert [(x.kalkis_sehir, x.varis_sehir) for x in b] == [("İstanbul", "Tokyo"), ("Tokyo", "İstanbul")]
    assert [[y.id for y in x.yolcular] for x in b] == [["ali", "ayse"], ["ali", "ayse"]]
    assert b[0].en_erken == date(2026, 12, 25) and b[0].en_gec == date(2026, 12, 29)
    assert b[1].en_erken == date(2027, 1, 4) and b[1].en_gec == date(2027, 1, 8)
    (yt,) = p.yan_turlar
    assert (yt.ana_sehir, yt.hedef_sehir, yt.yolcu_idler, yt.kalis_gece) == ("Tokyo", "Seul", ["ali"], 2)
    assert (yt.en_erken_gidis, yt.en_gec_donus) == (date(2026, 12, 28), date(2027, 1, 5))
    assert (yt.gidis_bacak_id, yt.donus_bacak_id) == ("tyo_sel", "sel_tyo")
    es = next(y for y in p.yolcular if y.id == "ayse")
    assert es.uyruklar == ["TR"] and es.izinler == []          # örnek yolcular nötr (ikisi de TR)


# ---------------- transit
def test_kisitli_uyruklu_dxb_elenir_doh_gecer():
    t = denetle(ucus("a", [Aktarma("DXB")], "Emirates"), [ALI, AYSE])
    assert not t.gecer and "2. yolcu (Ayşe): DXB aktarması — XX uyruğu için BAE yasak" in t.gerekceler
    t = denetle(ucus("b", [Aktarma("DOH")], "Qatar Airways"), [ALI, AYSE])
    assert t.gecer and any("Katar transit" in u for u in t.uyarilar)


def test_kore_aktarmasi_kisitli_yolcuyla_elenir_solo_gecer():
    s = ucus("k", [Aktarma("ICN")], "Korean Air")
    assert not denetle(s, [ALI, AYSE]).gecer
    assert denetle(s, [ALI]).gecer


def test_cin_ve_hong_kong_yasak():
    assert not denetle(ucus("c", [Aktarma("PVG")]), [AYSE]).gecer
    assert not denetle(ucus("h", [Aktarma("HKG")], "Cathay Pacific"), [AYSE]).gecer


def test_nokta_bilinmeyince_havayolu_merkezi_varsayilir():
    t = denetle(ucus("x", [], "China Eastern", aktarma_sayisi=1), [ALI, AYSE])
    assert not t.gecer and any("havayolu merkezi" in g for g in t.gerekceler)
    t = denetle(ucus("y", [], "Turkish Airlines", aktarma_sayisi=1), [ALI, AYSE])
    assert t.gecer and any("aktarma noktası kaynakta yok" in u for u in t.uyarilar)


def test_airside_ayri_bilet_vize_ister():
    t = denetle(ucus("q", [Aktarma("DOH", ayri_bilet=True)], "Peach"), [AYSE])
    assert not t.gecer and "bagaj için ülkeye giriş" in t.gerekceler[0]
    assert denetle(ucus("q2", [Aktarma("DOH", ayri_bilet=True)]), [ALI]).gecer  # TR: Katar serbest


def test_vize_gerekli_izin_yok_elenir_izin_varsa_gecer():
    yabanci = Yolcu("y", "Y", ["XX"])
    s = ucus("j", [Aktarma("NRT")], varis="BKK", k="2027-01-03T10:00:00+09:00", v="2027-01-03T20:00:00+07:00")
    assert not denetle(s, [yabanci]).gecer
    assert denetle(s, [Yolcu("y", "Y", ["XX"], [{"ulke": "JP", "tur": "vize", "bitis": "2027-06-01", "durum": "gercek"}])]).gecer


def test_izin_bitisi_tarihten_once_elenir():
    y = Yolcu("y", "Y", ["XX"], [{"ulke": "JP", "tur": "vize", "bitis": "2026-12-01", "durum": "gercek"}])
    t = denetle(ucus("j", [Aktarma("HND")], varis="ICN"), [y])
    assert not t.gecer and "2026-12-01" in t.gerekceler[0]


def test_ayse_seul_yan_turuna_giremez_ali_gider():
    # Ayşe'nin Kore (Seul) vizesi yok → Seul'e varış Ayşe için elenir, Ali için geçer
    s = ucus("n", [], varis="ICN", kalkis="NRT", k="2027-01-03T10:00:00+09:00", v="2027-01-03T12:30:00+09:00")
    t = denetle(s, [ALI, AYSE])
    assert not t.gecer and "2. yolcu (Ayşe): Kore girişi — vize/izin yok" in t.gerekceler
    assert denetle(s, [ALI]).gecer


def test_varis_kurali_bilinmeyen_ulke_yalniz_uyari():
    t = denetle(ucus("b", [], kalkis="IST", varis="BKK"), [AYSE])
    assert t.gecer and any("Tayland girişi için izin kaydı yok" in u for u in t.uyarilar)


def test_es_tokyo_ve_istanbul_varisi_gecer():
    assert denetle(ucus("t", [], kalkis="IST", varis="NRT"), [ALI, AYSE]).gecer           # JP varsayım izni
    assert denetle(ucus("i", [], kalkis="NRT", varis="IST", k="2027-01-06T10:00:00+09:00",
                        v="2027-01-06T18:00:00+03:00"), [ALI, AYSE]).gecer                 # TR ikamet 30.06.2027


def test_jp_varsayim_tokyo_varisi_ve_nrt_aktarmasi():
    s = ucus("n", [Aktarma("NRT")], kalkis="IST", varis="HND")
    t = denetle(s, [ALI, AYSE])
    assert t.gecer
    assert "2. yolcu (Ayşe): ⚠ Japonya vize varsayım (henüz alınmadı)" in t.uyarilar


def test_tablo_disi_ulke_uyari_ama_gecer():
    t = denetle(ucus("t", [Aktarma("TAS")], "Uzbekistan Airways"), [AYSE])
    assert t.gecer and any("Özbekistan transit kuralını teyit et" in u for u in t.uyarilar)


def test_vatandasi_oldugu_ulke_serbest():
    t = denetle(ucus("i", [Aktarma("IKA")], kalkis="IST", varis="NRT"), [AYSE])
    assert t.gecer


# ---------------- bağlantı
def test_sartname_ornegi_yht_ist_yetismez():
    yht = seg("yht", "ANKARA_YHT", "SOGUTLUCE", "2026-12-27T05:30:00+03:00", "2026-12-27T10:00:00+03:00", 800, "yht")
    ist = seg("ist", "IST", "NRT", "2026-12-27T14:00:00+03:00", "2026-12-28T08:00:00+09:00")
    ok, gerekli, bosluk, _ = uygun_mu(yht, ist)
    assert (ok, gerekli, bosluk) == (False, 360, 240)


def test_sartname_ornegi_saw_yetisir():
    yht = seg("yht", "ANKARA_YHT", "SOGUTLUCE", "2026-12-27T05:30:00+03:00", "2026-12-27T10:00:00+03:00", 800, "yht")
    saw = seg("saw", "SAW", "NRT", "2026-12-27T15:30:00+03:00", "2026-12-28T10:00:00+09:00")
    ok, gerekli, bosluk, _ = uygun_mu(yht, saw)
    assert ok and gerekli == 315 and bosluk == 330


def test_ic_hat_ve_ayar():
    a = seg("a", "ESB", "IST", "2026-12-27T08:00:00+03:00", "2026-12-27T09:10:00+03:00", 2000)
    b = seg("b", "IST", "NRT", "2026-12-27T14:00:00+03:00", "2026-12-28T08:00:00+09:00")
    assert not a.dis_hat and b.dis_hat
    assert gerekli_sure_dk(a, b)[0] == 45 + 0 + 180 + 60
    assert gerekli_sure_dk(a, b, ayar=BaglantiAyar(kabul_dis_hat=120), ayri_bilet=False)[0] == 165


def test_transfer_bilinmeyen_varsayilan_ve_iki_yonlu():
    assert transfer_dk("SAW", "SOGUTLUCE") == (45, True) and transfer_dk("IST", "IST") == (0, True)
    assert transfer_dk("ADB", "IST") == (120, False)


def test_gece_uyarisi():
    a = seg("a", "ANKARA_YHT", "SOGUTLUCE", "2026-12-26T22:00:00+03:00", "2026-12-27T02:00:00+03:00", 800, "yht")
    b = seg("b", "SAW", "NRT", "2026-12-27T10:00:00+03:00", "2026-12-28T05:00:00+09:00")
    ok, *_, u = uygun_mu(a, b)
    assert ok and any("gece" in x for x in u)


# ---------------- zincir
def test_bacak_suz_gerekceler_ve_siralama():
    p = Plan([Durak("İstanbul", date(2026, 12, 27), 1), Durak("Tokyo")], [ALI, AYSE])
    b = bacaklar(p)[0]
    s = bacak_suz(b, [ucus("pahali", fiyat=50000), ucus("dxb", [Aktarma("DXB")], fiyat=20000),
                      ucus("ucuz", [Aktarma("DOH")], fiyat=25000),
                      ucus("gec", fiyat=1000, k="2027-01-10T10:00:00+03:00", v="2027-01-11T10:00:00+09:00")])
    assert [x[0].id for x in s.gecen] == ["ucuz", "pahali"]
    assert {x[0].id for x in s.elenen} == {"dxb", "gec"}


def test_zincir_siralama_ve_uygunsuz_baglanti_elenir():
    yht = (seg("yht", "ANKARA_YHT", "SOGUTLUCE", "2026-12-27T05:30:00+03:00", "2026-12-27T10:00:00+03:00", 800, "yht"), [])
    otobus = (seg("bus", "ASTI", "ESENLER", "2026-12-26T23:00:00+03:00", "2026-12-27T05:30:00+03:00", 600, "otobus"), [])
    ist = (seg("ist", "IST", "NRT", "2026-12-27T14:00:00+03:00", "2026-12-28T08:00:00+09:00", 30000), [])
    saw = (seg("saw", "SAW", "NRT", "2026-12-27T15:30:00+03:00", "2026-12-28T10:00:00+09:00", 31000), [])
    z = en_iyi_zincirler([[yht, otobus], [ist, saw]])
    yollar = [[s.id for s in x.segmentler] for x in z]
    assert ["yht", "ist"] not in yollar           # 360 dk gerekli, 240 var
    assert yollar[0] == ["bus", "ist"]             # 600+30000 en ucuz; otobüs 05:30 → IST 14:00 yetişir (60+50+180+60)
    assert ["yht", "saw"] in yollar and z[0].kisi_basi_toplam == 30600


def test_zincir_kalisli_bacak_yalniz_kronoloji():
    a = (seg("a", "IST", "NRT", "2026-12-27T14:00:00+03:00", "2026-12-28T08:00:00+09:00"), [])
    b = (seg("b", "NRT", "ICN", "2027-01-03T10:00:00+09:00", "2027-01-03T12:30:00+09:00", 6000), [])
    eski = (seg("c", "NRT", "ICN", "2026-12-20T10:00:00+09:00", "2026-12-20T12:30:00+09:00", 5000), [])
    z = en_iyi_zincirler([[a], [b, eski]], baglantili=[False, False])
    assert [[s.id for s in x.segmentler] for x in z] == [["a", "b"]]


def test_zincir_bos_bacak():
    assert en_iyi_zincirler([[], []]) == []


def test_bilinmeyen_aktarma_noktasi():
    t = denetle(ucus("m", [Aktarma("ZZQ", ayri_bilet=True)], "MIAT"), [ALI, AYSE])
    assert not t.gecer and "ülkesi bilinmiyor" in t.gerekceler[0]
    # UBN artık tabloda (Moğolistan, OurAirports): ayrı bilette kural bilinmiyor → bagaj için giriş gerekir → elenir
    t = denetle(ucus("m3", [Aktarma("UBN", ayri_bilet=True)], "MIAT"), [ALI])
    assert not t.gecer and "UBN aktarması — ayrı bilet" in t.gerekceler[0]
    t = denetle(ucus("m2", [Aktarma("QQQ")]), [ALI])
    assert t.gecer and any("ülkesi bilinmiyor" in u for u in t.uyarilar)


# ---------------- yan tur (14.11: Tokyo→Seul→Tokyo, Ali solo, 2 gece)
from kazi.seyahat.yan_tur import YanTur, yan_tur_ciftleri  # noqa: E402

YT = YanTur("Tokyo", "Seul", ["ali"], 2, 0, date(2026, 12, 28), date(2027, 1, 5))


def _ts(id, gun, fiyat, saat="10:00", varis_saat="12:30", ters=False):
    k, v = ("ICN", "NRT") if ters else ("NRT", "ICN")
    return (seg(id, k, v, f"{gun}T{saat}:00+09:00", f"{gun}T{varis_saat}:00+09:00", fiyat), [])


def test_yan_tur_iki_gece_ve_en_ucuz_cift():
    g = [_ts("g31", "2026-12-31", 7000), _ts("g02", "2027-01-02", 6000), _ts("g04", "2027-01-04", 3000)]
    d = [_ts("d02", "2027-01-02", 7500, ters=True), _ts("d04", "2027-01-04", 6500, ters=True),
         _ts("d06", "2027-01-06", 1000, ters=True), _ts("d03", "2027-01-03", 100, ters=True)]
    c = yan_tur_ciftleri(g, d, YT)
    assert [(x.gidis.id, x.donus.id) for x in c] == [("g02", "d04"), ("g31", "d02")]  # g04+d06: pencere dışı (06.01)
    assert c[0].gece == 2 and c[0].kisi_basi_toplam == 12500


def test_yan_tur_kalis_esnekligi():
    g = [_ts("g31", "2026-12-31", 7000)]
    d = [_ts("d02", "2027-01-02", 7500, ters=True), _ts("d03", "2027-01-03", 5000, ters=True)]
    yt = YanTur("Tokyo", "Seul", ["ali"], 2, 1, date(2026, 12, 28), date(2027, 1, 5))
    assert [x.donus.id for x in yan_tur_ciftleri(g, d, yt)] == ["d03", "d02"]


def test_yan_tur_ana_ucuslarla_cakisma():
    gelis = seg("gel", "IST", "NRT", "2026-12-28T01:00:00+03:00", "2026-12-28T19:00:00+09:00", 30000)
    ayrilis = seg("ayr", "NRT", "IST", "2027-01-04T21:00:00+09:00", "2027-01-05T04:00:00+03:00", 30000)
    g = [_ts("g28", "2026-12-28", 5000, "20:00", "22:30"), _ts("g29", "2026-12-29", 6000)]
    d = [_ts("d30", "2026-12-30", 5000, "12:00", "14:30", ters=True), _ts("d31", "2026-12-31", 6000, ters=True),
         _ts("d04", "2027-01-04", 1, "10:00", "12:30", ters=True)]
    c = yan_tur_ciftleri(g, d, YT, ana_varis=gelis, ana_kalkis=ayrilis)
    ciftler = [(x.gidis.id, x.donus.id) for x in c]
    assert ("g28", "d30") not in ciftler   # 19:00 varış → 20:00 kalkış: bağlantı payı yetmez
    assert ciftler == [("g29", "d31")]
    # d04 12:30 NRT → 21:00 aynı gün NRT dış hat: 60 + 0 + 180 + 60 = 300 dk gerekli, 510 dk var → uygun ama uyarılı
    g2 = [_ts("g02", "2027-01-02", 4000)]
    c2 = yan_tur_ciftleri(g2, d, YT, ana_kalkis=ayrilis)
    assert [(x.gidis.id, x.donus.id) for x in c2] == [("g02", "d04")]
    assert any("aynı gün bağlantı" in u for u in c2[0].uyarilar)


def test_bilinmeyen_aktarma_ulkesi_kisitli_uyruk_icin_elenir():
    """C1 #4: kısıtlı (XX) uyruklu yolcu için ülkesi çözülemeyen aktarma güvensiz sayılır; TR yolcu için yalnız uyarı."""
    from kazi.seyahat.plan import Yolcu
    from kazi.seyahat.segment import Aktarma, Segment
    from kazi.seyahat.transit import denetle
    s = Segment(id="x", arac="ucak", kalkis_nokta="IST", varis_nokta="NRT", kalkis="2026-12-28T10:00:00+03:00",
                varis="2026-12-29T12:00:00+09:00", aktarmalar=[Aktarma(nokta="ZZZ")], firma="Bilinmeyen Hava",
                kisi_basi=10000, toplam=10000)
    es = Yolcu(id="ayse", ad="Ayşe", uyruklar=["XX"], izinler=[{"ulke": "JP", "tur": "vize", "durum": "varsayim"}])
    ben = Yolcu(id="ben", ad="Ben", uyruklar=["TR"])
    assert denetle(s, [ben]).gecer is True
    assert denetle(s, [es]).gecer is False
