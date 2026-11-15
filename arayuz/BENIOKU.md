# kazi-arayuz — Masaüstü Arayüzü (v0.0.1)

`kazi-asistan` kişisel veri kazıma asistanının **Rust + egui/eframe (0.33)** tabanlı yerel masaüstü arayüzüdür.
Wayland ve X11 üzerinde tek ikili dosya olarak çalışır, çekirdek servisine (`kazi-cekirdek`, varsayılan `http://127.0.0.1:8770`) HTTP/JSON üzerinden bağlanır.

## Derleme ve Test

```bash
export PATH="$HOME/.cargo/bin:$PATH"
cd kazi-asistan/arayuz

# Birim ve sözleşme ayrıştırma testleri
cargo test

# Yayın (release) derlemesi
cargo build --release
```

## Çalıştırma

### 1. Canlı Çekirdek ile Çalıştırma
```bash
./target/release/kazi-arayuz
# veya farklı bir API adresiyle:
./target/release/kazi-arayuz --api http://127.0.0.1:8770
```

### 2. Sahte Veri Klasörüyle Çalıştırma (Çevrimdışı / Test Modu)
```bash
./target/release/kazi-arayuz --sahte ornek
```
Sahte modda veriler belirtilen klasördeki `pano.json`, `detay_<urun_id>.json`, `siteler.json` ve `seyahat.json` dosyalarından okunur.

## Ekranlar

1. **Takip Listesi (`/pano`):**
   - Takip edilen ürünleri, hedef fiyatı, en iyi sepet fiyatını, satıcıyı, yıldız etiketini (`★★★ Hedef` altın, `★ Kelepir` turuncu), piyasa ortalamasını, ortalamaya göre yüzde farkı (`−%8,6`), teklif adedini ve son tarama zamanını listeler.
   - Altta **Sistem toplamı** ve fiyatı eksik parçalar yer alır.
   - Herhangi bir ürün satırına tıklandığında **Ürün Detayı** ekranı açılır.
2. **Ürün Detayı (`/urunler/{id}/detay`):**
   - Üstte ürün adı, hedef fiyat, piyasa ortalaması, medyan, toplam teklif adedi ve eşleşme kuralları (dahil/hariç) yer alır.
   - **Süzgeçler:** Yalnız teyitli, yalnız stokta ve site seçimi.
   - **Sıralanabilir Teklif Tablosu:** Site, satıcı, sepet fiyatı, liste fiyatı, peşin fiyatına taksit, stok durumu, kaynak katmanı rozeti (`K0`/`K1`/`K2`/`K3`), teyit durumu, yıldız, gözlem zamanı ve `[Aç ↪]` bağlantısı (`xdg-open`).
   - **Açılır Satır Detayları (`CollapsingHeader`):** Tam ürün başlığı, tam URL, banka taksit tablosu (banka, taksit sayısı, aylık, toplam, vade farkı %), yıldız gerekçesi ve kanıt ekran görüntüsü yolu.
   - **Fiyat Geçmişi Grafiği (`egui_plot`):** Günlük en düşük fiyat, ortalama fiyat ve yatay hedef fiyat çizgisi.
3. **Uçuşlar (`/seyahat`):**
   - **Yolcular Kartı:** Yolcu adları, uyrukları ve vize/ikamet izinleri (`durum: "varsayim"` olanlar `⚠ varsayım` rozetiyle vurgulanır).
   - **Bacak Tabloları:** Her bacak için tarih aralığı, yolcular, direkt/aktarmalı yıldız eşikleri ve kişi başı fiyata göre artan seçenek tablosu (tarih, kalkış→varış saati, rota/aktarmalar, süre, firma, kişi başı, toplam, yıldız, `⚠` uyarı ipucu, kaynak, link).
   - **Elenenler (`Elenenler (N)`):** Transit/vize kuralları nedeniyle elenen uçuşların açılır listesi ve gerekçeleri.
   - **Yan Turlar:** Tokyo ↔ Seul gibi esnek gidiş-dönüş çiftleri, gece sayısı, toplam süre ve kişi başı toplam tutar.
4. **Site Sağlığı (`/siteler`):**
   - Sitelerin sağlık durumu (Yeşil / Sarı / Kırmızı), tercih edilen tarayıcı katmanı (`K0`–`K3`), son başarı/hata zamanları ve son 24 saatteki başarılı/hatalı/teklif sayıları.
5. **Ara / Tanımla (`POST /tanimla`):**
   - Serbest metin kutusu ve tür seçimi (Ürün / Uçuş / Bilet / Diğer).
   - Dönen katalog aday kartları için **Evet, bu** (`POST /tanimla/{istek_id}/onayla`) ve **Hayır** düğmeleri; aday yoksa bilgilendirme mesajı.
6. **Ayarlar:**
   - API adresi, otomatik yenileme sıklığı (saniye) ve Koyu / Açık tema seçimi.
   - Ayarlar `~/.config/kazi/arayuz.json` dosyasına kalıcı olarak kaydedilir.
