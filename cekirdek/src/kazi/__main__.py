"""KAZI Çekirdek CLI giriş noktası."""
from __future__ import annotations

import argparse
import sys
import uvicorn
from pathlib import Path

from .ayar import ayar_yukle
from .api import uygulama_olustur


def main() -> None:
    parser = argparse.ArgumentParser(prog="kazi-cekirdek")
    subparsers = parser.add_subparsers(dest="komut")

    # Serve
    serve_parser = subparsers.add_parser("serve", help="Sunucuyu başlatır")
    serve_parser.add_argument("--port", type=int, help="Dinlenecek port")

    # OpenAPI
    openapi_parser = subparsers.add_parser("openapi", help="OpenAPI JSON çıktısını üretir")
    openapi_parser.add_argument("--cikti", type=Path, help="Çıktı dosyası yolu (stdout yerine)")

    # DB-Yolu
    subparsers.add_parser("db-yolu", help="Veritabanı yolunu yazdırır")

    # Geçiş dönemi: eski evren-asistan verisi
    aktar_parser = subparsers.add_parser("eski-aktar", help="evren-asistan fiyat.db'den ürün ve teklifleri aktarır")
    aktar_parser.add_argument("--saat", type=int, default=48, help="son kaç saatin teklifleri")
    ornek_parser = subparsers.add_parser("ornek-uret", help="arayüz sahte modu için gerçek veriden JSON üretir")
    ornek_parser.add_argument("--cikti", type=Path, required=True)

    args = parser.parse_args()

    if args.komut == "serve":
        ayar = ayar_yukle()
        port = args.port or ayar.port
        host = ayar.host
        app = uygulama_olustur(ayar, seyahat_isit=True)
        uvicorn.run(app, host=host, port=port, log_level="info")

    elif args.komut == "openapi":
        ayar = ayar_yukle()
        app = uygulama_olustur(ayar)
        import json
        schema = app.openapi()
        çıktı = json.dumps(schema, indent=2, ensure_ascii=False)
        if args.cikti:
            args.cikti.parent.mkdir(parents=True, exist_ok=True)
            args.cikti.write_text(çıktı, encoding="utf-8")
        else:
            sys.stdout.write(çıktı + "\n")

    elif args.komut == "db-yolu":
        ayar = ayar_yukle()
        print(ayar.db_yolu)

    elif args.komut == "eski-aktar":
        from .ayar import hazirla
        from .db import VeriTabani
        from . import eski
        ayar = hazirla(ayar_yukle())
        v = VeriTabani(ayar.db_yolu)
        print(eski.aktar(v, saat=args.saat))
        from .bildirim import alarm_denetle          # yeni fiyatlar geldi: alarm fiyatının altına düşen var mı
        gonderilen = alarm_denetle(v)
        if gonderilen:
            print("alarm gönderildi:", gonderilen)
        v.kapat()

    elif args.komut == "ornek-uret":
        from .ayar import hazirla
        from .db import VeriTabani
        from . import eski, pano
        ayar = hazirla(ayar_yukle())
        v = VeriTabani(ayar.db_yolu)
        c = args.cikti
        p = pano.pano(v)
        eski.json_yaz(p, c / "pano.json")
        for u in p["urunler"]:
            eski.json_yaz(pano.detay(v, u["id"]), c / f"detay_{u['id']}.json")
        eski.json_yaz(pano.siteler(v, eski.site_24s()), c / "siteler.json")
        eski.json_yaz(eski.seyahat(), c / "seyahat.json")
        v.kapat()
        print(f"yazıldı: {c}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
