"""asyncio yayın veriyolu: olayı hem DB'ye yazar hem abonelere iletir."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from .modeller import Olay

if TYPE_CHECKING:  # pragma: no cover
    from .db import VeriTabani


class YayinVeriyolu:
    def __init__(self, db: "VeriTabani", azami_kuyruk: int = 200) -> None:
        self._db = db
        self._azami = azami_kuyruk
        self._aboneler: set[asyncio.Queue[Olay]] = set()

    @property
    def abone_sayisi(self) -> int:
        return len(self._aboneler)

    def abone_ol(self, azami: int | None = None) -> asyncio.Queue[Olay]:
        kuyruk: asyncio.Queue[Olay] = asyncio.Queue(maxsize=azami or self._azami)
        self._aboneler.add(kuyruk)
        return kuyruk

    def abone_cikar(self, kuyruk: asyncio.Queue[Olay]) -> None:
        self._aboneler.discard(kuyruk)

    async def yayinla(self, olay: Olay) -> Olay:
        """Olayı DB'ye yaz (id atanmış hâliyle) ve tüm abonelere ilet."""
        kayit = self._db.olay_ekle(olay)
        for kuyruk in list(self._aboneler):
            try:
                kuyruk.put_nowait(kayit)
            except asyncio.QueueFull:
                # Yavaş aboneyi düşür: canlı akış tıkanmasın.
                try:
                    kuyruk.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    kuyruk.put_nowait(kayit)
                except asyncio.QueueFull:
                    pass
        return kayit
