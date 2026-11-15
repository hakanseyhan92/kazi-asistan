"""IATA havayolu kodu → tam ad (15.11 kullanıcı: "VF, TK, ET gibi kodları tam firma adına çevir").

YALNIZ GÖSTERİMDE kullanılır: kayıtlarda kod kalır, çünkü yolcuların transit kuralları (Kore/Çin/BAE aktarma yasağı,
`seyahat/segment.py HAVAYOLU_KOD_ULKE`) iki harfli kodlara bakar. Emin olunmayan kodlar (HH, 4O) tabloda yok → kod görünür.
"""
from __future__ import annotations

import re

HAVAYOLLARI: dict[str, str] = {
    # Türkiye
    "TK": "Türk Hava Yolları", "VF": "AJet", "PC": "Pegasus", "XQ": "SunExpress",
    # Japonya
    "JL": "Japan Airlines", "NH": "ANA (All Nippon Airways)", "MM": "Peach", "GK": "Jetstar Japan", "BC": "Skymark",
    "ZG": "ZIPAIR", "IJ": "Spring Japan", "7G": "StarFlyer", "6J": "Solaseed Air", "NU": "Japan Transocean Air",
    "JH": "Fuji Dream Airlines",
    # Güney Kore
    "KE": "Korean Air", "OZ": "Asiana Airlines", "7C": "Jeju Air", "TW": "T'way Air", "LJ": "Jin Air",
    "BX": "Air Busan", "RS": "Air Seoul", "ZE": "Eastar Jet", "YP": "Air Premia", "RF": "Aero K", "WE": "Parata Air",
    # Çin / Hong Kong / Tayvan / Makao
    "CA": "Air China", "MU": "China Eastern", "CZ": "China Southern", "FM": "Shanghai Airlines", "HO": "Juneyao Air",
    "9C": "Spring Airlines", "3U": "Sichuan Airlines", "HU": "Hainan Airlines", "ZH": "Shenzhen Airlines",
    "MF": "Xiamen Airlines", "SC": "Shandong Airlines", "GS": "Tianjin Airlines", "KN": "China United Airlines",
    "JD": "Beijing Capital Airlines", "CX": "Cathay Pacific", "UO": "HK Express", "HX": "Hong Kong Airlines",
    "HB": "Greater Bay Airlines", "NX": "Air Macau", "BR": "EVA Air", "CI": "China Airlines", "IT": "Tigerair Taiwan",
    # Orta Doğu / Körfez
    "QR": "Qatar Airways", "EK": "Emirates", "EY": "Etihad Airways", "FZ": "flydubai", "G9": "Air Arabia",
    "GF": "Gulf Air", "WY": "Oman Air", "SV": "Saudia", "XY": "flynas", "F3": "flyadeal", "KU": "Kuwait Airways",
    "RJ": "Royal Jordanian", "ME": "Middle East Airlines", "MS": "EgyptAir", "SM": "Air Cairo",
    "IR": "Iran Air", "W5": "Mahan Air", "EP": "Iran Aseman Airlines", "QB": "Qeshm Air",
    # Orta Asya / Kafkasya
    "HY": "Uzbekistan Airways", "C6": "Centrum Air", "KC": "Air Astana", "T5": "Turkmenistan Airlines",
    "J2": "Azerbaijan Airlines", "OM": "MIAT Mongolian Airlines",
    # Güney / Güneydoğu Asya
    "AI": "Air India", "TG": "Thai Airways", "PG": "Bangkok Airways", "FD": "Thai AirAsia", "SQ": "Singapore Airlines",
    "TR": "Scoot", "3K": "Jetstar Asia", "MH": "Malaysia Airlines", "OD": "Batik Air Malaysia", "AK": "AirAsia",
    "D7": "AirAsia X", "Z2": "Philippines AirAsia", "PR": "Philippine Airlines", "5J": "Cebu Pacific",
    "VN": "Vietnam Airlines", "VJ": "VietJet Air", "QH": "Bamboo Airways", "GA": "Garuda Indonesia",
    "UL": "SriLankan Airlines", "PK": "PIA", "BI": "Royal Brunei",
    # Avrupa
    "LH": "Lufthansa", "LX": "SWISS", "OS": "Austrian Airlines", "KL": "KLM", "AF": "Air France",
    "BA": "British Airways", "AZ": "ITA Airways", "IB": "Iberia", "AY": "Finnair", "SK": "SAS", "LO": "LOT",
    "A3": "Aegean Airlines", "GQ": "Sky Express", "W6": "Wizz Air", "FR": "Ryanair", "U2": "easyJet",
    "PS": "Ukraine International", "SU": "Aeroflot", "LY": "El Al", "H1": "Hahn Air",
    # Amerika / Okyanusya
    "UA": "United Airlines", "AA": "American Airlines", "DL": "Delta", "AC": "Air Canada", "QF": "Qantas",
    "NZ": "Air New Zealand", "JQ": "Jetstar",
    # Afrika
    "ET": "Ethiopian Airlines",
}

_KOD = re.compile(r"^[A-Z0-9]{2}$")


def havayolu_adi(firma: str | None) -> str:
    """"VF" → "AJet"; "A3, ET, ZG" → "Aegean Airlines, Ethiopian Airlines, ZIPAIR". Ad zaten yazılıysa dokunmaz,
    bilinmeyen kod olduğu gibi kalır, tekrar eden adlar bir kez yazılır."""
    if not firma:
        return firma or ""
    parcalar = [p.strip() for p in str(firma).split(",")]
    if not any(_KOD.match(p) for p in parcalar):
        return str(firma)
    sonuc: list[str] = []
    for p in parcalar:
        ad = HAVAYOLLARI.get(p, p) if _KOD.match(p) else p
        if ad and ad not in sonuc:
            sonuc.append(ad)
    return ", ".join(sonuc)
