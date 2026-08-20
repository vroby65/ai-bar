from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO


ONE_SHOT_COMMANDS = {"opencode", "hermes", "picoclaw"}


def preferences_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "ai-bar" / "config-assistant.json"
    return Path.home() / ".config" / "ai-bar" / "config-assistant.json"


def load_saved_command(path: Path) -> str | None:
    try:
        preferences = json.loads(path.read_text(encoding="utf-8"))
        saved = preferences.get("command") or preferences.get("agent")
    except (OSError, AttributeError, json.JSONDecodeError):
        return None
    if isinstance(saved, str) and saved.strip():
        return saved.strip()
    return None


def save_command(path: Path, command: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"command": command}, indent=2) + "\n", encoding="utf-8")


def ask_command(
    saved_command: str | None,
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    default = saved_command or "codex"
    while True:
        answer = input_fn(
            f"Comando [{default}] ('edit' per aprire la configurazione): "
        ).strip()
        command = answer or default
        if command.casefold() == "edit":
            return "edit"
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            print(f"Comando non valido: {exc}", file=output)
            continue
        if not argv:
            print("Comando non valido.", file=output)
        elif which(argv[0]) is None:
            print(f"{argv[0]} non è installato o non è nel PATH.", file=output)
        else:
            return command


def assistant_prompt(config_path: Path) -> str:
    return f"""Sei l'assistente di configurazione di AI-bar.
Lavora esclusivamente sul file JSON {config_path.resolve()}; non modificare il codice sorgente o altri file.
Inizia leggendo la configurazione, poi chiedi all'utente quale comportamento vuole correggere e procedi attraverso un dialogo. Prima di modificare, esplicita le assunzioni rilevanti.
Mantieni le personalizzazioni esistenti e cambia soltanto le chiavi necessarie. Il formato valido si ricava da `python3 -m ai_bar --print-default-config` e da `ai_bar.config.load_config`.
Prima di concludere valida il file con `ai_bar.config.load_config` e riassumi con precisione le modifiche effettuate."""


def command_name(command: str) -> str:
    return Path(shlex.split(command)[0]).name.casefold()


def agent_command_argv(command: str, prompt: str) -> list[str]:
    argv = shlex.split(command)
    name = Path(argv[0]).name.casefold()
    if name == "opencode":
        return [argv[0], "run", *argv[1:], prompt]
    if name == "ag":
        return [*argv, "--prompt-interactive", prompt]
    if name == "hermes":
        return [argv[0], "chat", *argv[1:], "--query", prompt]
    if name == "picoclaw":
        return [argv[0], "agent", *argv[1:], "--message", prompt]
    return [*argv, prompt]


def editor_argv(config_path: Path, editor: str | None = None) -> list[str]:
    editor_command = editor or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    argv = shlex.split(editor_command) if editor_command else ["sensible-editor"]
    return [*argv, str(config_path.resolve())]


def run_dialogue(
    command: str,
    initial_prompt: str,
    *,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    run_command: Callable[..., object] = subprocess.run,
) -> int:
    conversation = initial_prompt
    while True:
        try:
            completed = run_command(
                agent_command_argv(command, conversation),
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            print(f"Impossibile avviare {command}: {exc}", file=output)
            return 1

        response = (completed.stdout or completed.stderr or "").strip()
        if response:
            print(f"\n{command_name(command)}: {response}\n", file=output)
        if completed.returncode != 0:
            print(f"{command} è terminato con stato {completed.returncode}.", file=output)
            return int(completed.returncode)

        answer = input_fn("Tu (/fine per terminare): ").strip()
        if answer.casefold() in {"/fine", "/esci", "/exit"}:
            return 0
        if not answer:
            continue
        conversation += (
            f"\n\nAssistente: {response}\nUtente: {answer}"
            "\nContinua il dialogo e applica al file soltanto le correzioni concordate."
        )


def run(
    config_path: Path,
    *,
    preferences_file: Path | None = None,
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
    which: Callable[[str], str | None] = shutil.which,
    execvp: Callable[[str, list[str]], object] = os.execvp,
) -> int:
    saved_path = preferences_file or preferences_path()
    command = ask_command(
        load_saved_command(saved_path),
        input_fn=input_fn,
        output=output,
        which=which,
    )
    if command == "edit":
        argv = editor_argv(config_path)
        execvp(argv[0], argv)
        return 0

    save_command(saved_path, command)
    print(f"\nAvvio {command}...\n", file=output)
    prompt = assistant_prompt(config_path)
    if command_name(command) in ONE_SHOT_COMMANDS:
        return run_dialogue(command, prompt, input_fn=input_fn, output=output)
    argv = agent_command_argv(command, prompt)
    execvp(argv[0], argv)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assistente interattivo per configurare AI-bar")
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return run(args.config)
    except (EOFError, KeyboardInterrupt):
        print("\nSelezione annullata.")
        return 130
    except (OSError, RuntimeError) as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
