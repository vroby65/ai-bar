import os
import subprocess
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_pyproject_installs_ai_bar_command(self):
        with (ROOT / "pyproject.toml").open("rb") as handle:
            pyproject = tomllib.load(handle)

        self.assertEqual(pyproject["project"]["scripts"]["ai-bar"], "ai_bar.app:main")

    def test_installer_and_session_launcher_have_valid_shell_syntax(self):
        for path in (ROOT / "install.sh", ROOT / "scripts" / "ai-bar-openbox-session"):
            subprocess.run(["bash", "-n", path], check=True)

    def test_session_launcher_uses_installed_command(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            openbox = temporary / "openbox-session"
            openbox.write_text("#!/usr/bin/env bash\nsleep 0.2\n", encoding="utf-8")
            openbox.chmod(0o755)

            arguments_file = temporary / "ai-bar-arguments"
            ai_bar = temporary / "ai-bar"
            ai_bar.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    if [[ "${1:-}" == "--print-default-config" ]]; then
                        printf '{}\n'
                        exit 0
                    fi
                    printf '%s\n' "$*" > "$AI_BAR_TEST_ARGUMENTS"
                    sleep 5
                    """
                ),
                encoding="utf-8",
            )
            ai_bar.chmod(0o755)

            config_home = temporary / "config"
            environment = os.environ.copy()
            environment.update(
                {
                    "AI_BAR_BIN": str(ai_bar),
                    "AI_BAR_START_DELAY": "0",
                    "AI_BAR_TEST_ARGUMENTS": str(arguments_file),
                    "HOME": str(temporary),
                    "OPENBOX_SESSION": str(openbox),
                    "PYTHON_BIN": "/bin/false",
                    "XDG_CONFIG_HOME": str(config_home),
                }
            )

            subprocess.run(
                ["bash", ROOT / "scripts" / "ai-bar-openbox-session"],
                check=True,
                env=environment,
                timeout=5,
            )

            config_file = config_home / "ai-bar" / "config.json"
            self.assertEqual(config_file.read_text(encoding="utf-8"), "{}\n")
            self.assertEqual(
                arguments_file.read_text(encoding="utf-8").strip(),
                f"--config {config_file}",
            )

    def test_session_launcher_restarts_ai_bar_after_crash(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            openbox = temporary / "openbox-session"
            openbox.write_text("#!/usr/bin/env bash\nsleep 1.3\n", encoding="utf-8")
            openbox.chmod(0o755)

            starts_file = temporary / "ai-bar-starts"
            ai_bar = temporary / "ai-bar"
            ai_bar.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    if [[ "${1:-}" == "--print-default-config" ]]; then
                        printf '{}\n'
                        exit 0
                    fi
                    printf 'start\n' >> "$AI_BAR_TEST_STARTS"
                    if [[ "$(wc -l < "$AI_BAR_TEST_STARTS")" -eq 1 ]]; then
                        exit 1
                    fi
                    sleep 5
                    """
                ),
                encoding="utf-8",
            )
            ai_bar.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "AI_BAR_BIN": str(ai_bar),
                    "AI_BAR_START_DELAY": "0",
                    "AI_BAR_TEST_STARTS": str(starts_file),
                    "HOME": str(temporary),
                    "OPENBOX_SESSION": str(openbox),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                }
            )

            subprocess.run(
                ["bash", ROOT / "scripts" / "ai-bar-openbox-session"],
                check=True,
                env=environment,
                timeout=5,
            )

            self.assertGreaterEqual(
                starts_file.read_text(encoding="utf-8").count("start\n"),
                2,
            )


if __name__ == "__main__":
    unittest.main()
