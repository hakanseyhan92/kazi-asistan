# kazi-cekirdek v0.0.1

Kazı Asistanı'nın Python çekirdeği. Genel bakış: [`../README.md`](../README.md).

```bash
cd ~/kazi-asistan/cekirdek
uv run kazi-cekirdek serve            # 127.0.0.1:8770 (ayar: ~/.config/kazi/ayar.toml, veri: ~/.local/share/kazi/)
uv run kazi-cekirdek openapi --cikti ../api/openapi.json
uv run --group dev pytest -q tests    # testler
```

| Uç nokta | İş |
|---|---|
| GET /saglik | sürüm, çalışma süresi, db yolu |
| GET /siteler · /urunler · /urunler/{id} | listeler |
| GET /urunler/{id}/teklifler?gecmis=0\|1 | site+satıcı başına son teklif / tüm geçmiş |
| POST /tanimla {metin, tur} | "Bu mu?" adayları (A3) |
| POST /tanimla/{istek_id}/onayla | ürün kimliği oluşturur |
| POST /tara {urun_id} | tarama görevi (A2/A4) |
| WS /olaylar | canlı olay akışı |

Modüller: `kurallar/yildiz.py` (★ Kelepir / ★★★ Hedef, uçuş eşikleri, az aktarma kuralı) ·
`seyahat/` (plan, yolcu/transit, bağlantı payı, zincir; veri dosyaları `seyahat/veri/*.json`).
