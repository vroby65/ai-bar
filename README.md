# AI-bar

A GTK side panel similar to a desktop bar, with the date and time at the top, a tray/status area with a window list, two configurable launcher groups, and an embedded terminal at the bottom.

The default theme uses a flat graphite surface with solid accent colors for the
main controls. Launcher and window buttons cycle through the accent palette,
while page actions, quick launchers, and tray icons remain transparent.

## Installation

Open `Installa AI-bar.desktop` from the checkout to run the installer in a
terminal. If the file manager asks, allow the launcher to run. Alternatively:

```bash
./install.sh
```

Run the script as a regular user, without `sudo`: it requests elevated privileges only to install the system packages and session. The installer:

- installs the dependencies on Debian, Ubuntu, or Linux Mint
- enables GNOME Keyring so LightDM can unlock it with the login password
- installs the `ai-bar` command with `pipx` in editable mode and links `ai-bar`,
  `ai-bar-openbox-session`, and `ai-bar-askpass` into `/usr/local/bin`
- creates `~/.config/ai-bar/config.json` if it does not exist, using the
  configuration bundled with the installer
- installs the `Aura Midnight` Openbox theme into `~/.themes` and creates `~/.config/openbox/rc.xml` pointing to it if it does not already exist
- installs Picom with the GLX renderer, 14 px rounded corners, and light 2 px
  borders, including windows that draw their own decorations (such as Calculator)
- installs the `AI Bar Openbox` session for the login manager
- fixes MintUpdate's tray reopening on affected versions, when MintUpdate is installed

Picom starts automatically with Openbox on the next login. Maximized and
fullscreen windows, panels, and menus remain square. The installer writes
`$XDG_CONFIG_HOME/picom.conf`, `picom/csd-border.glsl`, and
`autostart/picom.desktop` (under `~/.config` by default), adapting the shader path
to the installing user. Existing files are preserved as `*.ai-bar.bak` before
replacement; running the installer again keeps the original backup. The Openbox
border is lighter on the active window; windows with their own decorations use
the same light border when inactive. An existing Openbox theme selection is
preserved, so select `Aura Midnight` if another theme is currently configured.

The MintUpdate fix preserves the original file as `mintUpdate.py.ai-bar.bak` and
is safe to run again. A MintUpdate package upgrade can overwrite it; rerun
`./install.sh` to reapply it. Versions with different tray code are left untouched
with a diagnostic message. MintUpdate continues to start through Openbox's XDG
autostart in the graphical login session, where the Polkit authentication agent
can authorize updates. The installer does not launch or restart MintUpdate; to
use the fix immediately, quit MintUpdate from its tray menu and reopen it from
the desktop application menu or a terminal opened by Openbox.

## Usage

After installation:

```bash
ai-bar
ai-bar --config ~/.config/ai-bar/config.json
ai-bar --print-default-config
```

To run it directly from the checkout during development:

```bash
python3 -m ai_bar --config config.example.json
```

The editable installation loads Python code directly from this checkout. After a code change, use the `Reload` button to test it without running the installer again.

By default, left-click the date or time at the top of the panel to open Google Calendar in Google Chrome (`google-chrome`). Set `clock.command` to change this action or disable it with `null`.

## Configuration

The default file is `$XDG_CONFIG_HOME/ai-bar/config.json`, falling back to
`~/.config/ai-bar/config.json` when `XDG_CONFIG_HOME` is unset. You can start
with `config.example.json` and customize:

- `panel.side`: `left` or `right`
- `panel.width`: panel width, default `400`
- `panel.height`: `screen` uses the selected monitor's work area; if the panel content is taller, it scrolls vertically instead of extending past the screen
- `panel.resizable`: enables dragging the side edge to change the width
- `panel.monitor`: which monitor holds the panel, by index or connector name such as `DP-1`; unset uses the primary one
- `panel.launch_monitor`: where windows started from the panel should appear, by index or connector name, or `auto` for the primary monitor; unset leaves placement to the window manager
- `clock.command`: command to run on a left-click on the date or time, as an argument list or shell string; defaults to `["google-chrome", "https://calendar.google.com/"]` when omitted; `null` disables the action
- `tray.items`: side items such as volume, Telegram, or custom commands
- `tray.icon_size`: size of XApp and XEmbed tray icons, default `16`
- `tray.items[].icon_only`: `true` shows only the icon for that item, without the text label
- `launcher_groups`: groups of buttons, icons, and commands; external launchers
  stay pinned in the window dock and focus the application instead of creating a
  duplicate button when it is already open; a yellow underline marks pinned
  launchers with an open window, while pinned launchers have a light border; for
  unpinned applications the yellow underline marks buttons that group multiple
  windows; multiple windows from an application share one button and repeated
  clicks cycle through them; right-click an unpinned
  application and choose `Mantieni nella barra` to save it as a launcher, or
  right-click a pinned one to open a new instance or remove it
- `launcher_groups[].buttons[].app_id`: stable application identifier saved
  automatically when an open window is pinned
- `launcher_groups[].buttons[].target`: where the button opens its content;
  omit it to launch an external application, or set one of these values for
  content inside the panel:
  - `terminal`: runs the command in an embedded terminal tab
  - `window`: launches a GUI program and embeds its window in a tab of the terminal area (best effort, see below)
  - `url`: opens a web app by URL in an embedded WebKit tab (requires WebKit2; falls back to the system browser with `xdg-open` if it is missing)
- `launcher_groups[].buttons[].url`: the URL for `target: "url"` buttons
- `launcher_groups[].buttons[].maximized`: opens the external window maximized
- `quick_launchers`: ordered mini launch buttons shown next to the current-tool reload button; set it to `[]` to hide the area
- `quick_launchers[].integrated`: `true` embeds the GUI application in the panel; `false` (the default) opens it in its own window
- `terminal.command`: shell or command to open in the embedded terminal
- `webview.hardware_acceleration`: `never` (the default), `on-demand`, or `always` for web app tabs
- `session_buttons`: bottom buttons for reload, logout, reboot, and powerdown

The default display button opens MATE's display settings (`mate-display-properties`). The default screenshot button opens MATE Screenshot's interactive menu, where you can choose the capture mode and options.

The separate button at the left of the volume control opens the configuration assistant in the embedded terminal. Every click discards its previous terminal page and starts with a clean command prompt. Agent commands are remembered in `~/.config/ai-bar/config-assistant.json` (or below `XDG_CONFIG_HOME`) and offered as the default next time. Enter `edit` instead to open the active JSON file with `$VISUAL`, `$EDITOR`, or `sensible-editor`; this temporary action does not replace the remembered agent command. Agent commands receive the configuration path and instructions to discuss the requested correction, preserve unrelated settings, and validate the result.

The recording button beside the small grid button launches `macro-recorder`. The grid button at the far right of the tray tiles the normal, non-minimized windows on the monitor of the focused window in a spiral layout, starting top-to-bottom when the work area is taller than it is wide; if no external window is focused, it uses the panel monitor.

The AI tool launchers open a separate terminal tab for each command. Returning to a tool that is already open selects its tab, keeps the process running in the background, and focuses the terminal. Tabs are hidden, so the button that opened the page on screen stays highlighted, the same way the window list marks the active window. `window` and `url` buttons work the same way: the app or web app is embedded in its own tab and keeps running while you switch to another tab. Web apps keep cookies and site data across restarts, and while a web tab is active you can use `Ctrl++` or `Ctrl+=` to zoom in and `Ctrl+-` to zoom out. In the terminal, use `Ctrl+Shift+C` and `Ctrl+Shift+V` to copy and paste, or use the context menu.

AI Bar exports `SUDO_ASKPASS=/usr/local/bin/ai-bar-askpass` to its embedded terminals when the helper is installed. Agent and terminal commands can request the themed graphical password dialog with `sudo -A`; the helper never stores or logs the password. An existing `SUDO_ASKPASS` value is preserved.

A tab can be detached into a window of its own with the button above the content, for when something started in the panel turns into real work. Nothing is restarted: the terminal keeps its process and the page keeps its state, typed text included. Pressing the detach button in the separate window puts the tab back in the panel; closing that window does the same rather than ending the tool. The reload button next to it restarts the current terminal or embedded GUI tool, or reloads the current web app. The panel falls back to the first tab that is left, or to an empty area if that was the only one, and while a tool is detached its launcher button is outlined and clicking it raises the window.

No replacement tab is created to fill the gap. Tab labels are hidden, so a tab that no launcher button owns could never be reached again and its process would keep running out of sight. An embedded GUI window can be detached after ai-bar has captured and embedded its X11 window; until then, the detach button remains disabled. Detaching or reattaching it creates a new `Gtk.Socket` and reparents the same window by XID, without relaunching the process.

Web app buttons take their icon from the site's favicon once the site has been opened, and fall back to the configured `icon` when the site has none. Icons that WebKit cannot decode itself — SVG favicons, for instance — are fetched from the site and decoded with GdkPixbuf, then cached under `ai-bar/webkit/icons` so the button keeps the icon across restarts.

Login forms in a web app can be filled from the system keyring. Nothing is stored until you log in yourself: the first time you submit a login form, ai-bar asks whether to keep the credentials, and only then writes them to the keyring — never to `config.json`. They are filled in again only while the page is still on the same origin as the configured `url`, and the form is never submitted for you. Without `gir1.2-secret-1` and a running keyring, or if you decline, the feature stays out of the way.

Embedding GUI programs (`target: "window"`) re-parents the program's first window into the panel using `Gtk.Socket` and libwnck. It is best effort: single-window GTK applications work well, but some programs do not tolerate being embedded (the decoration frame may remain, or the window may stay on the desktop); in that case the launcher still opens the program on the desktop. Web apps (`target: "url"`) require the `gir1.2-webkit2-4.1` package, installed by `install.sh`.

Web views run without accelerated compositing by default. On drivers where the GBM buffer allocation fails — the proprietary NVIDIA driver, for one — WebKit does not fall back on its own and the view stays blank, with `Failed to create GBM buffer` in the session log and nothing at all in the interface. On a view the width of a panel, software rendering costs little; a failure nobody can diagnose costs a lot. Set `webview.hardware_acceleration` to `on-demand` to get WebKit's own behaviour back, or to `always`.

The embedded tray supports XApp icons and, in X11 sessions, XEmbed icons. Applets share a left-aligned grid and wrap individually when they do not fit within the available width. On Wayland and with some modern AppIndicator/StatusNotifier applications, items may not appear in the panel; in that case, the configurable `tray.items` row remains available. On X11, the Super key hides or shows the panel with a sliding animation. Super+X requests powerdown through the same session authorization flow as the Powerdown button.

## LightDM/Openbox session

The session is installed by `install.sh`. Log out of the graphical session, select `AI Bar Openbox` in the LightDM session chooser, and log in. The installer enables the GNOME Keyring user socket so the login keyring can be unlocked by LightDM; after installing, log out and back in for this to take effect. The launcher finds the command installed by `pipx`, loads the application code from the repository checkout, and uses `~/.config/ai-bar/config.json`. Keep the checkout in the same path while testing. Window decorations use the bundled `Aura Midnight` Openbox theme, copied to `~/.themes` by the installer; an existing `~/.config/openbox/rc.xml` is left untouched.

## Verification

```bash
python3 -m unittest discover -s tests
python3 -m compileall ai_bar tests
git diff --check
```

Without a graphical display, run the suite through Xvfb:

```bash
xvfb-run -a python3 -m unittest discover -s tests
```
