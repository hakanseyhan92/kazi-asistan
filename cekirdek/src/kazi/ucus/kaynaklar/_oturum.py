"""Brave oturum yardımcısı — KULLANICI KURALI §2.7.

Brave'de bir siteye ilk giriş DAİMA ana sayfadan yapılır; ardından site içi
arama formu doldurulup arama başlatılır. Soğuk arama URL'si yasaktır. Form
başarısız olursa ana sayfa açık kalır ve sonuç URL'sine AYNI sekmeden
`location.assign` ile (referrer site içi) geçilir. Oturum kurulduktan sonraki
aramalar aynı sekmede doğrudan URL ile serbesttir.

Durum `Tarayici` nesnesi başına `t._ucus_oturum` sözlüğünde tutulur; kaynak
modülleri bu yardımcıyı çağırır.
"""
from __future__ import annotations

import logging
import re
import time

log = logging.getLogger("ucus.oturum")

# Yalnız bu adlar görünürse çerez bandı kapatılır; başka hiçbir şeye dokunulmaz.
_KABUL = re.compile(r"^(kabul( et)?|tümüne i̇zin ver|accept( all)?|i agree|tamam)$", re.I)
_REDDET = re.compile(r"^(reddet|reject( all)?|decline|yalnızca gerekli)$", re.I)


def _durum(t) -> dict:
    d = getattr(t, "_ucus_oturum", None)
    if d is None:
        d = {}
        setattr(t, "_ucus_oturum", d)
    return d


def cerez_kapat(pg) -> bool:
    """Yalnız görünür "Kabul/Reddet" düğmesi varsa kapat; yoksa dokunma."""
    for rx in (_KABUL, _REDDET):
        try:
            dugmeler = pg.get_by_role("button")
            n = dugmeler.count()
        except Exception:
            return False
        for i in range(min(n, 60)):
            try:
                el = dugmeler.nth(i)
                if not el.is_visible():
                    continue
                ad = (el.inner_text() or "").strip()
                if not ad or not rx.match(ad):
                    continue
                el.click(timeout=2500)
                pg.wait_for_timeout(700)
                return True
            except Exception:
                continue
    return False


def _ayni_sekme_git(pg, url: str) -> None:
    """Ana sayfadan sonra sonuç URL'sine o sekme içinden geç (referrer site içi)."""
    try:
        pg.evaluate("(u) => location.assign(u)", url)
    except Exception:
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            return
    try:
        pg.wait_for_load_state("domcontentloaded", timeout=45000)
    except Exception:
        pass
    pg.wait_for_timeout(2000)


def oturum_ac(t, site: str, ana_url: str, form_fn, sonuc_url: str | None = None) -> dict:
    """Oturumda site için ilk kez: ana sayfa + çerez + form_fn.

    Dönüş: {"acildi": True, "form": bool, "ilk_url": str}
    Sonraki çağrılarda hiçbir gezinme yapılmaz (aynı kayıt döner); kaynak
    modülü sonuç URL'sine kendi sekmesinde doğrudan gidebilir.

    `form_fn(pg)` True/False döndürür (ya da istisna fırlatır). Başarısızsa ve
    `sonuc_url` verildiyse ana sayfadan aynı sekmede `location.assign` ile
    sonuç sayfasına geçilir ve log'a "form başarısız, ana sayfa→URL" yazılır.
    """
    d = _durum(t)
    kayit = d.get(site)
    if kayit and kayit.get("acildi"):
        return kayit

    pg = t._sekme(site)
    try:
        pg.goto(ana_url, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        try:
            pg.goto(ana_url, wait_until="commit", timeout=60000)
        except Exception as e:
            kayit = {"acildi": True, "form": False, "ilk_url": ana_url,
                     "hata": f"{type(e).__name__}: {e}"}
            d[site] = kayit
            return kayit
    try:
        pg.wait_for_timeout(3000)
        pg.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass
    cerez_kapat(pg)

    form = False
    try:
        form = bool(form_fn(pg))
    except Exception as e:
        log.warning("%s form istisnası: %s: %s", site, type(e).__name__, e)

    kayit = {"acildi": True, "form": form, "ilk_url": ana_url}
    if not form:
        log.info("%s form başarısız, ana sayfa→URL (%s)", site, sonuc_url or "-")
        if sonuc_url:
            _ayni_sekme_git(pg, sonuc_url)
    d[site] = kayit
    return kayit


# ------------------------------------------------------------- tarih yardımcıları
AYLAR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
}


def tarih_etiketi(tarih: str) -> str:
    t = tarih.split("-")
    return f"{int(t[2])} {AYLAR[int(t[1])]} {t[0]}"


def takvim_sec(pg, tarih: str, adaylar=None) -> bool:
    """Açık takvimde günü seç. `adaylar` seçici listesi verilebilir (siteye özgü)."""
    etiket = tarih_etiketi(tarih)
    for secici in (adaylar or ()):
        try:
            loc = pg.locator(secici.format(etiket=etiket))
        except Exception:
            continue
        if not loc.count():
            continue
        for i in range(min(loc.count(), 4)):
            try:
                el = loc.nth(i)
                if el.is_visible():
                    el.click(timeout=4000)
                    pg.wait_for_timeout(1200)
                    return True
            except Exception:
                continue
    # genel: aria-label'ında tarih geçen görünür öğe
    genel = (
        f'[role="button"][aria-label*="{etiket}"]',
        f'button[aria-label*="{etiket}"]',
        f'[aria-label*="{etiket}"]',
        f'[data-date="{tarih}"]',
    )
    for secici in genel:
        try:
            loc = pg.locator(secici)
        except Exception:
            continue
        for i in range(min(loc.count(), 4)):
            try:
                el = loc.nth(i)
                if el.is_visible():
                    el.click(timeout=4000)
                    pg.wait_for_timeout(1200)
                    return True
            except Exception:
                continue
    return False


def tikla_ve_bekle(pg, loc, zaman_asimi: int = 5000) -> bool:
    try:
        loc.first.click(timeout=zaman_asimi)
        pg.wait_for_timeout(1200)
        return True
    except Exception:
        return False


def sonuc_sekmesini_kapat(pg) -> None:
    """`expect_page` ile yakalanan fazladan sonuç sekmesini kapat."""
    try:
        if pg is not None and not pg.is_closed():
            pg.close()
    except Exception:
        pass


# ------------------------------------------------------------- otomatik tamamlama (15.11)
def oneri_sec(pg, girdi, aramalar, iata: str, secici: str, bekle_ms: int = 3000) -> bool:
    """Arama formundaki kalkış/varış kutusuna yazıp öneri listesinden doğru noktayı seç.

    15.11 canlı bulgular (Ankara/ESB formu hiç dolmuyordu):
    - Kutuda önceki oturumdan kalan seçim çipi olabilir (Kayak "Ankara…", Trip.com "Seul") → `fill("")` silmez,
      yazılanlar kutuya girmez. Önce Ctrl+A + Backspace ile çipler/temel metin temizlenir.
    - Genel `ul li` seçicisi sayfadaki ilk 60 liste öğesine bakıyordu; menü/altbilgi öğeleri önce geldiği için öneri
      hiç denetlenmiyordu → yalnız siteye özgü öneri seçicisi (`secici`).
    - Önce TAM havalimanı kodu (\\bESB\\b), bulunamazsa şehir adı eşleşmesi seçilir; liste `bekle_ms`'e kadar beklenir.
    """
    try:
        girdi.click(timeout=6000)
    except Exception:
        return False
    pg.wait_for_timeout(400)
    kod_rx = re.compile(rf"\b{re.escape(str(iata).upper())}\b")
    # Kutunun bağlı olduğu öneri listesi (ARIA): Kayak'ta sayfada gizli ülke/dil listbox'ı da var, genel seçici
    # ilk 40 adayı onunla dolduruyordu ("Argentina (Español)"…). Bağlı liste varsa ÖNCE ona bakılır.
    bagli = ""
    try:
        for ozn in ("aria-controls", "aria-owns"):
            kimlik = girdi.get_attribute(ozn)
            if kimlik:
                bagli = ", ".join(f'[id="{k}"] [role="option"]' for k in kimlik.split())
                break
    except Exception:
        pass
    for aranan in [a for i, a in enumerate(aramalar) if a and a not in aramalar[:i]]:
        try:
            pg.keyboard.press("Control+A")
            for _ in range(4):
                pg.keyboard.press("Backspace")
            girdi.fill("")
        except Exception:
            pass
        try:
            pg.keyboard.type(str(aranan), delay=100)
        except Exception:
            return False
        bitis = time.monotonic() + bekle_ms / 1000
        while time.monotonic() < bitis:
            pg.wait_for_timeout(300)
            try:
                adaylar = pg.locator(bagli) if bagli and pg.locator(bagli).count() else pg.locator(secici)
                n = min(adaylar.count(), 150)
            except Exception:
                n = 0
            gorunur = []
            for i in range(n):
                el = adaylar.nth(i)
                try:
                    if el.is_visible():
                        tx = " ".join(filter(None, [(el.inner_text() or ""), el.get_attribute("aria-label") or ""]))
                        gorunur.append((el, tx))
                except Exception:
                    continue
            if not gorunur:
                continue
            secim = next((el for el, tx in gorunur if kod_rx.search(tx.upper())), None)
            if secim is None:
                secim = next((el for el, tx in gorunur if str(aranan).lower() in tx.lower()), None)
            if secim is None:
                continue
            try:
                secim.click(timeout=4000)
                pg.wait_for_timeout(900)
                return True
            except Exception:
                break
    try:
        pg.keyboard.press("Escape")
    except Exception:
        pass
    return False


def aramayi_baslat(pg, dugme, azami_sn: float = 10.0) -> bool:
    """Form 'Ara' düğmesine bas; YENİ SEKME açılınca ya da bu sekmenin adresi değişince HEMEN dön (15.11).
    Önceden her site yeni sekmeyi 15 sn bekliyordu; Trip.com aynı sekmede açtığı için her seferinde 15+3 sn boşa
    gidiyordu. Form yalnız oturum ısıtmasıdır (sonuçlar sonra adresle alınır) → sonuç sayfasının yüklenmesi beklenmez;
    açılan fazladan sekme kapatılır. Arama başladıysa True."""
    yeni: list = []

    def _sayfa(p) -> None:
        yeni.append(p)

    once = pg.url
    try:
        pg.context.on("page", _sayfa)
    except Exception:
        pass
    try:
        # 15.11 canlı (Trip.com): yolcu seçicisi ("Yetişkinler") açık kalıp Ara düğmesini örtüyordu → tıklama listeye
        # gidiyor, arama hiç başlamıyordu. Örtülüyse önce Escape; hâlâ örtülüyse tıklama doğrudan düğmeye iletilir.
        if _ortulu_mu(pg, dugme):
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(400)
        if _ortulu_mu(pg, dugme):
            dugme.dispatch_event("click")
        else:
            dugme.click(timeout=5000)
    except Exception:
        try:
            pg.context.remove_listener("page", _sayfa)
        except Exception:
            pass
        return False
    bitis = time.monotonic() + azami_sn
    basladi = False
    while time.monotonic() < bitis:
        pg.wait_for_timeout(250)
        if yeni or pg.url != once:
            basladi = True
            break
    try:
        pg.context.remove_listener("page", _sayfa)
    except Exception:
        pass
    for p in yeni:
        sonuc_sekmesini_kapat(p)
    return basladi


def _ortulu_mu(pg, dugme) -> bool:
    """Düğmenin ortasındaki en üst öğe düğmenin kendisi (ya da içindeki bir öğe) değilse örtülüdür."""
    try:
        return not dugme.evaluate("""(d) => { const r = d.getBoundingClientRect();
            const e = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
            return !!e && (e === d || d.contains(e)); }""")
    except Exception:
        return False
