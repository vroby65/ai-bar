# AI-bar

Pannello laterale GTK simile a una barra desktop: data/ora in alto, area tray/status con elenco finestre, due gruppi di launcher configurabili e terminale integrato nella parte bassa.

## Installazione

```bash
./install.sh
```

Esegui lo script come utente normale, senza `sudo`: chiedera' i privilegi solo per installare i pacchetti di sistema e la sessione. L'installer:

- installa le dipendenze su Debian, Ubuntu o Linux Mint
- installa il comando `ai-bar` con `pipx`
- crea `~/.config/ai-bar/config.json` se non esiste
- installa la sessione `AI Bar Openbox` per il login manager

## Avvio

Dopo l'installazione:

```bash
ai-bar
ai-bar --config ~/.config/ai-bar/config.json
```

Per avviarlo direttamente dal checkout durante lo sviluppo:

```bash
python3 -m ai_bar --config config.example.json
```

## Configurazione

Il file predefinito e' `~/.config/ai-bar/config.json`. Puoi partire da `config.example.json` e modificare:

- `panel.side`: `left` oppure `right`
- `panel.width`: larghezza del pannello, default `400`
- `panel.resizable`: abilita il trascinamento del bordo laterale per cambiare larghezza
- `tray.items`: elementi laterali tipo volume, Telegram o comandi personalizzati
- `launcher_groups`: gruppi di pulsanti, icone e comandi
- `launcher_groups[].buttons[].target`: usa `terminal` per eseguire il comando nel terminale integrato
- `launcher_groups[].buttons[].maximized`: apre la finestra esterna massimizzata
- `terminal.command`: shell/comando da aprire nel terminale integrato
- `session_buttons`: pulsanti in basso per reload, logout, reboot e powerdown

I launcher Ai-tools aprono una scheda terminale separata per ciascun comando. Tornando su un tool gia' aperto viene selezionata la sua scheda, il processo continua in background e il terminale riceve il focus. Nel terminale usa `Ctrl+Shift+C` e `Ctrl+Shift+V` per copia e incolla, oppure il menu del tasto destro.

Il tray integrato supporta le icone XApp e, nelle sessioni X11, le icone XEmbed. Le applet condividono una griglia allineata a sinistra e vanno a capo singolarmente quando non entrano in larghezza. Su Wayland e per alcune app AppIndicator/StatusNotifier moderne, gli elementi possono non comparire nel pannello; in quel caso resta disponibile la riga `tray.items` configurabile. Su X11 il tasto Super nasconde o mostra il pannello con slide laterale.

## Sessione LightDM/Openbox

La sessione viene installata da `install.sh`. Esci dalla sessione grafica, scegli `AI Bar Openbox` nel selettore di LightDM ed entra. Il launcher cerca il comando installato da `pipx` e usa `~/.config/ai-bar/config.json` senza dipendere dal checkout del repository.

## Verifica

```bash
python3 -m unittest discover -s tests
python3 -m compileall ai_bar tests
```
