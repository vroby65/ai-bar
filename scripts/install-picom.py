#!/usr/bin/env python3
"""Install the Openbox compositor profile, preserving previous user files."""

import json
import os
import shutil
from pathlib import Path


project = Path(__file__).resolve().parents[1]
config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
shader = config_home / "picom/csd-border.glsl"

for source, destination in (
    (project / "packaging/picom/csd-border.glsl", shader),
    (project / "packaging/picom/picom.conf", config_home / "picom.conf"),
    (project / "packaging/picom/picom.desktop", config_home / "autostart/picom.desktop"),
):
    contents = source.read_text(encoding="utf-8")
    if destination.name == "picom.conf":
        contents = contents.replace("@SHADER_PATH@", json.dumps(str(shader), ensure_ascii=False)[1:-1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_text(encoding="utf-8") == contents:
            continue
        backup = destination.with_name(destination.name + ".ai-bar.bak")
        if not backup.exists():
            shutil.copy2(destination, backup)
            print(f"Backup: {backup}")
    destination.write_text(contents, encoding="utf-8")
    print(f"Installato: {destination}")
