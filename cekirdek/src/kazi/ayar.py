"""Yapılandırma ve XDG yolları.

Varsayılanlar:
  ayar  : $XDG_CONFIG_HOME/kazi/ayar.toml  (yoksa oluşturulur; KAZI_AYAR ile değişir)
  veri  : $XDG_DATA_HOME/kazi/             (KAZI_VERI ile değişir)
  db    : <veri>/kazi.db
  kanıt : <veri>/kanit/
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

VARSAYILAN_PORT = 8770
VARSAYILAN_HOST = "127.0.0.1"


def _xdg(degisken: str, yedek: str) -> Path:
    deger = os.environ.get(degisken)
    if deger:
        return Path(deger).expanduser()
    return Path.home() / yedek


def ayar_yolu() -> Path:
    """ayar.toml yolu (KAZI_AYAR ortam değişkeni öncelikli)."""
    ozel = os.environ.get("KAZI_AYAR")
    if ozel:
        return Path(ozel).expanduser()
    return _xdg("XDG_CONFIG_HOME", ".config") / "kazi" / "ayar.toml"


def varsayilan_veri_dizini() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "kazi"


@dataclass
class Ayar:
    host: str = VARSAYILAN_HOST
    port: int = VARSAYILAN_PORT
    veri_dizini: Path = None  # type: ignore[assignment]
    ayar_yolu: Path = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.veri_dizini is None:
            self.veri_dizini = varsayilan_veri_dizini()

    @property
    def db_yolu(self) -> Path:
        return Path(self.veri_dizini) / "kazi.db"

    @property
    def kanit_dizini(self) -> Path:
        return Path(self.veri_dizini) / "kanit"


def _toml_metni(ayar: Ayar) -> str:
    # Basit elle TOML yazımı (yalnız düz skalerler).
    return (
        "[sunucu]\n"
        f'host = "{ayar.host}"\n'
        f"port = {int(ayar.port)}\n"
        "\n"
        "[veri]\n"
        f'dizin = "{Path(ayar.veri_dizini)}"\n'
    )


def ayar_yaz(ayar: Ayar, yol: Path | None = None) -> Path:
    yol = Path(yol) if yol else (ayar.ayar_yolu or ayar_yolu())
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.write_text(_toml_metni(ayar), encoding="utf-8")
    return yol


def ayar_yukle(olustur: bool = True) -> Ayar:
    """ayar.toml'u oku; yoksa varsayılan dosyayı oluştur.

    KAZI_VERI ortam değişkeni dosyadaki veri dizinini geçersiz kılar.
    KAZI_HOST / KAZI_PORT da (varsa) geçersiz kılar.
    """
    yol = ayar_yolu()
    ayar = Ayar(ayar_yolu=yol)

    if yol.is_file():
        try:
            with yol.open("rb") as fh:
                veri = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            veri = {}
        sunucu = veri.get("sunucu", {}) if isinstance(veri, dict) else {}
        veri_bolumu = veri.get("veri", {}) if isinstance(veri, dict) else {}
        if isinstance(sunucu, dict):
            if isinstance(sunucu.get("host"), str):
                ayar.host = sunucu["host"]
            if isinstance(sunucu.get("port"), int):
                ayar.port = sunucu["port"]
        if isinstance(veri_bolumu, dict) and isinstance(veri_bolumu.get("dizin"), str):
            ayar.veri_dizini = Path(veri_bolumu["dizin"]).expanduser()
    elif olustur:
        ayar_yaz(ayar, yol)

    ozel_veri = os.environ.get("KAZI_VERI")
    if ozel_veri:
        ayar.veri_dizini = Path(ozel_veri).expanduser()
    if os.environ.get("KAZI_HOST"):
        ayar.host = os.environ["KAZI_HOST"]
    if os.environ.get("KAZI_PORT"):
        try:
            ayar.port = int(os.environ["KAZI_PORT"])
        except ValueError:
            pass

    return ayar


def veri_dizini(ayar: Ayar | None = None) -> Path:
    ayar = ayar or ayar_yukle()
    return Path(ayar.veri_dizini)


def db_yolu(ayar: Ayar | None = None) -> Path:
    return veri_dizini(ayar) / "kazi.db"


def kanit_dizini(ayar: Ayar | None = None) -> Path:
    return veri_dizini(ayar) / "kanit"


def hazirla(ayar: Ayar | None = None) -> Ayar:
    """Veri ve kanıt dizinlerini oluştur, ayarı döndür."""
    ayar = ayar or ayar_yukle()
    Path(ayar.veri_dizini).mkdir(parents=True, exist_ok=True)
    Path(ayar.kanit_dizini).mkdir(parents=True, exist_ok=True)
    return ayar
