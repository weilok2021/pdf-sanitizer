"""Windows integration: the right-click "Send to > Sanitize document" entry.

Works from WSL. The entry is a Windows shortcut in the user's Send to
folder. It runs wsl.exe directly, which runs pdf-sanitizer, and File
Explorer adds the chosen files' paths to the end of its command.

File names come from the internet, so an attacker controls them. They
never pass through cmd.exe, a .bat file or a shell, where a character
like "&" could start a second command. wsl.exe hands each path to
pdf-sanitizer as one plain argument.
"""

import base64
import os
import re
import subprocess
from pathlib import PurePosixPath

SHORTCUT_NAME = "Sanitize document.lnk"
DESCRIPTION = "Make a safe PDF copy with pdf-sanitizer"


class IntegrationError(Exception):
    """A Windows step failed. The message is safe to print."""


def in_wsl():
    return bool(os.environ.get("WSL_DISTRO_NAME")) and os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")


def to_wsl_path(path):
    """Turn a Windows path such as C:\\Users\\me\\a.pdf into /mnt/c/Users/me/a.pdf."""
    if path.startswith("/"):
        return path
    result = subprocess.run(["wslpath", "-u", path], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise IntegrationError("cannot turn this Windows path into a WSL path")
    return result.stdout.rstrip("\n")


def to_windows_path(path):
    """Turn a WSL path back into a Windows path, for messages."""
    result = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        return str(path)
    return result.stdout.rstrip("\n")


def _ps_literal(text):
    """A PowerShell single-quoted string. Only the quote itself needs escaping."""
    return "'" + text.replace("'", "''") + "'"


def _powershell(script, timeout=60):
    """Run a PowerShell script. -EncodedCommand avoids every quoting layer."""
    script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise IntegrationError(f"cannot run PowerShell: {e}") from None
    out = result.stdout.decode("utf-8", errors="replace").replace("\r", "").strip()
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace").replace("\r", "").strip()
        raise IntegrationError(f"PowerShell failed: {err[:300]}")
    return out


def shortcut_arguments(entry_script, distro):
    """The arguments the shortcut gives wsl.exe. Explorer adds the file paths after them.

    The distro name stays unquoted: wsl.exe keeps quote marks as part of a
    -d value, so -d "Ubuntu" looks for a distro called "Ubuntu" with quotes.
    """
    entry = str(PurePosixPath(entry_script))
    if not re.fullmatch(r"[A-Za-z0-9._-]+", distro):
        raise IntegrationError("the WSL distro name has unusual characters; cannot build the shortcut")
    if '"' in entry:
        raise IntegrationError("the tool's path contains a double quote; cannot build the shortcut")
    return f'--cd ~ -d {distro} -e "{entry}" --from-windows --pause'


def _folder_expression(folder):
    if folder is None:
        return "[Environment]::GetFolderPath('SendTo')"
    return _ps_literal(folder)


def install_shortcut(entry_script, distro, folder=None):
    """Create the Send to shortcut. Return its Windows path.

    folder: a Windows folder for the shortcut. The default is the user's
    Send to folder. Tests pass a temporary folder instead.
    """
    arguments = shortcut_arguments(entry_script, distro)
    script = (
        f"$folder = {_folder_expression(folder)}\n"
        f"$path = Join-Path $folder {_ps_literal(SHORTCUT_NAME)}\n"
        "$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($path)\n"
        "$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\\wsl.exe'\n"
        f"$shortcut.Arguments = {_ps_literal(arguments)}\n"
        f"$shortcut.Description = {_ps_literal(DESCRIPTION)}\n"
        "$shortcut.WindowStyle = 1\n"
        "$shortcut.Save()\n"
        "Write-Output $path\n"
    )
    return _powershell(script)


def remove_shortcut(folder=None):
    """Delete the Send to shortcut. Return its Windows path, or "" if it was not there."""
    script = (
        f"$path = Join-Path ({_folder_expression(folder)}) {_ps_literal(SHORTCUT_NAME)}\n"
        "if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path; Write-Output $path }\n"
    )
    return _powershell(script)


def read_shortcut(folder=None):
    """Return (target, arguments) of the shortcut, or None if it is not there."""
    script = (
        f"$path = Join-Path ({_folder_expression(folder)}) {_ps_literal(SHORTCUT_NAME)}\n"
        "if (Test-Path -LiteralPath $path) {\n"
        "  $s = (New-Object -ComObject WScript.Shell).CreateShortcut($path)\n"
        "  Write-Output $s.TargetPath; Write-Output $s.Arguments\n"
        "}\n"
    )
    lines = _powershell(script).splitlines()
    return (lines[0], lines[1]) if len(lines) >= 2 else None
