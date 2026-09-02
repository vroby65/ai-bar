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
    yad
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
    cat > "$CONFIG_FILE" <<'AI_BAR_CONFIG_EOF'
{
  "panel": {
    "side": "left",
    "width": 400,
    "height": "screen",
    "decorated": false,
    "keep_above": true,
    "resizable": true,
    "reserve_space": true
  },
  "clock": {
    "time_format": "%H:%M:%S",
    "date_format": "%A %d %B %Y"
  },
  "tray": {
    "xembed": true,
    "icon_size": 16,
    "status_refresh_seconds": 5,
    "items": [
      {
        "type": "volume",
        "label": "Volume",
        "icon": "audio-volume-high-symbolic",
        "command": ["pavucontrol", "-t", "2"]
      },
      {
        "type": "display",
        "label": "Display",
        "icon": "preferences-desktop-display-symbolic",
        "command": ["arandr"],
        "icon_only": true
      },
      {
        "type": "screenshot",
        "label": "Screenshot",
        "icon": "camera-photo-symbolic",
        "command": ["/usr/bin/mate-screenshot", "/home/user/Immagini/screenshot_%Y-%m-%d_%H-%M-%S.png"],
        "icon_only": true
      }
    ]
  },
  "launcher_groups": [
    {
      "title": "",
      "columns": 4,
      "buttons": [
        {
          "label": "Terminale",
          "icon": "utilities-terminal-symbolic",
          "command": ["gnome-terminal"],
          "maximized": true
        },
        {
          "label": "Firefox",
          "icon": "firefox",
          "command": ["firefox"],
          "maximized": true
        },
        {
          "label": "Chrome",
          "icon": "google-chrome",
          "command": ["google-chrome"],
          "maximized": true
        },
        {
          "label": "caja",
          "icon": "system-file-manager-symbolic",
          "command": ["caja"],
          "maximized": true
        }
      ]
    },
    {
      "title": "Tools",
      "columns": 4,
      "buttons": [
                {
                    "label": "Menu",
                    "icon": "view-app-grid-symbolic",
                    "command": [
                        "menugui"
                    ],
                    "target": "window"
                },
        {
          "label": "or-codex",
          "icon": "system-run-symbolic",
          "command": ["or-codex"],
          "target": "terminal"
        },
        {
          "label": "ds-codex",
          "icon": "accessories-text-editor-symbolic",
          "command": ["ds-codex"],
          "target": "terminal"
        },
        {
          "label": "codex",
          "icon": "openai",
          "command": ["codex"],
          "target": "terminal"
        },
        {
          "label": "Calc",
          "icon": "accessories-calculator-symbolic",
          "command": ["gnome-calculator"],
          "target": "window"
        },
        {
          "label": "Meteo",
          "icon": "weather-few-clouds-symbolic",
          "url": "https://www.ilmeteo.it",
          "target": "url"
        },
        {
          "label": "VPN",
          "icon": "proton-vpn-logo",
          "command": ["/usr/bin/systemd-run", "--user", "--scope", "--collect", "--quiet", "/usr/bin/protonvpn-app"],
          "target": "window"
        },
        {
          "label": "terminal",
          "icon": "utilities-terminal-symbolic",
          "command": ["fish"],
          "target": "terminal"
        }
      ]
    }
  ],
  "quick_launchers": [
    {
      "label": "AnyDesk",
      "icon": "anydesk",
      "command": ["anydesk"]
    },
    {
      "label": "TeamViewer",
      "icon": "teamviewer",
      "command": ["teamviewer"]
    },
    {
      "label": "RustDesk",
      "icon": "rustdesk",
      "command": ["rustdesk"]
    },
    {
      "label": "LocalSend",
      "icon": "localsend_app",
      "command": ["localsend_app"]
    },
    {
      "label": "ChatGPT",
      "icon": "openai",
      "command": [
        "google-chrome-stable",
        "--app=http://chatgpt.com",
        "--class=WebApp-chatGPT8307",
        "--name=WebApp-chatGPT8307",
        "--user-data-dir=/home/user/.local/share/ice/profiles/chatGPT8307"
      ]
    }
  ],
  "terminal": {
    "command": ["hermes"],
    "working_directory": null,
    "font": "Monospace 10",
    "scrollback_lines": 10000
  },
  "session_buttons": [
    {
      "label": "Reload",
      "icon": "view-refresh-symbolic",
      "action": "reload"
    },
    {
      "label": "Logout",
      "icon": "system-log-out-symbolic",
      "command": ["openbox", "--exit"]
    },
    {
      "label": "Reboot",
      "icon": "system-reboot-symbolic",
      "command": ["systemctl", "reboot"]
    },
    {
      "label": "Powerdown",
      "icon": "system-shutdown-symbolic",
      "command": ["systemctl", "poweroff"]
    }
  ]
}
AI_BAR_CONFIG_EOF
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
sudo ln -sfn -- "$PROJECT_DIR/scripts/ai-bar-askpass" /usr/local/bin/ai-bar-askpass

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
