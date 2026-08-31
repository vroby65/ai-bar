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

    def test_installer_uses_an_editable_install_for_testing(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("pipx install --editable", installer)

    def test_installer_links_commands_in_usr_local_bin(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn(
            'ln -sfn -- "$AI_BAR_BIN" /usr/local/bin/ai-bar',
            installer,
        )
        self.assertIn(
            'ln -sfn -- "$PROJECT_DIR/scripts/ai-bar-openbox-session" '
            "/usr/local/bin/ai-bar-openbox-session",
            installer,
        )
        self.assertIn(
            'ln -sfn -- "$PROJECT_DIR/scripts/ai-bar-askpass" '
            "/usr/local/bin/ai-bar-askpass",
            installer,
        )

    def test_installer_provides_the_graphical_askpass_dependency(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("    yad\n", installer)

    def test_installer_enables_gnome_keyring_for_lightdm(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")

        self.assertIn("    gnome-keyring\n", installer)
        self.assertIn("    libpam-gnome-keyring\n", installer)
        self.assertIn(
            "systemctl --user unmask gnome-keyring-daemon.service "
            "gnome-keyring-daemon.socket",
            installer,
        )
        self.assertIn(
            "systemctl --user enable gnome-keyring-daemon.socket",
            installer,
        )

    def test_installer_and_session_launcher_have_valid_shell_syntax(self):
        for path in (
            ROOT / "install.sh",
            ROOT / "scripts" / "ai-bar-openbox-session",
            ROOT / "scripts" / "ai-bar-askpass",
        ):
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

    def test_session_starts_openbox_only_after_ai_bar_tray_is_ready(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            startup_order = temporary / "startup-order"

            openbox = temporary / "openbox-session"
            openbox.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    if [[ -n "${AI_BAR_READY_FILE:-}" && -e "$AI_BAR_READY_FILE" ]]; then
                        printf 'tray-ready\n' > "$AI_BAR_TEST_STARTUP_ORDER"
                    else
                        printf 'openbox-first\n' > "$AI_BAR_TEST_STARTUP_ORDER"
                    fi
                    sleep 0.1
                    """
                ),
                encoding="utf-8",
            )
            openbox.chmod(0o755)

            ai_bar = temporary / "ai-bar"
            ai_bar.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    if [[ "${1:-}" == "--print-default-config" ]]; then
                        printf '{}\n'
                        exit 0
                    fi
                    sleep 0.1
                    touch "$AI_BAR_READY_FILE"
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
                    "AI_BAR_READY_TIMEOUT": "2",
                    "AI_BAR_TEST_STARTUP_ORDER": str(startup_order),
                    "HOME": str(temporary),
                    "OPENBOX_SESSION": str(openbox),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                    "XDG_RUNTIME_DIR": str(temporary),
                }
            )

            subprocess.run(
                ["bash", ROOT / "scripts" / "ai-bar-openbox-session"],
                check=True,
                env=environment,
                timeout=5,
            )

            self.assertEqual(startup_order.read_text(encoding="utf-8"), "tray-ready\n")

    def test_session_launcher_uses_source_checkout_when_requested(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            openbox = temporary / "openbox-session"
            openbox.write_text("#!/usr/bin/env bash\nsleep 0.2\n", encoding="utf-8")
            openbox.chmod(0o755)

            source_dir = temporary / "source"
            (source_dir / "ai_bar").mkdir(parents=True)
            (source_dir / "ai_bar" / "__main__.py").write_text(
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )

            arguments_file = temporary / "python-arguments"
            python = temporary / "python"
            python.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    printf '%s\n' "$*" > "$AI_BAR_TEST_ARGUMENTS"
                    printf '%s\n' "$PYTHONPATH" > "$AI_BAR_TEST_PYTHONPATH"
                    sleep 0.1
                    """
                ),
                encoding="utf-8",
            )
            python.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "AI_BAR_SOURCE_DIR": str(source_dir),
                    "AI_BAR_START_DELAY": "0",
                    "AI_BAR_TEST_ARGUMENTS": str(arguments_file),
                    "AI_BAR_TEST_PYTHONPATH": str(temporary / "pythonpath"),
                    "HOME": str(temporary),
                    "OPENBOX_SESSION": str(openbox),
                    "PYTHON_BIN": str(python),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                }
            )

            subprocess.run(
                ["bash", ROOT / "scripts" / "ai-bar-openbox-session"],
                check=True,
                env=environment,
                timeout=5,
            )

            self.assertEqual(
                arguments_file.read_text(encoding="utf-8").strip(),
                "-m ai_bar --config {}/ai-bar/config.json".format(temporary / "config"),
            )
            self.assertTrue((temporary / "pythonpath").read_text(encoding="utf-8").startswith(str(source_dir)))

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

    def test_session_launcher_starts_a_polkit_agent_when_available(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            openbox = temporary / "openbox-session"
            openbox.write_text("#!/usr/bin/env bash\nsleep 0.2\n", encoding="utf-8")
            openbox.chmod(0o755)

            agent_log = temporary / "polkit-agent.log"
            agent = temporary / "polkit-agent"
            agent.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    printf 'started\n' >> "$POLKIT_AGENT_TEST_LOG"
                    sleep 5
                    """
                ),
                encoding="utf-8",
            )
            agent.chmod(0o755)

            ai_bar = temporary / "ai-bar"
            ai_bar.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    if [[ "${1:-}" == "--print-default-config" ]]; then
                        printf '{}\n'
                        exit 0
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
                    "HOME": str(temporary),
                    "OPENBOX_SESSION": str(openbox),
                    "POLKIT_AUTH_AGENT": str(agent),
                    "POLKIT_AGENT_TEST_LOG": str(agent_log),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                }
            )

            subprocess.run(
                ["bash", ROOT / "scripts" / "ai-bar-openbox-session"],
                check=True,
                env=environment,
                timeout=5,
            )

            self.assertEqual(agent_log.read_text(encoding="utf-8"), "started\n")

    def test_session_launcher_handles_reboot_inside_the_login_session(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            runtime = temporary / "runtime"
            runtime.mkdir()

            openbox = temporary / "openbox-session"
            openbox.write_text("#!/usr/bin/env bash\nsleep 0.5\n", encoding="utf-8")
            openbox.chmod(0o755)

            action_log = temporary / "systemctl-action"
            systemctl = temporary / "systemctl"
            systemctl.write_text(
                "#!/usr/bin/env bash\nprintf '%s\\n' \"$1\" > \"$ACTION_LOG\"\nprintf 'Access denied\\n' >&2\nexit 7\n",
                encoding="utf-8",
            )
            systemctl.chmod(0o755)

            result_log = temporary / "session-result"
            ai_bar = temporary / "ai-bar"
            ai_bar.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    if [[ "${1:-}" == "--print-default-config" ]]; then
                        printf '{}\n'
                        exit 0
                    fi
                    kill -USR1 "$AI_BAR_SESSION_SUPERVISOR_PID"
                    for _attempt in {1..50}; do
                        if [[ -s "$AI_BAR_SESSION_RESULT" ]]; then
                            cp "$AI_BAR_SESSION_RESULT" "$RESULT_LOG"
                            sleep 5
                            exit 0
                        fi
                        sleep 0.01
                    done
                    exit 1
                    """
                ),
                encoding="utf-8",
            )
            ai_bar.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "ACTION_LOG": str(action_log),
                    "AI_BAR_BIN": str(ai_bar),
                    "AI_BAR_START_DELAY": "0",
                    "HOME": str(temporary),
                    "OPENBOX_SESSION": str(openbox),
                    "RESULT_LOG": str(result_log),
                    "SYSTEMCTL_BIN": str(systemctl),
                    "XDG_CONFIG_HOME": str(temporary / "config"),
                    "XDG_RUNTIME_DIR": str(runtime),
                }
            )

            subprocess.run(
                ["bash", ROOT / "scripts" / "ai-bar-openbox-session"],
                check=True,
                env=environment,
                timeout=5,
            )

            self.assertEqual(action_log.read_text(encoding="utf-8"), "reboot\n")
            self.assertEqual(result_log.read_text(encoding="utf-8"), "7\nAccess denied\n")


if __name__ == "__main__":
    unittest.main()
