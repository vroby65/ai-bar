# AI-bar

A GTK side panel similar to a desktop bar, with the date and time at the top, a tray/status area with a window list, two configurable launcher groups, and an embedded terminal at the bottom.

## Installation

```bash
./install.sh
```

Run the script as a regular user, without `sudo`: it requests elevated privileges only to install the system packages and session. The installer:

- installs the dependencies on Debian, Ubuntu, or Linux Mint
- installs the `ai-bar` command with `pipx`
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

## Configuration

The default file is `~/.config/ai-bar/config.json`. You can start with `config.example.json` and customize:

- `panel.side`: `left` or `right`
- `panel.width`: panel width, default `400`
- `panel.resizable`: enables dragging the side edge to change the width
- `panel.monitor`: which monitor holds the panel, by index or connector name such as `DP-1`; unset uses the primary one
- `panel.launch_monitor`: where windows started from the panel should appear, by index or connector name, or `auto` for the primary monitor; unset leaves placement to the window manager
- `tray.items`: side items such as volume, Telegram, or custom commands
- `launcher_groups`: groups of buttons, icons, and commands
- `launcher_groups[].buttons[].target`: use `terminal` to run the command in the embedded terminal
- `launcher_groups[].buttons[].maximized`: opens the external window maximized
- `terminal.command`: shell or command to open in the embedded terminal
- `session_buttons`: bottom buttons for reload, logout, reboot, and powerdown

The AI tool launchers open a separate terminal tab for each command. Returning to a tool that is already open selects its tab, keeps the process running in the background, and focuses the terminal. In the terminal, use `Ctrl+Shift+C` and `Ctrl+Shift+V` to copy and paste, or use the context menu.

The embedded tray supports XApp icons and, in X11 sessions, XEmbed icons. Applets share a left-aligned grid and wrap individually when they do not fit within the available width. On Wayland and with some modern AppIndicator/StatusNotifier applications, items may not appear in the panel; in that case, the configurable `tray.items` row remains available. On X11, the Super key hides or shows the panel with a sliding animation.

## LightDM/Openbox session

The session is installed by `install.sh`. Log out of the graphical session, select `AI Bar Openbox` in the LightDM session chooser, and log in. The launcher finds the command installed by `pipx` and uses `~/.config/ai-bar/config.json` without depending on the repository checkout. Window decorations use the bundled `Aura Midnight` Openbox theme, copied to `~/.themes` by the installer; an existing `~/.config/openbox/rc.xml` is left untouched.

## Verification

```bash
python3 -m unittest discover -s tests
python3 -m compileall ai_bar tests
```
