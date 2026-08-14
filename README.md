# AI-bar

Pannello laterale GTK simile a una barra desktop: data/ora in alto, area tray/status con elenco finestre, due gruppi di launcher configurabili e terminale integrato nella parte bassa.

## Avvio

```bash
python3 -m ai_bar --config config.example.json
```

Dipendenze Debian/Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 gir1.2-wnck-3.0 gir1.2-xapp-1.0 python3-xlib pulseaudio-utils network-manager
```

## Configurazione

Il file predefinito e' `~/.config/ai-bar/config.json`. Puoi partire da `config.example.json` e modificare:

- `panel.side`: `left` oppure `right`
- `panel.width`: larghezza del pannello, default `400`
- `panel.resizable`: abilita il trascinamento del bordo laterale per cambiare larghezza
- `tray.items`: elementi laterali tipo volume, Telegram o comandi personalizzati
- `launcher_groups`: gruppi di pulsanti, icone e comandi
- `launcher_groups[].buttons[].target`: usa `terminal` per eseguire il comando nel terminale integrato
- `terminal.command`: shell/comando da aprire nel terminale integrato
- `session_buttons`: pulsanti in basso per reload, logout, reboot e powerdown

Il tray integrato supporta le icone XApp e, nelle sessioni X11, le icone XEmbed. Le applet condividono una griglia allineata a destra e vanno a capo singolarmente quando non entrano in larghezza. Su Wayland e per alcune app AppIndicator/StatusNotifier moderne, gli elementi possono non comparire nel pannello; in quel caso resta disponibile la riga `tray.items` configurabile. Su X11 il tasto Super nasconde o mostra il pannello con slide laterale.

## Sessione LightDM/Openbox

La sessione pronta e' `AI Bar Openbox`. Installa la voce LightDM con:

```bash
chmod +x scripts/ai-bar-openbox-session
mkdir -p ~/.config/ai-bar
cp --update=none config.example.json ~/.config/ai-bar/config.json
pkexec install -m 755 scripts/ai-bar-openbox-session /usr/local/bin/ai-bar-openbox-session
pkexec install -m 644 packaging/ai-bar-openbox.desktop /usr/share/xsessions/ai-bar-openbox.desktop
```

Poi esci dalla sessione grafica, scegli `AI Bar Openbox` nel selettore sessioni di LightDM ed entra. La configurazione usata al login e' `~/.config/ai-bar/config.json`.

## Verifica

```bash
python3 -m unittest discover -s tests
python3 -m compileall ai_bar tests
```
