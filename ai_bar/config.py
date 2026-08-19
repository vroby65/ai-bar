from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


DEFAULT_CONFIG: dict[str, Any] = {
    "panel": {
        "side": "left",
        "width": 400,
        "height": "screen",
        "decorated": False,
        "keep_above": True,
        "resizable": True,
        "monitor": None,
        "launch_monitor": None,
        "reserve_space": True,
    },
    "clock": {
        "time_format": "%H:%M:%S",
        "date_format": "%A %d %B %Y",
    },
    "tray": {
        "xembed": True,
        "icon_size": 24,
        "status_refresh_seconds": 5,
        "items": [
            {
                "type": "volume",
                "label": "Volume",
                "icon": "audio-volume-high-symbolic",
                "command": ["pavucontrol", "-t", "2"],
            },
        ],
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
                    "maximized": True,
                },
                {
                    "label": "Firefox",
                    "icon": "firefox",
                    "command": ["firefox"],
                    "maximized": True,
                },
                {
                    "label": "Chrome",
                    "icon": "google-chrome",
                    "command": ["google-chrome"],
                    "maximized": True,
                },
                {
                    "label": "Caja",
                    "icon": "system-file-manager-symbolic",
                    "command": ["caja"],
                    "maximized": True,
                },
            ],
        },
        {
            "title": "tools",
            "columns": 4,
            "buttons": [
                {
                    "label": "Hermes",
                    "icon": "applications-development-symbolic",
                    "command": ["hermes"],
                    "target": "terminal",
                },
                {
                    "label": "Codex",
                    "icon": "utilities-terminal-symbolic",
                    "command": ["codex"],
                    "target": "terminal",
                },
                {
                    "label": "DS Code",
                    "icon": "accessories-text-editor-symbolic",
                    "command": ["ds-code"],
                    "target": "terminal",
                },
                {
                    "label": "terminal",
                    "icon": "utilities-terminal-symbolic",
                    "command": ["fish"],
                    "target": "terminal",
                },
            ],
        },
    ],
    "terminal": {
        "command": ["hermes"],
        "working_directory": None,
        "font": "Monospace 10",
        "scrollback_lines": 10000,
    },
    "session_buttons": [
        {
            "label": "Reload",
            "icon": "view-refresh-symbolic",
            "action": "reload",
        },
        {
            "label": "Logout",
            "icon": "system-log-out-symbolic",
            "command": ["openbox", "--exit"],
        },
        {
            "label": "Reboot",
            "icon": "system-reboot-symbolic",
            "command": ["systemctl", "reboot"],
        },
        {
            "label": "Powerdown",
            "icon": "system-shutdown-symbolic",
            "command": ["systemctl", "poweroff"],
        },
    ],
}


def default_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_CONFIG)


def default_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "ai-bar" / "config.json"
    return Path.home() / ".config" / "ai-bar" / "config.json"


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    config = default_config()
    if path is None:
        path = default_config_path()

    path = Path(path)
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            user_config = json.load(fh)
        if not isinstance(user_config, dict):
            raise ConfigError("Il file di configurazione deve contenere un oggetto JSON.")
        config = merge_config(config, user_config)

    validate_config(config)
    return config


def merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _is_monitor_reference(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value >= 0
    return isinstance(value, str) and value.strip() != ""


def validate_config(config: dict[str, Any]) -> None:
    panel = config.get("panel", {})
    side = panel.get("side")
    if side not in {"left", "right"}:
        raise ConfigError("panel.side deve essere 'left' oppure 'right'.")

    width = panel.get("width")
    if not isinstance(width, int) or width < 120:
        raise ConfigError("panel.width deve essere un intero maggiore o uguale a 120.")

    resizable = panel.get("resizable", True)
    if not isinstance(resizable, bool):
        raise ConfigError("panel.resizable deve essere true oppure false.")

    monitor = panel.get("monitor")
    if monitor is not None and not _is_monitor_reference(monitor):
        raise ConfigError(
            "panel.monitor deve essere null, un indice intero non negativo "
            "oppure il nome di un connettore (es. \"DP-1\")."
        )

    launch_monitor = panel.get("launch_monitor")
    if (launch_monitor is not None and launch_monitor != "auto"
            and not _is_monitor_reference(launch_monitor)):
        raise ConfigError(
            "panel.launch_monitor deve essere null, \"auto\", un indice "
            "intero non negativo oppure il nome di un connettore."
        )

    for group in config.get("launcher_groups", []):
        columns = group.get("columns", 1)
        if not isinstance(columns, int) or columns < 1:
            raise ConfigError("launcher_groups[].columns deve essere un intero positivo.")
        for button in group.get("buttons", []):
            target = button.get("target")
            if target is not None and target not in {"terminal", "window", "url"}:
                raise ConfigError(
                    "launcher_groups[].buttons[].target deve essere 'terminal', 'window' oppure 'url'."
                )
            if not isinstance(button.get("maximized", False), bool):
                raise ConfigError("launcher_groups[].buttons[].maximized deve essere true oppure false.")
            if target == "url":
                url = button.get("url")
                if not isinstance(url, str) or not url.strip():
                    raise ConfigError("launcher_groups[].buttons[].url deve essere un URL non vuoto.")
                if button.get("command") is not None:
                    validate_command(button.get("command"), "launcher_groups[].buttons[].command")
            else:
                validate_command(button.get("command"), "launcher_groups[].buttons[].command")

    for item in config.get("tray", {}).get("items", []):
        if not isinstance(item.get("icon_only", False), bool):
            raise ConfigError("tray.items[].icon_only deve essere true oppure false.")
        if item.get("type") == "command":
            validate_command(item.get("command"), "tray.items[].command")
        elif item.get("command") is not None:
            validate_command(item.get("command"), "tray.items[].command")

    command = config.get("terminal", {}).get("command")
    if command is not None:
        validate_command(command, "terminal.command")

    for button in config.get("session_buttons", []):
        action = button.get("action")
        if action is not None:
            if action != "reload":
                raise ConfigError("session_buttons[].action deve essere 'reload'.")
        else:
            validate_command(button.get("command"), "session_buttons[].command")


def validate_command(command: Any, field: str) -> None:
    if isinstance(command, str) and command.strip():
        return
    if isinstance(command, list) and command and all(isinstance(part, str) for part in command):
        return
    raise ConfigError(f"{field} deve essere una stringa non vuota o una lista di stringhe.")
