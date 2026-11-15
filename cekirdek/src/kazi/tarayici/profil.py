"""Kazı Brave profili: kalıcı yer, gizlilik/hız ayarları ve çerez penceresinde "Tümünü reddet" (15.11, kullanıcı isteği).

Bulgu (15.11): Brave flatpak olarak kurulu ve flatpak'in `~/.local/share/kazi` klasörüne yazma izni yok → `--user-data-dir`
sandbox'ın GEÇİCİ alanına düşüyordu: her açılışta sıfır profil ("gizliliği koruyan ürün analizi" uyarısı her seferinde,
çerez/onay/doğrulama hiç hatırlanmıyor). Flatpak'te profil artık uygulamanın kendi kalıcı veri klasöründe
(`~/.var/app/com.brave.Browser/data/kazi_brave_profil`; ek izin gerekmez, kullanıcının kişisel profili `config/` altında, ayrı).

Ayarlar `~/.config/kazi/tarama.json` (gorunurluk.ayar_oku) — Ayarlar ekranından değişir:
- `analiz_kapali`     : Brave ürün analizi (P3A), günlük kullanım sinyali, çökme raporu sorusu kapalı
- `bildirim_engelle`  : sitelerin bildirim izni isteği engellenir
- `popup_engelle`     : açılır pencereler (window.open) engellenir
- `cerez_reddet`      : sayfa açılınca çerez penceresinde "Tümünü reddet / Yalnızca gerekli" tıklanır
Tercihler Brave AÇILMADAN önce yazılır (açıkken yazılan dosyayı Brave kapanırken ezer) → değişiklik sonraki açılışta geçerli;
çerez reddi anında geçerli. Gizleme/parmak izi değiştirme YOK.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .gorunurluk import ayar_oku

FLATPAK_PROFIL = Path.home() / ".var" / "app" / "com.brave.Browser" / "data" / "kazi_brave_profil"

VARSAYILAN = {"analiz_kapali": True, "bildirim_engelle": True, "popup_engelle": True, "cerez_reddet": True}
ANAHTARLAR = tuple(VARSAYILAN)


def gizlilik_ayarlari(ayar: dict | None = None) -> dict[str, bool]:
    a = ayar_oku() if ayar is None else ayar
    return {k: bool(a.get(k, v)) for k, v in VARSAYILAN.items()}


def profil_dizini(taban_komut: list[str] | None, varsayilan: Path) -> Path:
    """Flatpak Brave'de sandbox'ın yazabildiği kalıcı klasör; diğerlerinde verilen varsayılan."""
    if taban_komut and taban_komut[:2] == ["flatpak", "run"]:
        return FLATPAK_PROFIL
    return varsayilan


def _json_oku(yol: Path) -> dict:
    try:
        d = json.loads(yol.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _json_yaz(yol: Path, d: dict) -> None:
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_name(yol.name + ".kazi-tmp")
    gecici.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(gecici, yol)


KISISEL_PROFIL = Path.home() / ".var" / "app" / "com.brave.Browser" / "config" / "BraveSoftware" / "Brave-Browser"


def _koy(d: dict, yol: str, deger: Any) -> None:
    """'a.b.c' yoluna değer yaz (deger None → anahtarı sil, varsayılana dönsün)."""
    parcalar = yol.split(".")
    for p in parcalar[:-1]:
        alt = d.get(p)
        if not isinstance(alt, dict):
            alt = d[p] = {}
        d = alt
    if deger is None:
        d.pop(parcalar[-1], None)
    else:
        d[parcalar[-1]] = deger


def tercihler(ayar: dict[str, bool]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(Local State, Default/Preferences) için yazılacak yol → değer. None = Brave varsayılanı."""
    ls: dict[str, Any] = {}
    pr: dict[str, Any] = {}
    if ayar["analiz_kapali"]:
        ls.update({"brave.p3a.enabled": False, "brave.p3a.notice_acknowledged": True,
                   "brave.stats.reporting_enabled": False, "brave.dont_ask_for_crash_reporting": True,
                   "user_experience_metrics.reporting_enabled": False})
        pr["brave.web_discovery_enabled"] = False
    # içerik ayarları: 2 = engelle; None = Brave varsayılanı (bildirim: sor, açılır pencere: engelle)
    pr["profile.default_content_setting_values.notifications"] = 2 if ayar["bildirim_engelle"] else None
    pr["profile.default_content_setting_values.popups"] = 2 if ayar["popup_engelle"] else None
    # 15.11: açılışta önceki oturumu GERİ YÜKLEME (5 = yeni sekme sayfası). Kazı profili her açılışta önceki taramanın
    # işçi pencerelerini geri getiriyordu → about:blank birikiyordu (deneyde açılış başına 8 → 22 → 29 sayfa).
    pr["session.restore_on_startup"] = 5
    return ls, pr


def profil_tohumla(profil: Path | str, ayar: dict[str, bool] | None = None) -> dict[str, Any]:
    """Brave açılmadan önce profil dosyalarına ayarları yaz (var olan içerik korunur). Yazılanları döndürür."""
    profil = Path(profil).expanduser()
    if profil.resolve() == KISISEL_PROFIL.resolve():      # kullanıcının kişisel Brave profiline ASLA yazılmaz
        raise ValueError("kişisel Brave profili tohumlanamaz")
    ayar = gizlilik_ayarlari() if ayar is None else ayar
    ls_deg, pr_deg = tercihler(ayar)
    # önceki oturumun kayıtlı sekmeleri/pencereleri silinir: geri yükleme hiçbir koşulda (çökme sonrası dahil) olmasın
    shutil.rmtree(profil / "Default" / "Sessions", ignore_errors=True)
    ls_yol, pr_yol = profil / "Local State", profil / "Default" / "Preferences"
    ls, pr = _json_oku(ls_yol), _json_oku(pr_yol)
    for k, v in ls_deg.items():
        _koy(ls, k, v)
    for k, v in pr_deg.items():
        _koy(pr, k, v)
    _json_yaz(ls_yol, ls)
    _json_yaz(pr_yol, pr)
    return {"local_state": ls_deg, "preferences": pr_deg}


# ---------------------------------------------------------------- çerez penceresi: "Tümünü reddet"
# Önce bilinen onay yöneticilerinin RED düğmeleri, sonra görünür düğmelerde TAM metin eşleşmesi (yalnız ret ifadeleri;
# "kabul" içeren hiçbir düğmeye basılmaz). Sayfadaki başka "Reddet" bağlantılarına basmamak için metin birebir aranır.
RED_SECICILERI = [
    "#onetrust-reject-all-handler", "#CybotCookiebotDialogBodyButtonDecline", "#didomi-notice-disagree-button",
    "button[data-testid='uc-deny-all-button']", ".cmplz-deny", "#truste-consent-required", ".cc-deny",
    "button[aria-label='Tümünü Reddet']", "button[aria-label='Reject all']",
]
RED_METINLERI = [
    "tümünü reddet", "hepsini reddet", "tüm çerezleri reddet", "çerezleri reddet", "reddet", "kabul etmiyorum",
    "yalnızca gerekli çerezler", "sadece gerekli çerezler", "yalnızca gerekli", "sadece gerekli", "yalnızca zorunlu",
    "sadece zorunlu", "zorunlu çerezler", "zorunlu olanları kabul et", "reject all", "reject", "decline",
    "only necessary", "necessary only",
]

_RED_JS = """([seciciler, metinler]) => {
  const gorunur = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  for (const s of seciciler) { const e = document.querySelector(s);
    if (e && gorunur(e)) { e.click(); return 'seçici:' + s; } }
  const adaylar = [...document.querySelectorAll('button, a, [role=button], input[type=button], input[type=submit]')]
    .filter(gorunur);
  const yazi = e => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().toLocaleLowerCase('tr-TR')
    .replace(/\\s+/g, ' ');
  for (const m of metinler) { const e = adaylar.find(x => yazi(x) === m);
    if (e) { e.click(); return 'metin:' + m; } }
  return null; }"""


def cerezleri_reddet(pg: Any, bekle_ms: int = 600) -> str | None:
    """Çerez penceresi varsa ret düğmesine bas; basılanı döndürür. Hata yutar (tarama asla bunun yüzünden düşmez)."""
    try:
        sonuc = pg.evaluate(_RED_JS, [RED_SECICILERI, RED_METINLERI])
        if sonuc:
            pg.wait_for_timeout(bekle_ms)
        return sonuc
    except Exception:
        return None


# ---------------------------------------------------------------- site açılır pencereleri (15.11)
# Kullanıcı: "Trendyol'da kadın/erkek seçimi gibi bir pencere geliyor, onu kapat sonra arama kutusuna yaz."
# Yalnız KAPAT düğmeleri tıklanır (seçim yapılmaz). Siteye özgü seçiciler site modülünde `POPUP_KAPAT`, ortaklar burada.
GENEL_KAPAT = ("[data-testid='modal-close']", "button[aria-label='Kapat']", "button[aria-label='Close']",
               "[aria-label='Kapat'][role='button']", ".modal-close", ".popup-close")

_KAPAT_JS = """(seciciler) => {
  const gorunur = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const basilan = [];
  for (const s of seciciler) { for (const e of document.querySelectorAll(s)) {
    if (gorunur(e)) { e.click(); basilan.push(s); break; } } }
  return basilan; }"""


def acilir_kapat(pg: Any, seciciler: tuple[str, ...] = (), bekle_ms: int = 500) -> list[str]:
    """Görünür açılır pencerelerin kapat düğmelerine bas; basılan seçicileri döndürür. Hata yutar."""
    try:
        basilan = pg.evaluate(_KAPAT_JS, list(seciciler) + list(GENEL_KAPAT)) or []
        if basilan:
            pg.wait_for_timeout(bekle_ms)
        return basilan
    except Exception:
        return []
