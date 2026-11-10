"""The pdf-sanitizer command.

    pdf-sanitizer setup            build the sandbox (needs internet once)
    pdf-sanitizer check FILE...    say what each file really is; opens nothing
    pdf-sanitizer FILE...          make a safe PDF copy of each document
"""

import argparse
import os
import secrets
import sys
import time
from pathlib import Path

from . import filetype, sandbox
from .text import safe

MAX_INPUT = 1024 * 1024 * 1024  # 1 GiB, the same limit as the sandbox

USAGE = """\
usage:
  pdf-sanitizer setup              build the sandbox (needs internet once)
  pdf-sanitizer check FILE...      say what each file really is; opens nothing
  pdf-sanitizer [options] FILE...  make a safe PDF copy of each document

options:
  -o, --out-dir DIR   where to save the safe copies (default: next to each document)
  --dpi N             picture sharpness, 72 to 300 (default: 150)
  --no-ocr            skip OCR; faster, but the text is not searchable
  --lang CODE         OCR language (default: eng)
  --timeout SECONDS   stop when nothing happens for this long (default: 300)
  --force             replace a safe copy that already exists
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
    args = parser.parse_args(argv)
    if not 72 <= args.dpi <= 300:
        parser.error("--dpi must be between 72 and 300")
    if args.timeout < 10:
        parser.error("--timeout must be at least 10 seconds")
    return args


def _say(line=""):
    print(line, flush=True)


def _report(path, verdict):
    _say(f"{safe(path)}: {verdict.what}")
    for warning in verdict.warnings:
        _say(f"  warning: {warning}")
    _say(f"  {verdict.reason}")


def cmd_check(paths):
    all_ok = True
    for path in paths:
        verdict = filetype.inspect(path)
        _report(path, verdict)
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


def _progress(number, total):
    if sys.stderr.isatty():
        end = "\n" if number == total else ""
        print(f"\r  page {number}/{total}", end=end, file=sys.stderr, flush=True)


def _sanitize_one(path, args, made):
    """Sanitize one document. `made` holds the safe copies this run has written."""
    verdict = filetype.inspect(path)
    _report(path, verdict)
    if not verdict.ok:
        return False
    if os.path.getsize(path) > MAX_INPUT:
        _say("  Skipped: the file is larger than 1 GiB.")
        return False

    src = Path(path)
    out_dir = Path(args.out_dir) if args.out_dir else src.parent
    final = out_dir / f"{src.stem}.safe.pdf"
    if final in made:
        _say(f"  Skipped: this run already made {safe(str(final))} from another file with the same name.")
        return False
    if final.exists() and not args.force:
        _say(f"  Skipped: {safe(str(final))} already exists. Use --force to replace it.")
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
    _say(f"  Safe copy: {safe(str(final))} ({outcome.pages} pages, {size_mb:.1f} MB, {seconds:.0f} s)")
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
    for path in args.files:
        results.append(_sanitize_one(path, args, made))
        _say()
    done = sum(results)
    _say(f"{done} of {len(results)} sanitized.")
    return 0 if all(results) else 1


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
    return cmd_sanitize(argv)
