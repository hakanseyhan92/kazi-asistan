# Çekirdek ↔ Arayüz API sözleşmesi (v0.0.1)

Taban adres: `http://127.0.0.1:8770` (arayüz ayarlardan değiştirebilir). Tüm gövdeler JSON (UTF-8).
Zamanlar ISO-8601, saat dilimli (`2026-11-14T14:35:07+03:00`). Para: TL, float. Hata gövdesi her zaman `{"hata": str, "ayrinti": any}`.
`null` olabilen alanlar `?` ile işaretli. Arayüz bilinmeyen ek alanları YOK SAYMALI (ileride alan eklenecek).

## GET /saglik
```json
{"surum": 1, "calisma_suresi_sn": 12, "db_yolu": "~/.local/share/kazi/kazi.db", "cekirdek_surumu": "0.0.1"}
```

## GET /pano  — "Takip Listesi" ekranı (tek çağrıda her şey)
```json
{
  "guncelleme": "2026-11-14T15:20:00+03:00",
  "urunler": [
    {
      "id": "gpu_rtx5080", "ad": "Ekran kartı RTX 5080", "kategori": "gpu", "sistemde": true,
      "hedef_fiyat": 110000.0,
      "ozet": {"ortalama": 124350.0, "medyan": 122849.0, "adet": 41},
      "en_iyi": {                      // en ucuz GEÇERLİ teklif (yoksa null)
        "teklif_id": 812, "site": "trendyol", "satici": "GarajOnline", "baslik": "Inno3D RTX 5080 X3 ...",
        "sepet_fiyati": 111549.03, "liste_fiyati": 114999.0, "url": "https://...", "gozlem_zamani": "...",
        "teyit_durumu": "teyitli", "hedef_altinda": false,
        "taksit": {"pesin_fiyatina": 6, "tablo": []}
      },
      "son_tarama": "2026-11-14T15:09:00+03:00"
    }
  ],
  "sistem_toplami": {"tutar": 261800.0, "eksik": []}     // sistemde=true ürünlerin en iyi fiyat toplamı; eksik = fiyatı olmayan ürün adları
}
```

## GET /urunler/{id}/detay  — "Ürün Detayı" ekranı
```json
{
  "urun": {"id": "gpu_rtx5080", "ad": "...", "kategori": "gpu", "hedef_fiyat": 110000.0, "kurallar": {"dahil": ["..."], "haric": ["..."]}},
  "ozet": {"ortalama": 124350.0, "medyan": 122849.0, "adet": 41},
  "teklifler": [                       // site+satıcı başına SON gözlem, sepet fiyatına göre artan
    {
      "teklif_id": 812, "site": "trendyol", "satici": "GarajOnline", "baslik": "...", "url": "https://...",
      "sepet_fiyati": 111549.03, "liste_fiyati": 114999.0, "stok": true,
      "taksit": {"pesin_fiyatina": 9, "tablo": [{"banka": "Bonus", "taksit_sayisi": 12, "aylik": 10500.0, "toplam": 126000.0, "vade_farki_yuzde": 13.0}]},
      "kaynak_katmani": "K2",          // K0 = HTTP, K1 = Chromium, K2 = Brave, K3 = kullanıcı
      "gozlem_zamani": "...", "teyit_durumu": "teyitli", "teyit_zamani": "?", "kanit_goruntusu": "?",
      "hedef_altinda": false           // teyitli ve fiyat ≤ kullanıcının hedef fiyatı
    }
  ],
  "gecmis": [{"gun": "2026-11-14", "en_dusuk": 111549.0, "ortalama": 124000.0}]   // fiyat geçmişi grafiği
}
```
`teyit_durumu`: `bekliyor | teyitli | supheli | urun_farkli | stok_yok | fiyat_degisti`. Yıldız ve akıllı maliyet 14.11'de KALDIRILDI.
`taksit.tablo`: kart/banka bazlı taksit seçenekleri (`banka`, `taksit_sayisi`, `aylik`, `toplam`, `vade_farki_yuzde`; 0 = vade farksız).
Şu an boş gelir — doldurma görevi A5 (ürün sayfası taksit tablosu kazıma). `pesin_fiyatina`: vade farksız en yüksek taksit sayısı.

## GET /siteler  — "Site Sağlığı" ekranı
```json
[{"ad": "trendyol", "katman_tercihi": "K2", "son_basari": "...", "son_hata": "?", "hata_turu": "?", "etkin": true,
  "son_24s": {"basarili": 12, "hatali": 0, "teklif": 304}}]
```

## GET /seyahat  — "Uçuşlar" ekranı
```json
{
  "guncelleme": "...",
  "yolcular": [{"id": "ayse", "ad": "Ayşe", "uyruklar": ["TR"], "izinler": []}],
  "bacaklar": [
    {
      "id": "ist_tyo", "baslik": "İstanbul → Tokyo", "en_erken": "2026-12-25", "en_gec": "2026-12-29",
      "yolcular": ["ali", "ayse"],
      "secenekler": [                  // kişi başı fiyata göre artan
        {
          "id": "u123", "tarih": "2026-12-27", "kalkis": "2026-12-27T01:55:00+03:00", "varis": "2026-12-27T19:40:00+09:00",
          "kalkis_nokta": "IST", "varis_nokta": "HND", "aktarmalar": ["DOH"], "aktarma_sayisi": 1, "sure_dk": 1185,
          "firma": "Qatar Airways", "kisi_basi": 31541.0, "toplam": 63082.0, "kaynak": "kayak", "link": "https://...",
          "gozlem_zamani": "...",
          "uyarilar": ["2. yolcu (Ayşe): Katar transit: ..."], "ayri_bilet": false, "saat_bilinmiyor": false
        }
      ],
      "elenen_sayisi": 52, "elenen_ornekleri": [{"firma": "Emirates", "rota": "IST>DXB>NRT", "gerekce": "2. yolcu (Ayşe): XX aktarması — örnek transit kuralı"}]
    }
  ],
  "yan_turlar": [
    {
      "baslik": "Tokyo ↔ Seul (yalnız Ali, 2 gece)", "pencere": ["2026-12-28", "2027-01-05"], "yolcular": ["ali"],
      "ciftler": [{"gidis": {"...secenek": "yukarıdaki biçim"}, "donus": {"...": "..."}, "gece": 2, "kisi_basi_toplam": 16317.0, "toplam_sure_dk": 230, "uyarilar": []}]
    }
  ]
}
```
Arayüzde uçuş seçeneği için olmazsa olmaz sütunlar: **tarih, kalkış→varış saati, rota (aktarmalar), süre, firma, kişi başı, toplam, uyarı (⚠ simgesi + üstüne gelince metin), kaynak, link**.

## POST /tanimla  `{"metin": "rtx 5080 en ucuz", "tur": "urun|ucus|bilet|diger"}`
→ `{"istek_id": "a1b2c3", "adaylar": [{"id": "...", "baslik": "...", "kaynak": "akakce", "url": "?", "gorsel": "?", "mpn": "?", "ozellikler": {}, "fiyat_araligi": {"alt": 1.0, "ust": 2.0}}]}`
(v0.0.1'de adaylar boş dönebilir: arayüz "Aday bulunamadı — tanımlama A3 aşamasında gelecek" yazar.)

## POST /tanimla/{istek_id}/onayla  `{"aday_idler": ["..."], "kanonik_ad": "?", "mpn": "?"}` → ürün kimliği
## POST /tara `{"urun_id": "..."}` → `{"gorev_id": "..."}`
## WS /olaylar → `{"id": 1, "tur": "tarama_basladi", "zaman": "...", "urun_id": "?", "gorev_id": "?", "mesaj": "?", "veri": {}}`

## Seyahat planı — arayüzden düzenlenir (14.11, SARTNAME §13.1–13.2, §13.5)

| Uç | Açıklama |
|---|---|
| `GET /seyahat/plan` | `{plan, onizleme, kayitli}` — kayıtlı plan (`~/.config/kazi/seyahat_plan.json`); yoksa eski plandan üretilmiş ilk plan (`kayitli: false`) |
| `POST /seyahat/plan/onizle` (gövde: plan) | Kaydetmeden doğrula + hesapla → `{hatalar: [{alan, mesaj}], bacaklar: [...], yan_turlar: [...], uyarilar: [str]}` |
| `PUT /seyahat/plan?eski_tarayiciya=true` (gövde: plan) | Doğrula (hatalıysa 422 `{hata, ayrinti: [{alan, mesaj}]}`), kaydet (önceki `.yedek`), eksik bacak id'lerini üret, uçak bacaklarını eski tarayıcının `plan.json`'una yaz → `{plan, onizleme, eski_tarayici: {yazilan, atlanan}}`; WS olayı `plan_guncellendi` |
| `GET /yerler?q=ank&azami=12` | Şehir arama (Türkçe/İngilizce ad, IATA kodu, havalimanı adı; İ/ı duyarsız) → `[{sehir, ulke, ulke_ad, kaynak: elle|ourairports, noktalar: [{kod, ad, tur: havalimani|yht|otogar}]}]` |
| `GET /ulkeler` | `{ISO2: "Türkçe ad"}` (249 ülke) — uyruk ve izin seçicileri için |

Plan biçimi (`surum: 1`):
```json
{"surum":1,"ad":"Tokyo 2026-27",
 "yolcular":[{"id":"ayse","ad":"Ayşe","uyruklar":["TR"],
              "izinler":[{"ulke":"JP","tur":"vize","bitis":null,"durum":"varsayim","not":"henüz alınmadı"}]}],
 "duraklar":[{"sehir":"Ankara","noktalar":null,"tarih":"2026-12-26","esneklik":1,"kalis_gece":null,"gecis_noktasi":false},
             {"sehir":"İstanbul","tarih":null,"esneklik":0,"gecis_noktasi":true},
             {"sehir":"Tokyo","tarih":"2027-01-06","esneklik":2},
             {"sehir":"İstanbul"}],
 "bacaklar":[{"id":"ank_ist","yolcular":["ali","ayse"],"araclar":["ucak","yht","otobus"]},
             {"id":"ist_tyo","yolcular":["ali","ayse"],"araclar":["ucak"]},
             {"id":"tyo_ist","yolcular":["ali","ayse"],"araclar":["ucak"]}],
 "yan_turlar":[{"ana_sehir":"Tokyo","hedef_sehir":"Seul","yolcular":["ali"],"kalis_gece":2,"kalis_esneklik":0,
                "en_erken_gidis":"2026-12-28","en_gec_donus":"2027-01-05","gidis_id":"tyo_sel","donus_id":"sel_tyo"}]}
```
Kurallar: `len(bacaklar) == len(duraklar) − 1`; i. bacak = `duraklar[i] → duraklar[i+1]`. Bir durağın `tarih`i o şehirden **kalkış** tarihidir
(son durak tarihsiz). `gecis_noktasi: true` = kalmadan aktarma (tarihi önceki duraktan gelir). `esneklik` 0–3 gün. Dönüş = rotanın tersi,
aynı `duraklar` listesine eklenir. Ön izlemedeki `yolcu_denetimi[].durum`:
`ok | uyari | engel` (engel = o yolcuyla bu bacağın tüm seçenekleri elenir; ör. vizesi olmayan yolcu + varış ülkesi).

## Takip listesi işlemleri, alarm fiyatı, Telegram (14.11 akşam)
| Uç | Açıklama |
|---|---|
| `POST /takip/cikar` `{"urun_idler": [...]}` | Seçili ürünler takip listesinden düşer (geri alınabilir; toplamdan da çıkar). Yanıt: güncel `/pano` |
| `POST /takip/geri_al` `{"urun_idler": [...]}` | Takibe geri al. Yanıt: güncel `/pano` |
| `PUT /urunler/{id}/alarm` `{"alarm_fiyat": 110000 \| null}` | Alarm fiyatı. Kaydedilince hemen denetlenir → `{urun_id, alarm_fiyat, hemen_gonderilen: [...]}` |
| `GET /ayarlar/telegram` | `{etkin, bot, chat_id, token_maskeli: "1234…MNOP", token_var, kaynak: kazi\|evren-asistan\|yok, ayar_dosyasi}` (token ASLA açık gelmez) |
| `PUT /ayarlar/telegram` `{"token"?, "chat_id"?, "etkin"?}` | Boş token = mevcut korunur. Yanıt: maskeli özet |
| `POST /ayarlar/telegram/test` | `{gonderildi: bool}` |

`/pano` eklemeleri: ürün öğesinde `alarm_fiyat` (kullanıcının girdiği; yoksa eski hedef), teklifte `alarm_altinda` (sepet ≤ alarm);
kökte `takip_disi: [{id, ad, zaman}]` (geri alma listesi). Alarm kuralı: en iyi geçerli teklifin fiyatı alarm fiyatına EŞİT ya da ALTINDA →
Telegram'a anında ayrı mesaj (aynı ürün+link+fiyat için 12 saatte bir). `hedef_fiyat` geriye uyum için durur (= alarm_fiyat).

## Kategori bazlı tarama zamanlayıcısı (14.11 akşam — A6 yazar)
"Yenileme" artık GERÇEK TARAMA demektir (sitelerden fiyat çekme). Arayüz verisi yine `/pano` vb. ile okunur.
| Uç | Açıklama |
|---|---|
| `GET /tarama/plan` | Aşağıdaki biçim |
| `PUT /tarama/plan` `{"kategoriler": {"gpu": 30, "ucus": 120}}` | Aralıkları değiştir (yalnız `secenekler_dk` içinden). Yanıt: plan |
| `POST /tarama/kategori/{id}/simdi` | Elle tarama: hemen kuyruğa alır (202 `{durum: "kuyrukta"}`); son elle taramadan 10 dk geçmediyse 429 `{hata, kalan_sn}` |

```json
{"secenekler_dk": [30,35,40,…,115,120], "min_aralik_dk": 2, "manuel_bekleme_dk": 10, "simdi": "…",
 "kategoriler": [
   {"id": "gpu", "ad": "Ekran kartı", "tur": "urun", "urunler": ["gpu_rtx5080"], "aralik_dk": 30,
    "son_baslangic": "…|null", "son_bitis": "…|null", "son_sonuc": {"teklif": 33, "hata": 1, "sure_sn": 210} | null,
    "sonraki": "2026-11-14T20:35:00+03:00", "kalan_sn": 1234, "durum": "bekliyor|kuyrukta|calisiyor",
    "manuel_kilit_bitis": "…|null", "manuel_kalan_sn": 0},
   {"id": "ucus", "ad": "Uçuşlar", "tur": "ucus", "urunler": [], "aralik_dk": 120, "...": "..."}
 ]}
```
`POST /tarama/sifirla` `{"kategoriler": ["gpu"]}` (boş/eksik = hepsi) → geri sayım ŞİMDİDEN, seçili aralıkla yeniden başlar
(`tarama_kategori.sayac_sifir`; son tarama bilgisi değişmez). Şu an taranan kategoriye dokunulmaz. Yanıt: plan + `sifirlanan`,
`atlanan`; 404 bilinmeyen, 422 biçim. Plan öğelerinde `sayac_sifir` alanı (15.11).

Kurallar: kategori = takipteki ürünlerin `kategori` alanı (gpu, cpu, ram, anakart, ssd, psu, kasa, sogutucu…) + `ucus`. En kısa aralık 30 dk.
Seçenekler 30–120 dk, 5'er dk (15.11). Aralık kategorinin son taramasının BİTİŞİNDEN sayılır (bitiş yoksa/eskiyse başlangıç);
süre dolunca tarama kendiliğinden başlar. Herhangi İKİ otomatik tarama arasında en az `min_aralik_dk` (2; 15.11'e kadar 10) dk boşluk;
`sonraki` bu kaydırmayı içerir. Elle tarama 10 dk boşluk kuralını atlar ama aynı kategori için
10 dk elle tarama kilidi vardır. Aynı anda tek tarama çalışır.

### Telegram düzenli rapor (14.11 akşam)
`GET /ayarlar/telegram` ek alanları: `rapor_aralik_dk` (0 = kapalı; 30/60/120/180/360, varsayılan 60), `sessiz_saatler` (["01:00","07:00"]),
`aralik_secenekleri`. `PUT /ayarlar/telegram` bu iki alanı da kabul eder (geçersiz → 422). `POST /ayarlar/telegram/rapor` → özeti hemen gönder
`{gonderildi}`. Zamanlayıcı (kazi-zamanlayici) her 15 sn'de bir zamanı gelmişse özeti yollar: ürün başına en iyi fiyat + önceki rapora göre ↓/↑,
teyit, peşin taksit, alarm uzaklığı; seçili toplam; uçuş bacaklarının ve yan turun en ucuzu. 

## Aranan siteler — Ayarlar'dan site kapatma (15.11)
| Uç | Açıklama |
|---|---|
| `PUT /siteler/{ad}/etkin` `{"etkin": false}` | Site aramalardan çıkar: taranmaz (yürüyen taramada da sıradaki siteden itibaren), Akakçe/Cimri üzerinden gelen teklifleri elenir, kayıtlı teklifleri `/pano`, detay, ortalama ve Telegram raporunda görünmez (silinmez; `true` ile geri gelir). 422 geçersiz, 404 bilinmeyen site. Yanıt: güncel site satırı |

`GET /siteler` eklemeleri: `dogrudan` (false = kendi bağdaştırıcısı yok, yalnız toplayıcı üzerinden görülen mağaza); `son_24s` artık yeni taramaların `tarama_site_bitti` olaylarından hesaplanır (eski fiyat.db yalnız yedek).
İlk kapatılan: **sinerji** (15.11, kullanıcı: her taramada doğrulama → 6 dk bekleme, yüksek fiyat, vade farksız taksit yok).

## Yerel profil son çaresi (15.11)
`GET/PUT /ayarlar/tarama` yeni bool alan `yerel_profil_yedek` (varsayılan true; `chromium_yedek` gibi, geçersiz → 422). Brave ve görünür Chromium bot korumasına takılırsa, kullanıcının yerel Brave profilinin KLONU (`~/.var/app/com.brave.Browser/data/kazi_yerel_klon`; yalnız Local State, Preferences, çerezler, Local Storage; 6 saatte bir tazelenir; kişisel profile yazılmaz) ayrı bir Brave (CDP :9335) ile son çare denenir. Teklif/olay `yontem`/`yedek` = `yerel_brave`. Uçuş kaynakları için de aynı yedek geçerlidir. Klon yoksa (profilde çerez yok) özellik devre dışıdır.


### Hız, yedek ve doğrulama ayarları (15.11 gece)
`GET/PUT /ayarlar/tarama` ek alanları (bool, varsayılan true): `medya_engelle` (video/ses), `yazi_tipi_engelle` (web yazı tipi),
`widget_engelle` (canlı destek/anlık bildirim/kişiselleştirme betikleri: Insider, Segmentify, Intercom, Zendesk, OneSignal…),
`chromium_yedek`, `yerel_profil_yedek`. `gorsel_engelle` artık yalnız görseller. `dogrulama_bekle_sn` ∈ {0,30,60,120,300}
(varsayılan 0): otomatik taramada doğrulama sayfası çıkınca beklenecek süre (önceden sabit 300 sn).
Yerel profil yedeği: kişisel Brave profilinin yalnız çerez + Local Storage kopyası (`~/.var/app/com.brave.Browser/data/
kazi_yerel_klon`, 6 saatte bir tazelenir; Preferences/eklenti/Sync/parola KOPYALANMAZ), CDP 9335, Brave → Chromium da
engellenirse son çare. Uçuşta bir taramada engellenen kaynak o taramanın kalan bacaklarında atlanır (`tur: blok_atlandi`).
Alarm: her site bittiği anda alarm fiyatına inen en ucuz ilan Telegram'a gider ("⚡ anlık"; piyasa medyanının %20'sinden
fazla altı ise teyit sonrasına bırakılır); tekrar koruması normal alarm denetimiyle ortak. Zamanlayıcı açılışında yarım
kalmış tarama kayıtları kapatılır (`son_sonuc.kesildi: true`).

### Uçuş taraması paralel (15.11)
Uçuş taraması (bacak × tarih × kaynak) Ayarlar'daki "Aynı anda sekme" kadar pencerede paralel çalışır; bir kaynak aynı anda tek pencerede (bot güvenliği). Yerel profil yedeği havuz bitince sırayla denenir.
Her deneme için `tarama_site_bitti` olayı yazılır: `veri = {site, adet, sure, hata, tur, katman: "ucus", bacak_id, tarih}`.

## Kayıt defteri (15.11)
- `GET /kayitlar?limit=&sonra_id=&once_id=&seviye=uyari|hata&kaynak=&site=&kategori=&ara=` → `{kayitlar: [{id, zaman, seviye
  (bilgi|uyari|hata), kaynak (zamanlayici|tarama|ucus|tarayici|alarm|python), olay, kategori, site, tarayici, url, adet,
  sure_sn, mesaj, veri}], son_id}` — en yeni önce; `sonra_id` canlı izleme için yalnız daha yenileri döndürür.
- `GET /kayitlar/ozet?saat=24` → `{seviye: {bilgi,uyari,hata}, siteler, kaynaklar, kategoriler}`.
- Kaynaklar: her tarayıcı ana çerçeve gezinmesi ve belge HTTP ≥400 hatası, K0 HTTP istekleri, bot koruması/çerez/popup olayları,
  Brave başlat/kapat, her ürün×site ve uçuş bacak×tarih×kaynak işi BİTER BİTMEZ (adet, süre, en ucuz, hata), zamanlayıcı adımları,
  Python WARNING+ satırları, Telegram gönderimleri. Dosya `$XDG_DATA_HOME/kazi/gunluk.db` (ayrı SQLite, WAL), 14 gün saklanır.
- Arayüz: sol menüde Ayarlar'ın altında "📜 Kayıtlar" (Ctrl+8): seviye/kaynak/site süzgeci, arama, 2 sn'de bir canlı, satıra tıkla → ayrıntı.
