"""Test yardımcıları: Akakçe arama sayfası biçiminde sahte HTML üretir (astro-island props)."""
from __future__ import annotations

import html
import json


def _a(v):
    """Python değerini astro serileştirmesine çevir ([0, x] / [1, [..]])."""
    if isinstance(v, dict):
        return [0, {k: _a(x) for k, x in v.items()}]
    if isinstance(v, list):
        return [1, [_a(x) for x in v]]
    return [0, v]


def sahte_arama_html(urunler: list[dict]) -> str:
    """urunler: {code, name, price, countOfPrices?, url?, imageUrl?}"""
    liste = []
    for u in urunler:
        liste.append({
            "code": u["code"], "price": u["price"], "countOfPrices": u.get("countOfPrices", 3), "name": u["name"],
            "imageUrl": u.get("imageUrl", f"https://cdn.akakce.com/x/{u['code']}.jpg"),
            "url": u.get("url", f"/urun/en-ucuz-{u['code']}-fiyati,{u['code']}.html"),
        })
    props = {"searchData": {"productList": {"countOfProducts": len(liste), "products": liste}}}
    kacis = html.escape(json.dumps(_a(props)[1]), quote=True)
    return (f'<html><body><astro-island uid="x" props="{html.escape(json.dumps({"x": 1}), quote=True)}"></astro-island>'
            f'<astro-island uid="y" props="{kacis}"></astro-island></body></html>')
