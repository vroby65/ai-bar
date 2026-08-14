# ds-bar

Pannello laterale GTK simile a una barra desktop: data/ora in alto, area tray/status, due gruppi di launcher configurabili e terminale integrato nella parte bassa.

## Avvio

```bash
python3 -m ds_bar --config config.example.json
```

Dipendenze Debian/Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 python3-xlib pulseaudio-utils network-manager
```

## Configurazione

Il file predefinito e' `~/.config/ds-bar/config.json`. Puoi partire da `config.example.json` e modificare:

- `panel.side`: `left` oppure `right`
- `panel.width`: larghezza del pannello, default `400`
- `panel.resizable`: abilita il trascinamento del bordo laterale per cambiare larghezza
- `tray.items`: elementi laterali tipo volume, Wi-Fi, Telegram o comandi personalizzati
- `launcher_groups`: gruppi di pulsanti, icone e comandi
- `launcher_groups[].buttons[].target`: usa `terminal` per eseguire il comando nel terminale integrato
- `terminal.command`: shell/comando da aprire nel terminale integrato
- `session_buttons`: pulsanti in basso per reload, logout, reboot e powerdown

L'host tray integrato supporta le icone XEmbed su sessione X11. Su Wayland e per alcune app AppIndicator/StatusNotifier moderne, gli elementi possono non comparire nel pannello; in quel caso resta disponibile la riga `tray.items` configurabile.

## Sessione LightDM/Openbox

La sessione pronta e' `DS Bar Openbox`. Installa la voce LightDM con:

```bash
chmod +x scripts/ds-bar-openbox-session
mkdir -p ~/.config/ds-bar
cp --update=none config.example.json ~/.config/ds-bar/config.json
pkexec install -m 755 scripts/ds-bar-openbox-session /usr/local/bin/ds-bar-openbox-session
pkexec install -m 644 packaging/ds-bar-openbox.desktop /usr/share/xsessions/ds-bar-openbox.desktop
```

Poi esci dalla sessione grafica, scegli `DS Bar Openbox` nel selettore sessioni di LightDM ed entra. La configurazione usata al login e' `~/.config/ds-bar/config.json`.

## Verifica

```bash
python3 -m unittest discover -s tests
python3 -m compileall ds_bar tests
```
