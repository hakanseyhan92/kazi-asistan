"""Siteye göre öğrenen istek aralığı (AutoThrottle uyarlaması).

Sabit 2,5 sn + rastgele jitter yerine her sitenin yanıt hızına ve engel durumuna
göre bekleme süresini ayarlar:

- Hızlı ve sağlıklı yanıt veren sitede bekleme yavaşça ``alt_sn`` sınırına iner.
- Engel, 429 veya 503 görüldüğünde bekleme iki katına çıkar; sitenin bildirdiği
  ``Retry-After`` süresi daha uzunsa o kullanılır.
- Engel geçtikten sonra bekleme kademeli olarak normale döner, alt sınırın altına inmez.
- Siteler birbirinden bağımsızdır; aynı örnek birden çok sekmeden (thread)
  kullanılabilir.

Kaynak: Scrapling — https://github.com/D4Vinci/Scrapling
  ``scrapling/spiders/throttle.py`` (``AutoThrottle``, ``BLOCK_BACKOFF_FACTOR``)
Lisans: BSD 3-Clause, Copyright (c) 2024, Karim shoair.
Kod birebir kopyalanmamıştır; senkron, thread-safe ve Türkçe arayüzlü bir
uyarlamadır. Bekleme hesabı, alt/üst sınır kırpması ve engel geri çekilmesi
Scrapling'in yaklaşımını izler.
"""
from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable

__all__ = ["OtoHiz"]


class OtoHiz:
    """Site başına öğrenen, thread-safe istek aralığı yöneticisi."""

    def __init__(
        self,
        alt_sn: float = 2.0,
        ust_sn: float = 60.0,
        baslangic_sn: float = 2.5,
        hedef_eszamanlilik: float = 1.0,
        zaman_fn: Callable[[], float] = time.monotonic,
        uyku_fn: Callable[[float], None] = time.sleep,
        rastgele_fn: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if hedef_eszamanlilik <= 0:
            raise ValueError("hedef_eszamanlilik sıfırdan büyük olmalı")
        if ust_sn < alt_sn:
            raise ValueError("ust_sn, alt_sn'den küçük olamaz")
        if alt_sn < 0:
            raise ValueError("alt_sn negatif olamaz")

        self.alt_sn = float(alt_sn)
        self.ust_sn = float(ust_sn)
        # Başlangıç değeri de diğerleri gibi sınırların içinde tutulur.
        self.baslangic_sn = min(max(float(baslangic_sn), self.alt_sn), self.ust_sn)
        self.hedef_eszamanlilik = float(hedef_eszamanlilik)

        self._zaman_fn = zaman_fn
        self._uyku_fn = uyku_fn
        self._rastgele_fn = rastgele_fn

        self._kilit = threading.Lock()
        self._siteler: dict[str, dict[str, float | int | None]] = {}

    # ------------------------------------------------------------------ iç
    def _durum(self, site: str) -> dict[str, float | int | None]:
        """Site kaydını döndürür, yoksa başlangıç değerleriyle oluşturur.

        Yalnızca ``self._kilit`` tutulurken çağrılmalıdır.
        """
        kayit = self._siteler.get(site)
        if kayit is None:
            kayit = {
                "bekleme": self.baslangic_sn,
                "son_gecikme": 0.0,
                "engel_sayisi": 0,
                "son_istek": None,
                "istek_sayaci": 0,
                "uyku_toplam": 0.0,
            }
            self._siteler[site] = kayit
        return kayit

    # --------------------------------------------------------------- genel
    def bekleme(self, site: str) -> float:
        """Sitenin şu anki bekleme süresi (saniye).

        Henüz yanıt görülmemiş site için ``baslangic_sn`` döner ve site kaydı
        oluşturulmaz.
        """
        with self._kilit:
            kayit = self._siteler.get(site)
            return float(kayit["bekleme"]) if kayit is not None else self.baslangic_sn

    def bekle(self, site: str) -> float:
        """Son istekten bu yana gereken kadar uyur; uyuduğu süreyi döndürür.

        İlk istek için beklemeden geçilir (beklenecek önceki istek yoktur).
        Uyku süresine ±%10 rastgelelik uygulanır. Süre, kilit bırakılmadan
        önce "rezerve" edilir; böylece aynı siteye eşzamanlı gelen istekler
        üst üste binmez.
        """
        with self._kilit:
            kayit = self._durum(site)
            simdi = self._zaman_fn()
            gerekli = float(kayit["bekleme"]) * self._rastgele_fn(0.9, 1.1)
            son = kayit["son_istek"]
            if son is None:
                uyku = 0.0
            else:
                gecen = max(0.0, simdi - float(son))
                uyku = max(0.0, gerekli - gecen)
            kayit["son_istek"] = simdi + uyku
            kayit["istek_sayaci"] = int(kayit["istek_sayaci"] or 0) + 1
            kayit["uyku_toplam"] = float(kayit["uyku_toplam"] or 0.0) + uyku

        if uyku > 0:
            self._uyku_fn(uyku)
        return uyku

    def sayac(self, site: str) -> tuple[int, float]:
        """(şimdiye dek ``bekle`` çağrısı = istek sayısı, toplam uyunan sn). İş başında/sonunda farkı alınır:
        yanıt süresinden kendi uykumuzu çıkarmak için (15.11: uyku süreye katılınca bekleme kendini büyütüp
        üst sınıra tırmanıyordu — akakce'de istek arası 45 sn)."""
        with self._kilit:
            kayit = self._siteler.get(site)
            if kayit is None:
                return 0, 0.0
            return int(kayit["istek_sayaci"] or 0), float(kayit["uyku_toplam"] or 0.0)

    def yanit(
        self,
        site: str,
        sure_sn: float,
        durum: int | None = 200,
        engel: bool = False,
        retry_after: float | None = None,
    ) -> None:
        """Tamamlanan isteğin sonucunu öğren; sitenin bekleme süresini güncelle.

        :param site: İsteğin yapıldığı site.
        :param sure_sn: İsteğin saniye cinsinden süresi (gecikme).
        :param durum: HTTP durum kodu; ``None`` ağ hatası anlamına gelir.
        :param engel: Gövde/başlık incelemesinde bot engeli görüldüyse ``True``.
        :param retry_after: Sitenin ``Retry-After`` başlığında istediği süre.
        """
        with self._kilit:
            kayit = self._durum(site)
            eski = float(kayit["bekleme"])
            kayit["son_gecikme"] = max(0.0, float(sure_sn))

            engelli = engel or durum in (429, 503)
            if engelli:
                kayit["engel_sayisi"] = int(kayit["engel_sayisi"] or 0) + 1
                # Engel asla hızlandırmaz: en az iki katına çık, Retry-After
                # daha uzunsa ona uy, üst sınırı aşma.
                bildirilen = max(0.0, float(retry_after)) if retry_after is not None else 0.0
                yeni = min(self.ust_sn, max(eski * 2.0, bildirilen))
            elif durum is None or not (200 <= durum < 300):
                # Diğer başarısız yanıtlar (5xx, 403 vb.) da yavaşlatır.
                yeni = min(self.ust_sn, max(eski * 2.0, eski))
            else:
                # Sağlıklı yanıt: hedef gecikme = süre / eşzamanlılık.
                hedef = kayit["son_gecikme"] / self.hedef_eszamanlilik
                yeni = (eski + float(hedef)) / 2.0
                yeni = min(self.ust_sn, max(self.alt_sn, yeni))

            kayit["bekleme"] = yeni

    def durum(self) -> dict[str, dict[str, float | int]]:
        """Öğrenilen durumun kopyası: ``{site: {"bekleme", "son_gecikme", "engel_sayisi"}}``."""
        with self._kilit:
            return {
                site: {
                    "bekleme": float(kayit["bekleme"]),
                    "son_gecikme": float(kayit["son_gecikme"] or 0.0),
                    "engel_sayisi": int(kayit["engel_sayisi"] or 0),
                }
                for site, kayit in self._siteler.items()
            }
