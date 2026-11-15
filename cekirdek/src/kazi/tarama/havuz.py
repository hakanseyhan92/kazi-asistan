"""Çok sekmeli iş havuzu (15.11, kullanıcı: "5 sekmede 5 farklı sitede aynı anda ürün aransın").

Kurallar:
- İş birimi (site, yük). N işçi iş parçacığı; her işçinin KENDİ kaynağı var (`kaynak_fabrika(i)` → ör. kendi CDP bağlantısı ve
  sekmeleriyle bir Tarayici). Playwright'ın senkron API'si iş parçacıkları arasında paylaşılamaz; bu yüzden kaynak işçiye özeldir.
- Bir site aynı anda YALNIZ BİR işçide çalışır (siteler bot sanmasın). Boşalan işçi, şu an kimsenin taramadığı bir sitenin
  sıradaki işini alır; tüm kalan işlerin siteleri meşgulse bekler.
- İşler verilen sırayla ele alınır (öncelik korunur), ama meşgul siteler atlanıp sonra dönülür.
- Bir işin hatası diğerlerini durdurmaz; `is_fn` istisnası sonuçta `hata` olarak döner.
- `durdur` (threading.Event) kurulursa yeni iş alınmaz, süren işler biter.
- 15.11 pencere düzeni: boşalan işçi ÖNCE kendi açık penceresi olan sitenin sıradaki işini alır (yeni pencere açılmaz).
  Bir sitenin bekleyen ve süren işi kalmayınca o site "biter"; penceresi açık olan HER işçi (kendi iş parçacığında —
  Playwright nesneleri iş parçacığına özeldir) `site_bitti(kaynak, site)` ile kapatır. Önceden işi biten sitelerin
  pencereleri tarama sonuna kadar açık kalıyordu (GPU taramasında 9 pencere).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, Iterable


@dataclass
class IsSonucu:
    sira: int
    site: str
    yuk: Any
    deger: Any = None
    hata: BaseException | None = None
    isci: int = -1


@dataclass
class HavuzOzeti:
    sonuclar: list[IsSonucu] = field(default_factory=list)
    azami_eszamanli: int = 0


def calistir(
    isler: Iterable[tuple[str, Any]],
    isci_sayisi: int,
    kaynak_fabrika: Callable[[int], Any],
    is_fn: Callable[[Any, str, Any], Any],
    kaynak_kapat: Callable[[Any], None] | None = None,
    durdur: threading.Event | None = None,
    site_bitti: Callable[[Any, str], None] | None = None,
) -> HavuzOzeti:
    """`isler` = [(site, yük), …]. Her iş `is_fn(kaynak, site, yük)` ile çalışır. Sonuçlar iş sırasına göre döner."""
    bekleyen: list[tuple[int, str, Any]] = [(i, s, y) for i, (s, y) in enumerate(isler)]
    ozet = HavuzOzeti()
    if not bekleyen:
        return ozet
    isci_sayisi = max(1, min(int(isci_sayisi), len(bekleyen)))
    kosul = threading.Condition()
    mesgul: set[Hashable] = set()
    aktif = [0]
    sonuclar: list[IsSonucu] = []

    biten: set[str] = set()                 # bekleyen ve süren işi kalmamış siteler

    def _al(sahip: set[str]) -> tuple[int, str, Any] | list[str] | None:
        """Sıradaki iş; ya da bu işçinin kapatması gereken biten site pencereleri (liste); iş kalmadıysa None."""
        with kosul:
            while True:
                kapat = sorted(sahip & biten)
                if kapat and site_bitti is not None:
                    return kapat
                if (durdur is not None and durdur.is_set()) or not bekleyen:
                    return None
                secim = None
                for j, (_, site, _) in enumerate(bekleyen):       # önce kendi penceresi açık olan site
                    if site in sahip and site not in mesgul:
                        secim = j
                        break
                if secim is None:
                    for j, (_, site, _) in enumerate(bekleyen):
                        if site not in mesgul:
                            secim = j
                            break
                if secim is not None:
                    is_ = bekleyen.pop(secim)
                    mesgul.add(is_[1])
                    aktif[0] += 1
                    ozet.azami_eszamanli = max(ozet.azami_eszamanli, aktif[0])
                    return is_
                kosul.wait(timeout=1.0)

    def _birak(site: str) -> None:
        with kosul:
            mesgul.discard(site)
            aktif[0] -= 1
            if not any(s == site for _, s, _ in bekleyen):
                biten.add(site)
            kosul.notify_all()

    def _isci(no: int) -> None:
        kaynak = None
        sahip: set[str] = set()             # bu işçinin penceresi açık olan siteler
        try:
            kaynak = kaynak_fabrika(no)
            while True:
                is_ = _al(sahip)
                if is_ is None:
                    return
                if isinstance(is_, list):
                    for site in is_:
                        sahip.discard(site)
                        try:
                            site_bitti(kaynak, site)          # type: ignore[misc]
                        except Exception:
                            pass
                    continue
                sira, site, yuk = is_
                sahip.add(site)
                kayit = IsSonucu(sira=sira, site=site, yuk=yuk, isci=no)
                try:
                    kayit.deger = is_fn(kaynak, site, yuk)
                except BaseException as e:   # noqa: BLE001 — iş hatası havuzu düşürmez
                    kayit.hata = e
                finally:
                    with kosul:
                        sonuclar.append(kayit)
                    _birak(site)
        except BaseException as e:   # kaynak kurulamadı: bu işçinin alamadığı işler diğer işçilere kalır
            with kosul:
                sonuclar.append(IsSonucu(sira=-1, site="", yuk=None, hata=e, isci=no))
                kosul.notify_all()
        finally:
            if kaynak is not None and kaynak_kapat is not None:
                try:
                    kaynak_kapat(kaynak)
                except Exception:
                    pass

    iscler = [threading.Thread(target=_isci, args=(i,), name=f"kazi-sekme-{i + 1}", daemon=True)
              for i in range(isci_sayisi)]
    for th in iscler:
        th.start()
    for th in iscler:
        th.join()
    # bütün işçilerin kaynağı kurulamadıysa kalan işler hata olarak döner
    with kosul:
        for sira, site, yuk in bekleyen:
            neden = "durduruldu" if durdur is not None and durdur.is_set() else "işçi kalmadı (tarayıcı açılamadı)"
            sonuclar.append(IsSonucu(sira=sira, site=site, yuk=yuk, hata=RuntimeError(neden)))
        bekleyen.clear()
    ozet.sonuclar = sorted(sonuclar, key=lambda k: k.sira)
    return ozet
