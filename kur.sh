#!/usr/bin/env bash
# kazi-asistan kurulumu — Linux dağıtımından bağımsız, sudo GEREKTİRMEZ.
# Kullanım: ./kur.sh [--servis] [--masaustu] [--hepsi] [--denetle]
#   --denetle  : yalnız bağımlılıkları denetle, hiçbir şey kurma
#   --servis   : çekirdek + tarama zamanlayıcısını systemd kullanıcı servisi olarak kur ve başlat
#   --masaustu : arayüzü derle, uygulama menüsüne kısayol ekle (~/.local/share/applications/kazi-arayuz.desktop)
#   --hepsi    : ikisi birden
set -uo pipefail
KOK="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
KIRMIZI=$'\e[31m'; YESIL=$'\e[32m'; SARI=$'\e[33m'; SIFIR=$'\e[0m'
tamam() { echo "${YESIL}✓${SIFIR} $*"; }
uyari() { echo "${SARI}⚠${SIFIR} $*"; }
hata()  { echo "${KIRMIZI}✗${SIFIR} $*"; }

SERVIS=0; MASAUSTU=0; DENETLE=0
for a in "$@"; do
  case "$a" in
    --servis) SERVIS=1 ;; --masaustu) MASAUSTU=1 ;; --hepsi) SERVIS=1; MASAUSTU=1 ;; --denetle) DENETLE=1 ;;
    *) echo "bilinmeyen seçenek: $a"; exit 2 ;;
  esac
done

echo "== Bağımlılık denetimi"
EKSIK=0
if command -v uv >/dev/null; then tamam "uv $(uv --version | cut -d' ' -f2)"; else
  hata "uv yok → kur: curl -LsSf https://astral.sh/uv/install.sh | sh"; EKSIK=1; fi
if command -v cargo >/dev/null; then tamam "cargo $(cargo --version | cut -d' ' -f2)"; else
  uyari "Rust yok (arayüz için gerekli) → kur: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal"; fi
BRAVE=""
if flatpak info com.brave.Browser >/dev/null 2>&1; then BRAVE="flatpak com.brave.Browser"
else for b in brave brave-browser brave-browser-stable; do command -v "$b" >/dev/null && { BRAVE="$b"; break; }; done; fi
if [[ -n "$BRAVE" ]]; then tamam "Brave: $BRAVE (bot korumalı siteler için K2 katmanı)"; else
  uyari "Brave bulunamadı → korumalı siteler (Trendyol, Hepsiburada, Cimri…) K2 olmadan çekilemez"; fi
if [[ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]]; then tamam "grafik oturum var (arayüz ve Brave açılabilir)"; else
  uyari "grafik oturum görünmüyor (yalnız çekirdek çalışır)"; fi
if command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then tamam "systemd kullanıcı oturumu"; else
  uyari "systemd kullanıcı oturumu yok → --servis yerine tmux ile çalıştır"; fi
[[ $EKSIK -eq 1 ]] && { hata "zorunlu bağımlılık eksik"; exit 1; }
[[ $DENETLE -eq 1 ]] && exit 0

echo "== Python çekirdeği"
( cd "$KOK/cekirdek" && uv sync -q --group dev ) && tamam "bağımlılıklar kuruldu (cekirdek/.venv)" || { hata "uv sync başarısız"; exit 1; }
( cd "$KOK/cekirdek" && uv run -q playwright install chromium chromium-headless-shell >/dev/null 2>&1 ) \
  && tamam "Playwright Chromium (K1 katmanı)" || uyari "Chromium indirilemedi (K1 atlanır; sonra: cd cekirdek && uv run playwright install chromium)"
( cd "$KOK/cekirdek" && uv run -q --group dev pytest -q tests >/tmp/kazi-test.log 2>&1 ) \
  && tamam "testler: $(tail -1 /tmp/kazi-test.log)" || uyari "bazı testler başarısız: $(tail -1 /tmp/kazi-test.log) (ayrıntı /tmp/kazi-test.log)"

if [[ $SERVIS -eq 1 ]]; then
  echo "== systemd kullanıcı servisi"
  mkdir -p "$HOME/.config/systemd/user"
  for birim in kazi-cekirdek kazi-zamanlayici; do
    sed "s|%h/kazi-asistan|$KOK|g; s|%h/.local/bin/uv|$(command -v uv)|g" "$KOK/dagitim/$birim.service" > "$HOME/.config/systemd/user/$birim.service"
  done
  # geçici tmux pencerelerinde çalışan çekirdek/zamanlayıcı varsa kapat (port 8770 ve pid kilidi boşalsın)
  for w in kazi kazi-zaman; do tmux kill-window -t "asistan:$w" 2>/dev/null && uyari "tmux asistan:$w kapatıldı (servise taşındı)"; done
  sleep 2
  systemctl --user daemon-reload && systemctl --user enable --now kazi-cekirdek.service kazi-zamanlayici.service \
    && tamam "kazi-cekirdek (127.0.0.1:8770) + kazi-zamanlayici (tarama, Telegram alarm/rapor) etkin — açılışta kendiliğinden başlar" \
    || hata "servis başlatılamadı: systemctl --user status kazi-cekirdek kazi-zamanlayici"
  loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes || uyari "linger kapalı: oturum kapanınca servis durur (açmak için: loginctl enable-linger $USER — yönetici izni isteyebilir)"
fi

if [[ $MASAUSTU -eq 1 ]]; then
  echo "== Arayüz"
  if command -v cargo >/dev/null && ( cd "$KOK/arayuz" && cargo build -q --release ); then
    BIN="$KOK/arayuz/target/release/kazi-arayuz"
    mkdir -p "$HOME/.local/share/applications"
    cat > "$HOME/.local/share/applications/kazi-arayuz.desktop" <<EOF2
[Desktop Entry]
Type=Application
Name=Kazı Asistanı
Comment=Kişisel fiyat ve seyahat kazıma asistanı
Exec=$BIN
Icon=system-search
Terminal=false
Categories=Utility;Network;
EOF2
    tamam "arayüz derlendi, uygulama menüsüne 'Kazı Asistanı' eklendi"
  else
    hata "arayüz derlenemedi (cd arayuz && cargo build --release)"
  fi
fi
echo "== Bitti. Çekirdek: http://127.0.0.1:8770/saglik · Arayüz: $KOK/arayuz/target/release/kazi-arayuz"
