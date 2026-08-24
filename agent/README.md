# Guide for AI agents

This is the operating guide for adapting or modifying `ai-bar`. Before making a
change, also read `README.md`, the relevant source file, and its corresponding tests.
The information here describes the code in the repository; always inspect the
current version and diff before relying on a detail.

## Purpose and boundaries

`ai-bar` is a configurable GTK 3 side panel designed primarily for an Openbox/X11
session. It displays a clock, status and tray items, active windows, launchers,
embedded VTE terminals, WebKit web apps, and session commands.

Keep every change small and traceable to the request:

- state any assumptions that could affect the result;
- choose the simplest implementation that satisfies the requested behavior;
- write a test that reproduces a bug or describes a new capability before coding;
- do not refactor, rename, or reformat adjacent code without a concrete need;
- preserve the user's existing changes and do not include unrelated files;
- do not add unrequested compatibility, options, or handling for impossible cases.

## Repository map

- `ai_bar/app.py`: application entrypoint, GTK window, CSS, launchers, VTE terminals,
  embedded windows and web views, detached pages, monitors, volume, favicons,
  keyring integration, and session actions.
- `ai_bar/config.py`: default configuration, merge with user JSON, and validation.
  This is the authoritative source for the runtime schema.
- `ai_bar/config_assistant.py`: dialogue that lets an external agent or editor modify
  only the active configuration file.
- `ai_bar/xapp_tray.py`: XApp indicator integration.
- `ai_bar/xembed_tray.py`: XEmbed tray icon host for X11.
- `ai_bar/__main__.py`: forwards execution to `ai_bar.app:main`.
- `config.example.json`: user-facing example that must stay aligned with public
  options in `DEFAULT_CONFIG`.
- `install.sh`: Debian/Ubuntu/Mint dependencies and editable `pipx` installation.
- `scripts/ai-bar-openbox-session`: starts Openbox, supervises and restarts `ai-bar`,
  and safely forwards reboot and power-off requests.
- `packaging/`: desktop session and Openbox theme installed by the project.
- `tests/`: `unittest` tests organized by module or subsystem.

Do not modify `ai_bar.egg-info/`, caches, WebKit data, or configuration under the
home directory to implement repository behavior.

## Runtime flow

1. `python3 -m ai_bar` calls `ai_bar.app:main`.
2. `load_config` starts from a deep copy of `DEFAULT_CONFIG`, applies user JSON with
   a recursive dictionary merge, and validates the result.
3. `AiBarWindow` builds the panel, registers GTK/GLib callbacks, and initializes the
   available optional integrations.
4. The terminal notebook hides its tabs. Launcher buttons provide access to pages,
   which use keys produced by `launcher_page_key` and `terminal_session_key`.
5. The Openbox session runs and, when needed, restarts the `ai-bar` process from
   either the source checkout or the installed command.

The default user configuration is `$XDG_CONFIG_HOME/ai-bar/config.json`, or
`~/.config/ai-bar/config.json`. WebKit cookies, favicons, and icon cache are runtime
data stored under `$XDG_DATA_HOME/ai-bar/webkit`, or
`~/.local/share/ai-bar/webkit`. Web credentials must remain in the Secret Service
keyring and must never appear in JSON, logs, tests, or the repository.

## Invariants to preserve

### GTK and concurrency

- The project uses GTK 3 and VTE 2.91 through PyGObject. Update GTK widgets and state
  on the main thread; return from a worker thread through `GLib.idle_add`.
- `Wnck`, `WebKit2`, `Secret`, and `python-xlib` are optional code integrations. A
  missing integration must disable only its related capability or use the existing
  fallback.
- Periodic GLib handlers must return the correct boolean, and registered resources
  must be stopped in `_on_destroy`.

### Pages and launchers

- `self.terminals`, `self.embedded`, `self.launcher_buttons`, and `self.detached`
  represent the same logical identities from different viewpoints. Keep all maps in
  sync when a page is added, replaced, detached, or removed.
- Notebook tabs are hidden, so do not create pages without a way to reach them. If
  the last page is detached, the panel area remains empty.
- Closing a detached window returns its page to the panel instead of ending the
  session. A `Gtk.Socket`-based page cannot be detached.
- Launcher targets have distinct semantics: no `target` launches an external
  program; `terminal` uses VTE; `window` attempts to embed an X11 window; `url` uses
  WebKit and falls back to the system browser when WebKit is unavailable.
- Commands accept either a shell string or an argument list. Use
  `command_to_shell_line` and `terminal_argv` instead of rebuilding shell and quoting
  behavior elsewhere.

### X11, monitors, and session actions

- Vertical geometry uses the monitor work area so the panel does not cover reserved
  docks; overflowing content scrolls vertically.
- EWMH struts and XEmbed are X11-specific. Do not attempt to apply them on Wayland,
  and preserve the existing fallbacks.
- The panel monitor and launch monitor are separate concepts. If a configured
  monitor does not exist, preserve the fallback and one-time warning.
- Reboot and power-off must not run directly as ordinary launchers. They go through
  the session supervisor and the existing authorization flow.

### Web content and sensitive data

- Credential filling and favicon downloads are restricted to the same origin as the
  configured URL. Do not widen this check without an explicit request and security
  tests.
- Preserve favicon timeouts, size limits, and fallbacks; downloads remain outside
  the GTK thread.
- Never store passwords outside the keyring or put real secrets in tests.

## How to make common changes

### Add or change configuration

Update, in the order required by the test:

1. a test in `tests/test_config.py` for the default, merge, and invalid input;
2. `DEFAULT_CONFIG` and `validate_config` in `ai_bar/config.py`;
3. the consumer in `ai_bar/app.py` or the relevant module;
4. `config.example.json` and the Configuration section of `README.md`.

Loading an older partial configuration must continue to work through defaults. A
JSON list replaces the default list; its elements are not merged individually.

### Change the panel interface or behavior

Move pure logic into a small testable function only when the separation genuinely
makes the behavior clearer. For widgets, construct GTK instances in tests, emit
signals, and destroy the widgets afterward. Preserve existing CSS classes and style
unless the request explicitly concerns appearance.

For terminals, web views, or embedded windows, add tests for the page key, notebook
selection, and state maps. A test that covers only the button click does not cover
the full lifecycle.

### Change tray or desktop integrations

Work in `xapp_tray.py` or `xembed_tray.py` when behavior belongs to the tray
protocol, not in `app.py`. Preserve clean removal of `FlowBoxChild` instances and
host registration and shutdown. For geometry, window activation, and struts, cover
the relevant multi-monitor cases, fallbacks, and X11/Wayland differences separately.

### Change installation or session behavior

Keep `install.sh` limited to the declared distributions and preserve
`pipx --editable --system-site-packages`, which is required for system GI modules.
Every shell change requires at least `bash -n` and the tests in
`tests/test_packaging.py`. Never perform a real logout, reboot, or power-off during
tests.

## Verification

Run from the repository root:

```bash
python3 -m unittest discover -s tests
python3 -m compileall ai_bar tests
git diff --check
```

In an environment without a display, use:

```bash
xvfb-run -a python3 -m unittest discover -s tests
```

For a visible change, if a graphical environment and the dependencies are
available, supplement the automated tests with a focused launch:

```bash
python3 -m ai_bar --config config.example.json
```

Check only the affected flow: panel side, resizing and scrolling, external,
terminal, window, or web launchers, detaching and returning pages, tray behavior, or
session commands. Do not use manual testing as a substitute for an automated
regression test.

## Completion criteria

A change is ready when the requested behavior is covered, the suite and compilation
pass, `git diff --check` is clean, documentation and the example are aligned when a
public surface changes, and the diff contains no unrelated modifications. Report
checks that could not be run and explain why instead of claiming they passed.
