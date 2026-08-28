#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
AI_BAR_BIN_DIR="${PIPX_BIN_DIR:-"$HOME/.local/bin"}"
AI_BAR_BIN="$AI_BAR_BIN_DIR/ai-bar"
CONFIG_DIR="${XDG_CONFIG_HOME:-"$HOME/.config"}/ai-bar"
CONFIG_FILE="$CONFIG_DIR/config.json"

APT_PACKAGES=(
    python3
    python3-gi
    gir1.2-gtk-3.0
    gir1.2-vte-2.91
    gir1.2-wnck-3.0
    gir1.2-xapp-1.0
    gir1.2-webkit2-4.1
    gir1.2-secret-1
    gnome-keyring
    libpam-gnome-keyring
    python3-xlib
    openbox
    policykit-1-gnome
    pipx
    pulseaudio-utils
    pavucontrol
    network-manager
)

if [ "$EUID" -eq 0 ]; then
    echo "Esegui questo installer come utente normale, senza sudo." >&2
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1 || ! command -v dpkg-query >/dev/null 2>&1; then
    echo "Questo installer richiede una distribuzione Debian, Ubuntu o Linux Mint." >&2
    exit 1
fi

if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo e' necessario per installare dipendenze e file di sessione." >&2
    exit 1
fi

missing_packages=()
for package in "${APT_PACKAGES[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -qx "install ok installed"; then
        missing_packages+=("$package")
    fi
done

if [ "${#missing_packages[@]}" -gt 0 ]; then
    echo "Installazione dipendenze di sistema: ${missing_packages[*]}"
    sudo apt-get update
    sudo apt-get install -y "${missing_packages[@]}"
fi

echo "Abilitazione del keyring per LightDM..."
systemctl --user unmask gnome-keyring-daemon.service gnome-keyring-daemon.socket
systemctl --user enable gnome-keyring-daemon.socket

echo "Installazione del comando ai-bar..."
pipx install --editable --force --system-site-packages --python /usr/bin/python3 "$PROJECT_DIR"

if [ ! -x "$AI_BAR_BIN" ]; then
    echo "pipx non ha creato il comando atteso: $AI_BAR_BIN" >&2
    exit 1
fi

mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_FILE" ]; then
    "$AI_BAR_BIN" --print-default-config > "$CONFIG_FILE"
    echo "Configurazione creata: $CONFIG_FILE"
fi

echo "Installazione del tema openbox Aura Midnight..."
mkdir -p "$HOME/.themes"
cp -r "$PROJECT_DIR/packaging/themes/Aura Midnight" "$HOME/.themes/"

if [ ! -f "$HOME/.config/openbox/rc.xml" ]; then
    mkdir -p "$HOME/.config/openbox"
    cat > "$HOME/.config/openbox/rc.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <theme>
    <name>Aura Midnight</name>
    <titleLayout>NLIMC</titleLayout>
    <keepBorder>yes</keepBorder>
  </theme>
</openbox_config>
EOF
    echo "Configurazione openbox creata con il tema Aura Midnight: $HOME/.config/openbox/rc.xml"
fi

echo "Installazione dei collegamenti dei comandi..."
sudo ln -sfn -- "$AI_BAR_BIN" /usr/local/bin/ai-bar
sudo ln -sfn -- "$PROJECT_DIR/scripts/ai-bar-openbox-session" /usr/local/bin/ai-bar-openbox-session

echo "Installazione della sessione AI Bar Openbox..."
sudo install -Dm644 \
    "$PROJECT_DIR/packaging/ai-bar-openbox.desktop" \
    /usr/share/xsessions/ai-bar-openbox.desktop

echo "Installazione completata."
echo "Tool: /usr/local/bin/ai-bar"
echo "Sessione: AI Bar Openbox"

case ":$PATH:" in
    *":$AI_BAR_BIN_DIR:"*) ;;
    *) echo "Aggiungi $AI_BAR_BIN_DIR al PATH oppure riapri il terminale." ;;
esac
