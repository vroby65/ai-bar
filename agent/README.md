# Guida per agenti AI

Questa è la guida operativa per adattare o modificare `ai-bar`. Prima di intervenire,
leggi anche `README.md`, il file sorgente interessato e i test corrispondenti. Le
informazioni qui descrivono il codice presente nel repository: verifica sempre il
diff e la versione corrente prima di basarti su un dettaglio.

## Obiettivo e limiti

`ai-bar` è un pannello laterale GTK 3 configurabile, pensato soprattutto per una
sessione Openbox/X11. Mostra orologio, stato e tray, finestre attive, launcher,
terminali VTE incorporati, web app WebKit e comandi di sessione.

Mantieni gli interventi piccoli e riconducibili alla richiesta:

- esplicita le assunzioni che possono cambiare il risultato;
- scegli l'implementazione più semplice che soddisfa il comportamento richiesto;
- scrivi prima un test che riproduca un bug o descriva la nuova capacità;
- non rifattorizzare, rinominare o riformattare codice adiacente senza necessità;
- conserva le modifiche preesistenti dell'utente e non includere file estranei;
- non aggiungere compatibilità, opzioni o gestione di casi impossibili non richieste.

## Mappa del repository

- `ai_bar/app.py`: ingresso dell'applicazione, finestra GTK, CSS, launcher, terminali
  VTE, finestre e web view incorporate, distacco delle schede, monitor, volume,
  favicon, portachiavi e azioni di sessione.
- `ai_bar/config.py`: configurazione predefinita, merge con il JSON utente e
  validazione. È la fonte autorevole dello schema runtime.
- `ai_bar/config_assistant.py`: dialogo che permette a un agente esterno o a un
  editor di modificare esclusivamente il file di configurazione attivo.
- `ai_bar/xapp_tray.py`: integrazione degli indicatori XApp.
- `ai_bar/xembed_tray.py`: host XEmbed per le icone tray in X11.
- `ai_bar/__main__.py`: inoltra l'esecuzione a `ai_bar.app:main`.
- `config.example.json`: esempio utente, da mantenere allineato alle opzioni
  pubbliche in `DEFAULT_CONFIG`.
- `install.sh`: dipendenze Debian/Ubuntu/Mint e installazione editabile con `pipx`.
- `scripts/ai-bar-openbox-session`: avvio di Openbox, supervisione e riavvio di
  `ai-bar`, più inoltro sicuro di reboot e spegnimento.
- `packaging/`: sessione desktop e tema Openbox installati dal progetto.
- `tests/`: test `unittest`, organizzati per modulo o sottosistema.

Non modificare `ai_bar.egg-info/`, cache, dati WebKit o configurazioni sotto la home
per implementare una funzionalità del repository.

## Flusso runtime

1. `python3 -m ai_bar` chiama `ai_bar.app:main`.
2. `load_config` parte da una copia profonda di `DEFAULT_CONFIG`, applica il JSON
   utente con un merge ricorsivo dei dizionari e valida il risultato.
3. `AiBarWindow` costruisce il pannello, registra callback GTK/GLib e inizializza le
   integrazioni opzionali disponibili.
4. Il notebook terminale non mostra le linguette. Le pagine sono raggiunte tramite
   i launcher e identificate dalle chiavi prodotte da `launcher_page_key` e
   `terminal_session_key`.
5. La sessione Openbox esegue e, se necessario, riavvia il processo `ai-bar` usando
   il checkout sorgente o il comando installato.

La configurazione utente predefinita è
`$XDG_CONFIG_HOME/ai-bar/config.json`, oppure `~/.config/ai-bar/config.json`.
Cookie, favicon e cache delle icone WebKit sono dati runtime sotto
`$XDG_DATA_HOME/ai-bar/webkit`, oppure `~/.local/share/ai-bar/webkit`. Le
credenziali web devono restare nel portachiavi Secret Service e non devono mai
finire nel JSON, nei log, nei test o nel repository.

## Invarianti da preservare

### GTK e concorrenza

- Il codice usa GTK 3 e VTE 2.91 tramite PyGObject. Aggiorna widget e stato GTK nel
  thread principale; da un thread di lavoro rientra tramite `GLib.idle_add`.
- `Wnck`, `WebKit2`, `Secret` e `python-xlib` sono integrazioni opzionali nel codice.
  L'assenza di una di esse deve disabilitare solo la relativa capacità o usare il
  fallback già previsto.
- Gli handler periodici GLib devono restituire il booleano corretto e le risorse
  registrate devono essere fermate in `_on_destroy`.

### Pagine e launcher

- `self.terminals`, `self.embedded`, `self.launcher_buttons` e `self.detached`
  rappresentano la stessa identità logica da punti diversi. Quando una pagina viene
  aggiunta, sostituita, distaccata o rimossa, mantieni coerenti tutte le mappe.
- Le linguette del notebook sono nascoste: non creare pagine prive di un percorso
  per raggiungerle. Se l'ultima pagina viene distaccata, l'area resta vuota.
- Chiudere una finestra distaccata riporta la pagina nel pannello e non termina la
  sessione. Una pagina basata su `Gtk.Socket` non è distaccabile.
- I target dei launcher hanno semantiche diverse: nessun `target` avvia un programma
  esterno; `terminal` usa VTE; `window` prova a incorporare una finestra X11;
  `url` usa WebKit e ripiega sul browser di sistema se WebKit non è disponibile.
- I comandi accettano una stringa shell o una lista di argomenti. Usa
  `command_to_shell_line` e `terminal_argv` invece di ricostruire quoting e shell in
  un nuovo punto.

### X11, monitor e sessione

- La geometria verticale usa la work area del monitor, così non copre dock già
  riservati; il contenuto eccedente scorre verticalmente.
- Gli strut EWMH e XEmbed sono specifici di X11. Non tentare di applicarli in
  Wayland e conserva i fallback esistenti.
- Il monitor del pannello e quello di lancio sono concetti separati. Se un monitor
  configurato non esiste, mantieni il fallback e l'avviso una sola volta.
- Reboot e poweroff non devono essere eseguiti direttamente come normali launcher:
  passano dal supervisore di sessione e dal flusso di autorizzazione esistente.

### Web e dati sensibili

- Compilazione credenziali e download delle favicon sono limitati alla stessa
  origine dell'URL configurato. Non allargare questo controllo senza una richiesta
  esplicita e test di sicurezza.
- Mantieni timeout, limite dimensionale e fallback delle favicon; il download resta
  fuori dal thread GTK.
- Non salvare password fuori dal portachiavi e non inserire segreti reali nei test.

## Come apportare le modifiche più comuni

### Aggiungere o cambiare una configurazione

Aggiorna, nell'ordine necessario al test:

1. un test in `tests/test_config.py` per default, merge e input non valido;
2. `DEFAULT_CONFIG` e `validate_config` in `ai_bar/config.py`;
3. il consumatore in `ai_bar/app.py` o nel modulo interessato;
4. `config.example.json` e la sezione Configuration di `README.md`.

Il caricamento di un vecchio file parziale deve continuare a funzionare grazie ai
default. Una lista nel JSON sostituisce la lista predefinita; non viene unita
elemento per elemento.

### Cambiare interfaccia o comportamento del pannello

Metti la logica pura in una piccola funzione testabile solo quando separarla rende
il comportamento realmente più chiaro. Per i widget, costruisci istanze GTK nei
test, simula i segnali e distruggile al termine. Mantieni classi CSS e stile
esistenti salvo che la richiesta riguardi esplicitamente l'aspetto.

Per terminali, web view o finestre incorporate, aggiungi test sulla chiave di pagina,
sulla selezione del notebook e sulle mappe di stato: un test solo sul click del
pulsante non copre il ciclo di vita.

### Cambiare tray o integrazioni desktop

Lavora in `xapp_tray.py` o `xembed_tray.py` se il comportamento appartiene al
protocollo tray, non in `app.py`. Preserva la rimozione pulita dei `FlowBoxChild` e
la registrazione/chiusura degli host. Per geometria, attivazione finestre e strut,
copri separatamente monitor multipli, fallback e differenze X11/Wayland pertinenti.

### Cambiare installazione o sessione

Mantieni `install.sh` limitato alle distribuzioni dichiarate e l'installazione
`pipx --editable --system-site-packages`, necessaria per i moduli GI di sistema.
Ogni modifica shell richiede almeno `bash -n` e i test in `tests/test_packaging.py`.
Non eseguire realmente logout, reboot o poweroff durante i test.

## Verifica

Esegui dalla radice del repository:

```bash
python3 -m unittest discover -s tests
python3 -m compileall ai_bar tests
git diff --check
```

In un ambiente senza display usa:

```bash
xvfb-run -a python3 -m unittest discover -s tests
```

Per una modifica visibile, se l'ambiente grafico e le dipendenze sono disponibili,
completa i test automatici con un avvio mirato:

```bash
python3 -m ai_bar --config config.example.json
```

Controlla soltanto il flusso interessato: lato pannello, ridimensionamento e scroll,
launcher esterno/terminale/finestra/web, distacco e rientro, tray o comandi di
sessione. Non usare il test manuale come sostituto di una regressione automatica.

## Criteri di completamento

Una modifica è pronta quando il comportamento richiesto è coperto, la suite e la
compilazione passano, `git diff --check` è pulito, documentazione ed esempio sono
allineati se cambia una superficie pubblica, e il diff non contiene cambiamenti non
correlati. Riporta verifiche non eseguibili e relativo motivo invece di dichiararle
superate.
