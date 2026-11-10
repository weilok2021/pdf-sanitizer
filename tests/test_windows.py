"""The Windows right-click entry, "Send to > Sanitize document".

The pure tests run anywhere. The others need WSL, and the end-to-end tests
also need Docker and a built sandbox.
"""

import os
import secrets
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from pdf_sanitizer import sandbox, windows
from tests import fixtures

ROOT = Path(__file__).resolve().parent.parent
IN_WSL = windows.in_wsl() and shutil.which("powershell.exe") is not None
DOCKER_READY = sandbox.docker_available() and sandbox.image_exists(sandbox.image_tag())

# Valid Windows file names an attacker could choose. If a name ever went
# through cmd.exe or a .bat file, the first one would run "mkdir PWNED".
HOSTILE_NAMES = [
    "a&mkdir PWNED&.pdf",
    "space name (1).pdf",
    "中文 书名.pdf",
    "100% it's ^ok!.pdf",
    "semi;colon,comma=.pdf",
]


class ShortcutArgumentsTest(unittest.TestCase):
    def test_paths_are_quoted(self):
        self.assertEqual(
            windows.shortcut_arguments("/home/me/my tools/pdf-sanitizer", "Ubuntu"),
            '--cd ~ -d Ubuntu -e "/home/me/my tools/pdf-sanitizer" --from-windows --pause',
        )

    def test_double_quote_is_refused(self):
        with self.assertRaises(windows.IntegrationError):
            windows.shortcut_arguments('/home/me/a"b/pdf-sanitizer', "Ubuntu")

    def test_unusual_distro_name_is_refused(self):
        for distro in ('Ubuntu"', "Ubuntu --exec rm", "Ubuntu&calc", ""):
            with self.subTest(distro=distro), self.assertRaises(windows.IntegrationError):
                windows.shortcut_arguments("/home/me/pdf-sanitizer", distro)

    def test_powershell_literal_escapes_its_quote(self):
        self.assertEqual(windows._ps_literal("it's"), "'it''s'")


class WindowsFolderTest(unittest.TestCase):
    """Each test gets a fresh folder under the user's Windows temp folder."""

    def setUp(self):
        local = windows._powershell("[Environment]::GetFolderPath('LocalApplicationData')")
        self.win_dir = f"{local}\\Temp\\pdf-sanitizer-test-{secrets.token_hex(4)}"
        self.dir = Path(windows.to_wsl_path(self.win_dir))
        self.dir.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.dir, True)


@unittest.skipUnless(IN_WSL, "needs WSL with Windows interop")
class WslTest(WindowsFolderTest):
    def test_hostile_names_survive_the_path_round_trip(self):
        for name in HOSTILE_NAMES:
            with self.subTest(name=name):
                (self.dir / name).write_bytes(b"x")
                win = f"{self.win_dir}\\{name}"
                wsl = windows.to_wsl_path(win)
                self.assertEqual(Path(wsl), self.dir / name)
                self.assertEqual(windows.to_windows_path(wsl).lower(), win.lower())

    def test_shortcut_is_created_read_and_removed(self):
        entry = ROOT / "pdf-sanitizer"
        distro = os.environ["WSL_DISTRO_NAME"]
        path = windows.install_shortcut(entry, distro, folder=self.win_dir)
        self.assertTrue(path.endswith(windows.SHORTCUT_NAME))
        target, arguments = windows.read_shortcut(folder=self.win_dir)
        self.assertTrue(target.lower().endswith("\\system32\\wsl.exe"), target)
        self.assertEqual(arguments, windows.shortcut_arguments(entry, distro))
        self.assertEqual(windows.remove_shortcut(folder=self.win_dir), path)
        self.assertIsNone(windows.read_shortcut(folder=self.win_dir))


@unittest.skipUnless(IN_WSL and DOCKER_READY, "needs WSL, Docker and a built sandbox")
class SendToEndToEndTest(WindowsFolderTest):
    def test_explorer_style_launch_with_hostile_names(self):
        for name in HOSTILE_NAMES:
            (self.dir / name).write_bytes(fixtures.risky_pdf())

        # File Explorer starts the shortcut's target with its arguments,
        # then each chosen file's full path in double quotes.
        arguments = windows.shortcut_arguments(ROOT / "pdf-sanitizer", os.environ["WSL_DISTRO_NAME"])
        arguments = arguments.replace(" --pause", "")  # no one is there to press Enter
        command_line = arguments + " " + " ".join(f'"{self.win_dir}\\{n}"' for n in HOSTILE_NAMES)
        log = f"{self.win_dir}\\run.log"
        script = (
            "$p = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\\wsl.exe')"
            f" -ArgumentList {windows._ps_literal(command_line)}"
            f" -WorkingDirectory {windows._ps_literal(self.win_dir)}"
            f" -RedirectStandardOutput {windows._ps_literal(log)}"
            f" -RedirectStandardError {windows._ps_literal(log + '.err')}"
            " -NoNewWindow -Wait -PassThru\n"
            "Write-Output $p.ExitCode\n"
        )
        exit_code = windows._powershell(script, timeout=600)
        output = (self.dir / "run.log").read_text(encoding="utf-8", errors="replace")

        self.assertEqual(exit_code, "0", output)
        self.assertIn(f"{len(HOSTILE_NAMES)} of {len(HOSTILE_NAMES)} sanitized.", output)
        for name in HOSTILE_NAMES:
            self.assertTrue((self.dir / f"{Path(name).stem}.safe.pdf").exists(), name)
        # Messages show Windows paths, which is what a File Explorer user knows.
        self.assertIn(f"Safe copy: {self.win_dir}\\", output)
        # Nothing ran a command hidden in a file name.
        self.assertNotIn("PWNED", os.listdir(self.dir))
        self.assertFalse((Path.home() / "PWNED").exists())

    def test_pause_waits_for_enter_and_keeps_messages(self):
        missing = f"{self.win_dir}\\missing.pdf"
        result = subprocess.run(
            [sys.executable, "-I", str(ROOT / "pdf-sanitizer"), "--from-windows", "--pause", missing],
            input="\n", capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("unreadable file", result.stdout)
        self.assertIn("Press Enter to close this window.", result.stdout)


if __name__ == "__main__":
    unittest.main()
