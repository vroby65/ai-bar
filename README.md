# AI-bar

A GTK side panel similar to a desktop bar, with the date and time at the top, a tray/status area with a window list, two configurable launcher groups, and an embedded terminal at the bottom.

## Installation

```bash
./install.sh
```

Run the script as a regular user, without `sudo`: it requests elevated privileges only to install the system packages and session. The installer:

- installs the dependencies on Debian, Ubuntu, or Linux Mint
- installs the `ai-bar` command with `pipx` in editable mode
- creates `~/.config/ai-bar/config.json` if it does not exist
- installs the `Aura Midnight` Openbox theme into `~/.themes` and creates `~/.config/openbox/rc.xml` pointing to it if it does not already exist
- installs the `AI Bar Openbox` session for the login manager

## Usage

After installation:

```bash
ai-bar
ai-bar --config ~/.config/ai-bar/config.json
```

To run it directly from the checkout during development:

```bash
python3 -m ai_bar --config config.example.json
```

The editable installation loads Python code directly from this checkout. After a code change, use the `Reload` button to test it without running the installer again.

## Configuration

The default file is `~/.config/ai-bar/config.json`. You can start with `config.example.json` and customize:

- `panel.side`: `left` or `right`
- `panel.width`: panel width, default `400`
- `panel.resizable`: enables dragging the side edge to change the width
- `panel.monitor`: which monitor holds the panel, by index or connector name such as `DP-1`; unset uses the primary one
- `panel.launch_monitor`: where windows started from the panel should appear, by index or connector name, or `auto` for the primary monitor; unset leaves placement to the window manager
- `tray.items`: side items such as volume, Telegram, or custom commands
- `tray.items[].icon_only`: `true` shows only the icon for that item, without the text label
- `launcher_groups`: groups of buttons, icons, and commands
- `launcher_groups[].buttons[].target`: where the button opens its content — all inside the panel:
  - `terminal` (default): runs the command in an embedded terminal tab
  - `window`: launches a GUI program and embeds its window in a tab of the terminal area (best effort, see below)
  - `url`: opens a web app by URL in an embedded WebKit tab (requires WebKit2; falls back to the system browser with `xdg-open` if it is missing)
- `launcher_groups[].buttons[].url`: the URL for `target: "url"` buttons
- `launcher_groups[].buttons[].maximized`: opens the external window maximized
- `terminal.command`: shell or command to open in the embedded terminal
- `webview.hardware_acceleration`: `never` (the default), `on-demand`, or `always` for web app tabs
- `session_buttons`: bottom buttons for reload, logout, reboot, and powerdown

The separate button at the left of the volume control opens the configuration assistant in the embedded terminal. Every click discards its previous terminal page and starts with a clean command prompt. Agent commands are remembered in `~/.config/ai-bar/config-assistant.json` (or below `XDG_CONFIG_HOME`) and offered as the default next time. Enter `edit` instead to open the active JSON file with `$VISUAL`, `$EDITOR`, or `sensible-editor`; this temporary action does not replace the remembered agent command. Agent commands receive the configuration path and instructions to discuss the requested correction, preserve unrelated settings, and validate the result.

The AI tool launchers open a separate terminal tab for each command. Returning to a tool that is already open selects its tab, keeps the process running in the background, and focuses the terminal. `window` and `url` buttons work the same way: the app or web app is embedded in its own tab and keeps running while you switch to another tab. Web apps keep cookies and site data across restarts, and while a web tab is active you can use `Ctrl++` or `Ctrl+=` to zoom in and `Ctrl+-` to zoom out. In the terminal, use `Ctrl+Shift+C` and `Ctrl+Shift+V` to copy and paste, or use the context menu.

Web app buttons take their icon from the site's favicon once the site has been opened, and fall back to the configured `icon` when the site has none. Icons that WebKit cannot decode itself — SVG favicons, for instance — are fetched from the site and decoded with GdkPixbuf, then cached under `ai-bar/webkit/icons` so the button keeps the icon across restarts.

Login forms in a web app can be filled from the system keyring. Nothing is stored until you log in yourself: the first time you submit a login form, ai-bar asks whether to keep the credentials, and only then writes them to the keyring — never to `config.json`. They are filled in again only while the page is still on the same origin as the configured `url`, and the form is never submitted for you. Without `gir1.2-secret-1` and a running keyring, or if you decline, the feature stays out of the way.

Embedding GUI programs (`target: "window"`) re-parents the program's first window into the panel using `Gtk.Socket` and libwnck. It is best effort: single-window GTK applications work well, but some programs do not tolerate being embedded (the decoration frame may remain, or the window may stay on the desktop); in that case the launcher still opens the program on the desktop. Web apps (`target: "url"`) require the `gir1.2-webkit2-4.1` package, installed by `install.sh`.

Web views run without accelerated compositing by default. On drivers where the GBM buffer allocation fails — the proprietary NVIDIA driver, for one — WebKit does not fall back on its own and the view stays blank, with `Failed to create GBM buffer` in the session log and nothing at all in the interface. On a view the width of a panel, software rendering costs little; a failure nobody can diagnose costs a lot. Set `webview.hardware_acceleration` to `on-demand` to get WebKit's own behaviour back, or to `always`.

The embedded tray supports XApp icons and, in X11 sessions, XEmbed icons. Applets share a left-aligned grid and wrap individually when they do not fit within the available width. On Wayland and with some modern AppIndicator/StatusNotifier applications, items may not appear in the panel; in that case, the configurable `tray.items` row remains available. On X11, the Super key hides or shows the panel with a sliding animation.

## LightDM/Openbox session

The session is installed by `install.sh`. Log out of the graphical session, select `AI Bar Openbox` in the LightDM session chooser, and log in. The launcher finds the command installed by `pipx`, loads the application code from the repository checkout, and uses `~/.config/ai-bar/config.json`. Keep the checkout in the same path while testing. Window decorations use the bundled `Aura Midnight` Openbox theme, copied to `~/.themes` by the installer; an existing `~/.config/openbox/rc.xml` is left untouched.

## Verification

```bash
python3 -m unittest discover -s tests
python3 -m compileall ai_bar tests
```
