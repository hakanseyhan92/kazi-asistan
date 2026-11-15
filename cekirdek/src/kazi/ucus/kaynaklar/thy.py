"""THY (turkishairlines.com) — DESTEKLENMİYOR (bot koruması).

14.11.2026 canlı denemesi: ana sayfa açılıyor ve form alanları DOM'da var
(`#fromPort`, `#toPort`, `#booker-date-toggle`, `#buttonUcusara`) ama ilk
etkileşimde PerimeterX modal'ı (`<iframe id="px-captcha-modal">`) tüm
tıklamaları yakalıyor; Playwright "iframe intercepts pointer events" ile
zaman aşımına düşüyor. CAPTCHA/stealth atlatma yasak olduğundan kaynak
kapalıdır. TK satırları Google/Skyscanner/Kayak kaynaklarından gelir.

Sözleşme gereği `ara()` `BotEngeli` fırlatır; çağıran bunu "desteklenmiyor"
olarak kaydeder.
"""
from __future__ import annotations

from ..model import BotEngeli

KAYNAK = "thy"
SITE = "https://www.turkishairlines.com/tr-tr/"
DESTEKLENIYOR = False
NEDEN = ("ana sayfa formu PerimeterX px-captcha-modal iframe ile korunuyor; "
         "ilk tıklamada modal tüm etkileşimi kesiyor (14.11.2026 canlı deneme). "
         "CAPTCHA atlatma yasak.")


def ara(t, bacak, tarih: str):
    raise BotEngeli("thy", SITE)
