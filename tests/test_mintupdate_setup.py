import runpy
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = textwrap.dedent("""\
class MintUpdate:
    def tray_activate(self, time=0):
        try:
            focused = self.ui_window.get_window().get_state() & Gdk.WindowState.FOCUSED
        except:
            focused = self.ui_window.is_active() and self.ui_window.get_visible()

        if focused:
            self.save_window_size()
            self.hide_window()
        else:
            self.show_window(time)
""")


class MintUpdateSetupTests(unittest.TestCase):
    def setUp(self):
        self.patch_mintupdate = runpy.run_path(
            str(ROOT / "scripts" / "patch-mintupdate.py")
        )["patch_mintupdate"]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "mintUpdate.py"
        self.source.write_text(ORIGINAL, encoding="utf-8")
        self.source.chmod(0o755)
        self.backup = self.source.with_suffix(".py.ai-bar.bak")

    def test_patched_tray_reopens_hidden_windows_with_stale_focus(self):
        self.assertTrue(self.patch_mintupdate(self.source))
        namespace = {"Gdk": SimpleNamespace(WindowState=SimpleNamespace(FOCUSED=1))}
        exec(compile(self.source.read_text(), str(self.source), "exec"), namespace)

        for visible, focused in ((False, 1), (False, 0), (True, 1), (True, 0)):
            with self.subTest(visible=visible, focused=focused):
                app = namespace["MintUpdate"]()
                app.ui_window = Mock()
                app.ui_window.get_visible.return_value = visible
                app.ui_window.get_window.return_value.get_state.return_value = focused
                app.show_window = Mock()
                app.hide_window = Mock()
                app.save_window_size = Mock()

                app.tray_activate(123)

                if visible and focused:
                    app.hide_window.assert_called_once_with()
                    app.show_window.assert_not_called()
                else:
                    app.show_window.assert_called_once_with(123)
                    app.hide_window.assert_not_called()

    def test_patch_preserves_backup_and_permissions_when_setup_is_repeated(self):
        self.assertTrue(self.patch_mintupdate(self.source))
        patched = self.source.read_text()
        modified = self.source.stat().st_mtime_ns
        backup_modified = self.backup.stat().st_mtime_ns

        self.assertFalse(self.patch_mintupdate(self.source))

        self.assertEqual(self.source.read_text(), patched)
        self.assertEqual(self.source.stat().st_mtime_ns, modified)
        self.assertEqual(self.backup.read_text(), ORIGINAL)
        self.assertEqual(self.backup.stat().st_mtime_ns, backup_modified)
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o755)

    def test_existing_backup_is_not_overwritten(self):
        self.backup.write_text("previous backup\n", encoding="utf-8")

        self.assertTrue(self.patch_mintupdate(self.source))

        self.assertEqual(self.backup.read_text(), "previous backup\n")

    def test_missing_mintupdate_is_skipped(self):
        self.source.unlink()

        self.assertFalse(self.patch_mintupdate(self.source))

        self.assertFalse(self.source.exists())
        self.assertFalse(self.backup.exists())

    def test_unrecognized_upstream_version_is_left_untouched(self):
        changed = ORIGINAL.replace("self.ui_window.get_window().get_state()", "self.window_state()")
        self.source.write_text(changed, encoding="utf-8")

        self.assertFalse(self.patch_mintupdate(self.source))

        self.assertEqual(self.source.read_text(), changed)
        self.assertFalse(self.backup.exists())


if __name__ == "__main__":
    unittest.main()
