#!/usr/bin/python3
"""Fix MintUpdate's stale window focus check when reopening from the tray."""

import shutil
import sys
from pathlib import Path


OLD = (
    "    def tray_activate(self, time=0):\n"
    "        try:\n"
    "            focused = self.ui_window.get_window().get_state() & Gdk.WindowState.FOCUSED\n"
)
NEW = OLD.replace("focused = ", "focused = self.ui_window.get_visible() and ", 1)


def patch_mintupdate(source: Path) -> bool:
    if not source.is_file():
        return False
    original = source.read_text(encoding="utf-8")
    if NEW in original:
        print("MintUpdate: correzione tray già presente.")
        return False
    if original.count(OLD) != 1:
        print("MintUpdate: versione non riconosciuta, correzione tray non applicata.", file=sys.stderr)
        return False

    backup = source.with_suffix(".py.ai-bar.bak")
    if not backup.exists():
        shutil.copy2(source, backup)
    source.write_text(original.replace(OLD, NEW, 1), encoding="utf-8")
    print(f"MintUpdate: correzione tray applicata. Backup: {backup}")
    print("La correzione sarà attiva al prossimo avvio di MintUpdate dalla sessione grafica.")
    return True


if __name__ == "__main__":
    patch_mintupdate(Path("/usr/lib/linuxmint/mintUpdate/mintUpdate.py"))
