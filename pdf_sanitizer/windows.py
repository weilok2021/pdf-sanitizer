"""Windows integration: two right-click entries in File Explorer.

    Open with > pdf-sanitizer       one document; on the Windows 11 menu itself
    Send to > Sanitize document     many files at once; under "Show more options"

Works from WSL. Both entries run wsl.exe directly, which runs
pdf-sanitizer with the chosen file paths.

- Open with: a per-user file type entry (a "ProgID") in the registry,
  listed under each document ending's OpenWithProgids key. Only HKCU is
  touched, so no admin rights are needed. The default app for PDFs stays
  as it was.
- Send to: a Windows shortcut in the user's Send to folder.

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

PROGID = "PdfSanitizer.Document"
APP_NAME = "pdf-sanitizer"  # the name the Open with menu shows
EXTENSIONS = (".pdf", ".epub", ".mobi", ".fb2", ".cbz")
CLASSES = "Software\\Classes"
TOASTS = "Software\\Microsoft\\Windows\\CurrentVersion\\ApplicationAssociationToasts"


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


# PowerShell reads all of these as a single quote mark.
PS_QUOTES = ("'", "\u2018", "\u2019", "\u201a", "\u201b")


def _ps_literal(text):
    """A PowerShell single-quoted string. Only quote marks need escaping: doubled."""
    for quote in PS_QUOTES:
        text = text.replace(quote, quote * 2)
    return "'" + text + "'"


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


def open_with_arguments(entry_script, distro, pause=True):
    """The arguments of the Open with command, after the path of wsl.exe.

    File Explorer puts the chosen file's path in place of "%1". The quotes
    around it keep it one argument: a Windows path cannot contain a double
    quote. Explorer also reads other %-codes in the command, so the tool's
    own path may not contain a percent sign.
    """
    arguments = shortcut_arguments(entry_script, distro)
    if "%" in arguments:
        raise IntegrationError("the tool's path contains a percent sign; cannot build the Open with entry")
    if not pause:
        arguments = arguments.removesuffix(" --pause")
    return f'{arguments} "%1"'


def _ps_array(items):
    return "@(" + ", ".join(_ps_literal(item) for item in items) + ")"


def install_open_with(entry_script, distro, progid=PROGID, extensions=EXTENSIONS, pause=True):
    """Add "Open with > pdf-sanitizer" for each document ending.

    progid, extensions: tests pass a made-up type and ending instead.
    """
    arguments = open_with_arguments(entry_script, distro, pause)
    verb = f"{CLASSES}\\{progid}\\shell\\open"
    command = f"{verb}\\command"
    script = (
        "$hkcu = [Microsoft.Win32.Registry]::CurrentUser\n"
        "$wsl = Join-Path $env:SystemRoot 'System32\\wsl.exe'\n"
        f"$k = $hkcu.CreateSubKey({_ps_literal(verb)})\n"
        f"$k.SetValue('FriendlyAppName', {_ps_literal(APP_NAME)})\n"
        # One window per file is fine for one file; Send to handles many.
        "$k.SetValue('MultiSelectModel', 'Single')\n"
        "$k.Close()\n"
        f"$k = $hkcu.CreateSubKey({_ps_literal(command)})\n"
        f"$k.SetValue('', '\"' + $wsl + '\" ' + {_ps_literal(arguments)})\n"
        "$k.Close()\n"
        f"$toasts = $hkcu.CreateSubKey({_ps_literal(TOASTS)})\n"
        f"foreach ($ext in {_ps_array(extensions)}) {{\n"
        f"  $k = $hkcu.CreateSubKey({_ps_literal(CLASSES)} + '\\' + $ext + '\\OpenWithProgids')\n"
        f"  $k.SetValue({_ps_literal(progid)}, '', [Microsoft.Win32.RegistryValueKind]::String)\n"
        "  $k.Close()\n"
        # Windows lists the apps it has already announced for a file type
        # here. Being on the list keeps it from asking "How do you want to
        # open this file?", where a slip would make this the default PDF app.
        f"  $toasts.SetValue({_ps_literal(progid + '_')} + $ext, 0, [Microsoft.Win32.RegistryValueKind]::DWord)\n"
        "}\n"
        "$toasts.Close()\n"
    )
    _powershell(script)


def remove_open_with(progid=PROGID, extensions=EXTENSIONS):
    """Take the Open with entry away. Return True if it was there.

    Only this tool's own keys and values are deleted. Other apps listed
    under the same document endings stay.
    """
    key = f"{CLASSES}\\{progid}"
    script = (
        "$hkcu = [Microsoft.Win32.Registry]::CurrentUser\n"
        f"$progid = {_ps_literal(key)}\n"
        "$k = $hkcu.OpenSubKey($progid)\n"
        "$found = $null -ne $k\n"
        "if ($found) { $k.Close(); $hkcu.DeleteSubKeyTree($progid) }\n"
        f"$toasts = $hkcu.OpenSubKey({_ps_literal(TOASTS)}, $true)\n"
        f"foreach ($ext in {_ps_array(extensions)}) {{\n"
        f"  $k = $hkcu.OpenSubKey({_ps_literal(CLASSES)} + '\\' + $ext + '\\OpenWithProgids', $true)\n"
        f"  if ($k) {{ $k.DeleteValue({_ps_literal(progid)}, $false); $k.Close() }}\n"
        f"  if ($toasts) {{ $toasts.DeleteValue({_ps_literal(progid + '_')} + $ext, $false) }}\n"
        "}\n"
        "if ($toasts) { $toasts.Close() }\n"
        "Write-Output $found\n"
    )
    return _powershell(script) == "True"


def read_open_with(progid=PROGID, extensions=EXTENSIONS):
    """Return (name, command, endings that list it), or None if it is not there."""
    verb = f"{CLASSES}\\{progid}\\shell\\open"
    script = (
        "$hkcu = [Microsoft.Win32.Registry]::CurrentUser\n"
        f"$k = $hkcu.OpenSubKey({_ps_literal(verb)})\n"
        "if ($k) {\n"
        "  Write-Output $k.GetValue('FriendlyAppName')\n"
        "  Write-Output $k.OpenSubKey('command').GetValue('')\n"
        f"  foreach ($ext in {_ps_array(extensions)}) {{\n"
        f"    $o = $hkcu.OpenSubKey({_ps_literal(CLASSES)} + '\\' + $ext + '\\OpenWithProgids')\n"
        f"    if ($o -and ($o.GetValueNames() -contains {_ps_literal(progid)})) {{ Write-Output $ext }}\n"
        "  }\n"
        "}\n"
    )
    lines = _powershell(script).splitlines()
    return (lines[0], lines[1], lines[2:]) if len(lines) >= 2 else None
