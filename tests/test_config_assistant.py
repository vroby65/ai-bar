import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_bar.config_assistant import (
    agent_command_argv,
    assistant_prompt,
    ask_command,
    editor_argv,
    load_saved_command,
    run,
    run_dialogue,
)


class ConfigAssistantTests(unittest.TestCase):
    def test_asks_for_the_command_without_showing_an_agent_menu(self):
        prompts = []

        command = ask_command(
            None,
            input_fn=lambda prompt: prompts.append(prompt) or "claude",
            output=io.StringIO(),
            which=lambda command: "/bin/" + command,
        )

        self.assertEqual(command, "claude")
        self.assertIn("Comando", prompts[0])
        self.assertNotIn("1.", prompts[0])

    def test_saved_command_is_the_default_choice(self):
        command = ask_command(
            "hermes",
            input_fn=lambda _prompt: "",
            output=io.StringIO(),
            which=lambda _command: "/bin/agent",
        )

        self.assertEqual(command, "hermes")

    def test_edit_is_accepted_without_an_external_agent(self):
        self.assertEqual(
            ask_command(
                None,
                input_fn=lambda _prompt: "edit",
                output=io.StringIO(),
                which=lambda _command: None,
            ),
            "edit",
        )

    def test_known_agent_commands_receive_the_initial_instruction(self):
        prompt = "Correggi la configurazione"
        expected = {
            "codex": ["codex", prompt],
            "claude": ["claude", prompt],
            "opencode": ["opencode", "run", prompt],
            "ag": ["ag", "--prompt-interactive", prompt],
            "hermes": ["hermes", "chat", "--query", prompt],
            "picoclaw": ["picoclaw", "agent", "--message", prompt],
        }

        for command, argv in expected.items():
            with self.subTest(command=command):
                self.assertEqual(agent_command_argv(command, prompt), argv)

    def test_one_shot_agent_is_wrapped_in_a_continuing_dialogue(self):
        answers = iter(["Sposta il pannello a destra", "/fine"])
        run_command = Mock(
            side_effect=[
                SimpleNamespace(returncode=0, stdout="Cosa vuoi correggere?\n", stderr=""),
                SimpleNamespace(returncode=0, stdout="Configurazione corretta.\n", stderr=""),
            ]
        )

        result = run_dialogue(
            "hermes",
            "Istruzione iniziale",
            input_fn=lambda _prompt: next(answers),
            output=io.StringIO(),
            run_command=run_command,
        )

        self.assertEqual(result, 0)
        self.assertEqual(run_command.call_count, 2)
        second_prompt = run_command.call_args_list[1].args[0][-1]
        self.assertIn("Assistente: Cosa vuoi correggere?", second_prompt)
        self.assertIn("Utente: Sposta il pannello a destra", second_prompt)

    def test_edit_opens_the_active_config_in_the_configured_editor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            preferences_path = root / "assistant.json"
            execvp = Mock()

            with patch.dict("os.environ", {"VISUAL": "nano -w"}, clear=True):
                result = run(
                    config_path,
                    preferences_file=preferences_path,
                    input_fn=lambda _prompt: "edit",
                    output=io.StringIO(),
                    which=lambda _command: None,
                    execvp=execvp,
                )

            self.assertEqual(result, 0)
            self.assertEqual(editor_argv(config_path, "nano -w"), ["nano", "-w", str(config_path)])
            execvp.assert_called_once_with("nano", ["nano", "-w", str(config_path)])
            self.assertEqual(
                json.loads(preferences_path.read_text(encoding="utf-8")),
                {"command": "edit"},
            )

    def test_run_persists_choice_and_starts_agent_with_instructions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text("{}\n", encoding="utf-8")
            preferences_path = root / "assistant.json"
            execvp = Mock()

            result = run(
                config_path,
                preferences_file=preferences_path,
                input_fn=lambda _prompt: "codex",
                output=io.StringIO(),
                which=lambda _command: "/bin/codex",
                execvp=execvp,
            )

            self.assertEqual(result, 0)
            self.assertEqual(
                json.loads(preferences_path.read_text(encoding="utf-8")),
                {"command": "codex"},
            )
            argv = execvp.call_args.args[1]
            self.assertEqual(argv[0], "codex")
            self.assertIn(str(config_path), argv[-1])
            self.assertIn("chiedi all'utente", argv[-1])

    def test_load_saved_command_accepts_legacy_agent_preference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "assistant.json"
            path.write_text('{"agent": "hermes"}\n', encoding="utf-8")

            self.assertEqual(load_saved_command(path), "hermes")

    def test_prompt_limits_work_to_active_configuration(self):
        prompt = assistant_prompt(Path("/tmp/config.json"))

        self.assertIn("/tmp/config.json", prompt)
        self.assertIn("non modificare il codice sorgente", prompt)
        self.assertIn("valida", prompt)


if __name__ == "__main__":
    unittest.main()
