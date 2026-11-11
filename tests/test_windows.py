"""The Windows right-click entries: "Open with > pdf-sanitizer" and
"Send to > Sanitize document".

The pure tests run anywhere. The others need WSL, and the end-to-end tests
also need Docker and a built sandbox. The Open with tests register a
made-up file type and ending, never the real .pdf entry.
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


class OpenWithArgumentsTest(unittest.TestCase):
    def test_file_path_is_quoted(self):
        self.assertEqual(
            windows.open_with_arguments("/home/me/my tools/pdf-sanitizer", "Ubuntu"),
            '--cd ~ -d Ubuntu -e "/home/me/my tools/pdf-sanitizer" --from-windows --pause "%1"',
        )

    def test_pause_can_be_left_out(self):
        self.assertNotIn("--pause", windows.open_with_arguments("/home/me/pdf-sanitizer", "Ubuntu", pause=False))

    def test_percent_in_tool_path_is_refused(self):
        # Explorer would read "%1" or "%L" in the tool's path as a placeholder.
        with self.assertRaises(windows.IntegrationError):
            windows.open_with_arguments("/home/me/100%1/pdf-sanitizer", "Ubuntu")

    def test_shortcut_rules_still_apply(self):
        with self.assertRaises(windows.IntegrationError):
            windows.open_with_arguments('/home/me/a"b/pdf-sanitizer', "Ubuntu")
        with self.assertRaises(windows.IntegrationError):
            windows.open_with_arguments("/home/me/pdf-sanitizer", "Ubuntu&calc")


# Asks Windows the two questions File Explorer asks: which apps does the
# Open with menu list for an ending (SHAssocEnumHandlers), and what runs
# when one is chosen (ShellExecuteEx with the app's type, which fills in "%1").
SHELL_HELPER = r'''
Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
[ComImport, Guid("F04061AC-1659-4a3f-A954-775AA57FC083"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAssocHandler {
  [PreserveSig] int GetName([MarshalAs(UnmanagedType.LPWStr)] out string name);
  [PreserveSig] int GetUIName([MarshalAs(UnmanagedType.LPWStr)] out string name);
}
[ComImport, Guid("973810ae-9599-4b88-9e4d-6ee98c9552da"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IEnumAssocHandlers {
  [PreserveSig] int Next(uint count, [MarshalAs(UnmanagedType.Interface)] out IAssocHandler handler, out uint fetched);
}
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct ShellExecuteInfo {
  public int cbSize; public uint fMask; public IntPtr hwnd;
  public string lpVerb; public string lpFile; public string lpParameters; public string lpDirectory;
  public int nShow; public IntPtr hInstApp; public IntPtr lpIDList; public string lpClass;
  public IntPtr hkeyClass; public uint dwHotKey; public IntPtr hIcon; public IntPtr hProcess;
}
public static class ShellHelper {
  [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
  static extern int SHAssocEnumHandlers(string ending, int filter, out IEnumAssocHandlers handlers);
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  static extern bool ShellExecuteEx(ref ShellExecuteInfo info);
  [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint ms);
  [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr handle, out uint code);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

  public static string[] OpenWithNames(string ending) {
    var names = new List<string>();
    IEnumAssocHandlers handlers;
    if (SHAssocEnumHandlers(ending, 1, out handlers) != 0) return names.ToArray();  // 1: recommended
    IAssocHandler handler; uint fetched;
    while (handlers.Next(1, out handler, out fetched) == 0 && fetched == 1) {
      string name; handler.GetUIName(out name); names.Add(name);
    }
    return names.ToArray();
  }

  public static long Launch(string progid, string file, uint ms) {
    var info = new ShellExecuteInfo();
    info.cbSize = Marshal.SizeOf(info);
    info.fMask = 0x1 | 0x40 | 0x100 | 0x400;  // class name, keep process, no async, no UI
    info.lpVerb = "open"; info.lpFile = file; info.lpClass = progid; info.nShow = 0;
    if (!ShellExecuteEx(ref info)) return -Marshal.GetLastWin32Error();
    uint code = 0;
    if (WaitForSingleObject(info.hProcess, ms) == 0) GetExitCodeProcess(info.hProcess, out code); else code = 9999;
    CloseHandle(info.hProcess);
    return code;
  }
}
"@
'''


class FakeFileType:
    """A made-up file type and ending, removed again when the test ends."""

    def __init__(self, test):
        token = secrets.token_hex(4)
        self.progid = f"PdfSanitizer.Test{token}"
        self.ending = f".pstest{token}"
        # Cleanups run last first: the entry, then the ending's own key.
        test.addCleanup(self._forget_ending)
        test.addCleanup(windows.remove_open_with, self.progid, [self.ending])

    def _forget_ending(self):
        key = windows._ps_literal(f"{windows.CLASSES}\\{self.ending}")
        windows._powershell(f"[Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree({key}, $false)")

    def install(self, pause=True):
        windows.install_open_with(
            ROOT / "pdf-sanitizer", os.environ["WSL_DISTRO_NAME"],
            progid=self.progid, extensions=[self.ending], pause=pause,
        )

    def read(self):
        return windows.read_open_with(self.progid, [self.ending])

    def remove(self):
        return windows.remove_open_with(self.progid, [self.ending])

    def open_with_names(self):
        script = SHELL_HELPER + f"[ShellHelper]::OpenWithNames({windows._ps_literal(self.ending)})\n"
        return windows._powershell(script).splitlines()


@unittest.skipUnless(IN_WSL, "needs WSL with Windows interop")
class OpenWithRegistryTest(unittest.TestCase):
    def setUp(self):
        self.type = FakeFileType(self)

    def test_entry_is_added_read_and_removed(self):
        self.assertNotIn(windows.APP_NAME, self.type.open_with_names())
        self.type.install()

        name, command, endings = self.type.read()
        self.assertEqual(name, windows.APP_NAME)
        self.assertTrue(command.lower().startswith('"c:\\windows\\system32\\wsl.exe" '), command)
        arguments = windows.open_with_arguments(ROOT / "pdf-sanitizer", os.environ["WSL_DISTRO_NAME"])
        self.assertTrue(command.endswith(" " + arguments), command)
        self.assertEqual(endings, [self.type.ending])
        # The name the Open with menu shows, not "Windows Subsystem for Linux".
        self.assertIn(windows.APP_NAME, self.type.open_with_names())

        self.assertTrue(self.type.remove())
        self.assertIsNone(self.type.read())
        self.assertNotIn(windows.APP_NAME, self.type.open_with_names())
        self.assertFalse(self.type.remove())

    def test_remove_keeps_other_apps(self):
        self.type.install()
        other = windows._ps_literal(f"{windows.CLASSES}\\{self.type.ending}\\OpenWithProgids")
        windows._powershell(f"[Microsoft.Win32.Registry]::CurrentUser.CreateSubKey({other}).SetValue('Other.App', '')")
        self.type.remove()
        names = windows._powershell(f"[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey({other}).GetValueNames()")
        self.assertEqual(names.splitlines(), ["Other.App"])


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

    def test_powershell_literal_survives_every_quote_mark(self):
        # PowerShell ends a single-quoted string at any of these marks.
        for text in ("it's", "a‘b", "a’b", "a‚b", "a‛b", "x’; Write-Output PWNED; ’"):
            with self.subTest(text=text):
                self.assertEqual(windows._powershell(f"Write-Output {windows._ps_literal(text)}"), text)

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
class RightClickEndToEndTest(WindowsFolderTest):
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

    def test_open_with_launch_with_hostile_names(self):
        fake = FakeFileType(self)
        fake.install(pause=False)  # no one is there to press Enter

        launches = ""
        for name in HOSTILE_NAMES:
            (self.dir / name).write_bytes(fixtures.risky_pdf())
            file = windows._ps_literal(f"{self.win_dir}\\{name}")
            launches += f"Write-Output ([ShellHelper]::Launch({windows._ps_literal(fake.progid)}, {file}, 300000))\n"
        exit_codes = windows._powershell(SHELL_HELPER + launches, timeout=1600).splitlines()

        self.assertEqual(exit_codes, ["0"] * len(HOSTILE_NAMES))
        for name in HOSTILE_NAMES:
            self.assertTrue((self.dir / f"{Path(name).stem}.safe.pdf").exists(), name)
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
