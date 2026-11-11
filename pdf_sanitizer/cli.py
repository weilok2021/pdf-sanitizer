"""The pdf-sanitizer command.

    pdf-sanitizer setup            build the sandbox (needs internet once)
    pdf-sanitizer check FILE...    say what each file really is; opens nothing
    pdf-sanitizer FILE...          make a safe PDF copy of each document
    pdf-sanitizer install-menu     add the right-click entries to Windows:
                                   "Open with > pdf-sanitizer" and
                                   "Send to > Sanitize document"
    pdf-sanitizer remove-menu      remove them again
"""

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

from . import filetype, sandbox, windows
from .text import safe

MAX_INPUT = 1024 * 1024 * 1024  # 1 GiB, the same limit as the sandbox

USAGE = """\
usage:
  pdf-sanitizer setup              build the sandbox (needs internet once)
  pdf-sanitizer check FILE...      say what each file really is; opens nothing
  pdf-sanitizer [options] FILE...  make a safe PDF copy of each document
  pdf-sanitizer install-menu       add "Open with > pdf-sanitizer" and
                                   "Send to > Sanitize document" to the
                                   Windows right-click menu
  pdf-sanitizer remove-menu        remove them again

options:
  -o, --out-dir DIR   where to save the safe copies (default: next to each document)
  --dpi N             picture sharpness, 72 to 300 (default: 150)
  --no-ocr            skip OCR; faster, but the text is not searchable
  --lang CODE         OCR language (default: eng)
  --timeout SECONDS   stop when nothing happens for this long (default: 300)
  --force             replace a safe copy that already exists

The menu entries add --from-windows (the files are Windows paths) and
--pause (keep the window open until you press Enter).
"""


def _parse_sanitize(argv):
    parser = argparse.ArgumentParser(prog="pdf-sanitizer", add_help=False, usage=USAGE)
    parser.add_argument("files", nargs="+")
    parser.add_argument("-o", "--out-dir")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--lang", default="eng")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--from-windows", action="store_true")
    parser.add_argument("--pause", action="store_true")
    args = parser.parse_args(argv)
    if not 72 <= args.dpi <= 300:
        parser.error("--dpi must be between 72 and 300")
    if args.timeout < 10:
        parser.error("--timeout must be at least 10 seconds")
    return args


def _say(line=""):
    print(line, flush=True)


def _report(shown, verdict):
    """shown: the file's name, already made safe to print."""
    _say(f"{shown}: {verdict.what}")
    for warning in verdict.warnings:
        _say(f"  warning: {warning}")
    _say(f"  {verdict.reason}")


def cmd_check(paths):
    all_ok = True
    for path in paths:
        verdict = filetype.inspect(path)
        _report(safe(path), verdict)
        all_ok = all_ok and verdict.ok
    return 0 if all_ok else 1


def cmd_setup():
    if not sandbox.docker_available():
        _say("Docker is not running. Start Docker Desktop, then try again.")
        return 1
    tag = sandbox.image_tag()
    _say(f"Building the sandbox image {tag} ...")
    if not sandbox.build_image(tag):
        _say("The build failed. See the Docker output above.")
        return 1
    old = sandbox.remove_old_images(tag)
    if old:
        _say(f"Removed {len(old)} old sandbox image(s).")
    _say("Done. You can now sanitize documents.")
    return 0


def _shown(path, args):
    """The path as the user knows it: Windows style when they came from Windows."""
    if args.from_windows:
        return safe(windows.to_windows_path(path))
    return safe(str(path))


def _progress(number, total):
    if sys.stderr.isatty():
        end = "\n" if number == total else ""
        print(f"\r  page {number}/{total}", end=end, file=sys.stderr, flush=True)


def _sanitize_one(path, args, made):
    """Sanitize one document. `made` holds the safe copies this run has written."""
    verdict = filetype.inspect(path)
    _report(_shown(path, args), verdict)
    if not verdict.ok:
        return False
    if os.path.getsize(path) > MAX_INPUT:
        _say("  Skipped: the file is larger than 1 GiB.")
        return False

    src = Path(path)
    out_dir = Path(args.out_dir) if args.out_dir else src.parent
    final = out_dir / f"{src.stem}.safe.pdf"
    if final in made:
        _say(f"  Skipped: this run already made {_shown(final, args)} from another file with the same name.")
        return False
    if final.exists() and not args.force:
        _say(f"  Skipped: {_shown(final, args)} already exists. Use --force to replace it.")
        return False

    part = out_dir / f".{src.stem}.{secrets.token_hex(4)}.part"
    started = time.monotonic()
    try:
        with open(part, "xb") as out_file:
            outcome = sandbox.sanitize(
                str(src), out_file, verdict.kind,
                dpi=args.dpi, ocr=not args.no_ocr, language=args.lang,
                timeout=args.timeout, on_page=_progress,
            )
        with open(part, "rb") as f:
            if f.read(5) != b"%PDF-":
                raise sandbox.SanitizeError("The rebuild step did not produce a PDF.")
        os.replace(part, final)
    except sandbox.SanitizeError as e:
        _say(f"  Failed: {e}")
        return False
    finally:
        if part.exists():
            part.unlink()

    made.add(final)
    size_mb = final.stat().st_size / (1024 * 1024)
    seconds = time.monotonic() - started
    _say(f"  Safe copy: {_shown(final, args)} ({outcome.pages} pages, {size_mb:.1f} MB, {seconds:.0f} s)")
    return True


def cmd_sanitize(argv):
    args = _parse_sanitize(argv)
    if args.out_dir and not Path(args.out_dir).is_dir():
        _say(f"The folder {safe(args.out_dir)} does not exist.")
        return 1
    if not sandbox.docker_available():
        _say("Docker is not running. Start Docker Desktop, then try again.")
        return 1
    if not sandbox.image_exists(sandbox.image_tag()):
        _say("The sandbox is not built yet, or the code changed since it was built.")
        _say("Run: pdf-sanitizer setup")
        return 1

    results = []
    made = set()
    try:
        for path in args.files:
            if args.from_windows:
                try:
                    path = windows.to_wsl_path(path)
                except windows.IntegrationError as e:
                    _say(f"{safe(path)}: {e}")
                    results.append(False)
                    continue
            results.append(_sanitize_one(path, args, made))
            _say()
    except KeyboardInterrupt:
        # The sandbox and the half-written file are cleaned up on the way out.
        _say("\nStopped. The file in progress has no safe copy.")
        return 130
    done = sum(results)
    _say(f"{done} of {len(results)} sanitized.")
    return 0 if all(results) else 1


def cmd_install_menu():
    if not windows.in_wsl():
        _say("The right-click menu needs Windows with WSL, and this is not WSL.")
        return 1
    entry = Path(__file__).resolve().parent.parent / "pdf-sanitizer"
    distro = os.environ["WSL_DISTRO_NAME"]
    try:
        windows.install_open_with(entry, distro)
        windows.install_shortcut(entry, distro)
    except windows.IntegrationError as e:
        _say(f"Could not add the menu entries: {e}")
        return 1
    endings = ", ".join(windows.EXTENSIONS)
    _say("Added. In File Explorer, right-click a document, then choose:")
    _say(f"  Open with > {windows.APP_NAME}")
    _say(f"    for one file ({endings})")
    _say("  Send to > Sanitize document")
    _say("    for many files at once; on Windows 11 it is under 'Show more options'")
    _say("Your default app for opening PDFs does not change.")
    _say("If no other app opens an ebook ending, double-clicking such a file now makes a safe copy.")
    _say("Docker Desktop must be running when you use them.")
    return 0


def cmd_remove_menu():
    if not windows.in_wsl():
        _say("The right-click menu needs Windows with WSL, and this is not WSL.")
        return 1
    try:
        had_open_with = windows.remove_open_with()
        path = windows.remove_shortcut()
    except windows.IntegrationError as e:
        _say(f"Could not remove the menu entries: {e}")
        return 1
    if had_open_with:
        _say(f"Removed Open with > {windows.APP_NAME}")
    if path:
        _say("Removed Send to > Sanitize document")
    if not (had_open_with or path):
        _say("The menu entries were not there.")
    return 0


def _wait_for_enter():
    try:
        input("\nPress Enter to close this window.")
    except (EOFError, KeyboardInterrupt):
        pass


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE, end="")
        return 0 if argv else 2
    if argv[0] == "setup":
        return cmd_setup()
    if argv[0] == "check":
        if len(argv) < 2:
            print(USAGE, end="")
            return 2
        return cmd_check(argv[1:])
    if argv[0] == "install-menu":
        return cmd_install_menu()
    if argv[0] == "remove-menu":
        return cmd_remove_menu()
    if "--pause" not in argv:
        return cmd_sanitize(argv)
    # Opened from the Windows menu: keep the window open, even after a crash,
    # so the user can read what happened.
    try:
        return cmd_sanitize(argv)
    except Exception:
        import traceback

        traceback.print_exc()
        return 1
    finally:
        _wait_for_enter()
