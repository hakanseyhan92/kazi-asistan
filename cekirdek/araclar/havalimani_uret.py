"""OurAirports açık verisinden (https://ourairports.com/data/, kamu malı) havalimanı tablosu üretir.

Kullanım: uv run python araclar/havalimani_uret.py <airports.csv> <countries.csv>
Çıktı: src/kazi/seyahat/veri/havalimanlari.json  (tarifeli seferi olan IATA kodlu büyük/orta havalimanları)
        src/kazi/seyahat/veri/havalimani_ulke.json (IATA → ülke; elle eklenenler korunur)
        src/kazi/seyahat/veri/ulkeler.json        (ISO kodu → ad; Türkçe adlar elle)
"""
import csv
import json
import sys
from pathlib import Path

VERI = Path(__file__).resolve().parents[1] / "src/kazi/seyahat/veri"
TR_ULKE = {
    "TR": "Türkiye", "JP": "Japonya", "KR": "Güney Kore", "KP": "Kuzey Kore", "CN": "Çin", "HK": "Hong Kong", "TW": "Tayvan",
    "IR": "İran", "AE": "Birleşik Arap Emirlikleri", "QA": "Katar", "SA": "Suudi Arabistan", "KZ": "Kazakistan", "UZ": "Özbekistan",
    "TM": "Türkmenistan", "AZ": "Azerbaycan", "GE": "Gürcistan", "AM": "Ermenistan", "RU": "Rusya", "UA": "Ukrayna",
    "DE": "Almanya", "FR": "Fransa", "GB": "Birleşik Krallık", "NL": "Hollanda", "IT": "İtalya", "ES": "İspanya", "AT": "Avusturya",
    "CH": "İsviçre", "GR": "Yunanistan", "BG": "Bulgaristan", "FI": "Finlandiya", "PL": "Polonya", "HU": "Macaristan",
    "CZ": "Çekya", "US": "Amerika Birleşik Devletleri", "CA": "Kanada", "TH": "Tayland", "VN": "Vietnam", "SG": "Singapur",
    "MY": "Malezya", "ID": "Endonezya", "PH": "Filipinler", "IN": "Hindistan", "PK": "Pakistan", "EG": "Mısır", "JO": "Ürdün",
    "IQ": "Irak", "SY": "Suriye", "LB": "Lübnan", "IL": "İsrail", "MN": "Moğolistan", "KG": "Kırgızistan", "TJ": "Tacikistan",
    "AF": "Afganistan", "OM": "Umman", "KW": "Kuveyt", "BH": "Bahreyn", "CY": "Kıbrıs", "BE": "Belçika", "DK": "Danimarka",
    "SE": "İsveç", "NO": "Norveç", "PT": "Portekiz", "IE": "İrlanda", "RO": "Romanya", "RS": "Sırbistan", "AU": "Avustralya",
}


def main(airports: str, countries: str) -> None:
    ulkeler = {r["code"]: TR_ULKE.get(r["code"], r["name"]) for r in csv.DictReader(open(countries, encoding="utf-8"))}
    liste, ulke = [], {}
    for r in csv.DictReader(open(airports, encoding="utf-8")):
        kod = (r["iata_code"] or "").strip().upper()
        if len(kod) != 3 or r["type"] not in ("large_airport", "medium_airport") or r["scheduled_service"] != "yes":
            continue
        ulke[kod] = r["iso_country"]
        liste.append({"kod": kod, "ad": r["name"], "sehir": r["municipality"] or r["name"], "ulke": r["iso_country"],
                      "buyuk": r["type"] == "large_airport",
                      "enlem": round(float(r["latitude_deg"]), 3), "boylam": round(float(r["longitude_deg"]), 3)})
    liste.sort(key=lambda x: (not x["buyuk"], x["kod"]))
    eski = json.loads((VERI / "havalimani_ulke.json").read_text(encoding="utf-8"))
    ulke.update({k: v for k, v in eski.items() if k not in ulke})     # elle eklenenleri koru
    (VERI / "havalimanlari.json").write_text(json.dumps(liste, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (VERI / "havalimani_ulke.json").write_text(json.dumps(dict(sorted(ulke.items())), ensure_ascii=False, indent=0), encoding="utf-8")
    (VERI / "ulkeler.json").write_text(json.dumps(dict(sorted(ulkeler.items())), ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(liste)} havalimanı, {len(ulke)} IATA→ülke, {len(ulkeler)} ülke")


if __name__ == "__main__":
    main(*sys.argv[1:3])
