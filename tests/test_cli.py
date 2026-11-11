"""The command line: option checks, batch behaviour and messages.

The batch tests need Docker and a built sandbox.
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
import secrets
import zipfile
from pathlib import Path
from unittest import mock

from pdf_sanitizer import cli, sandbox, windows
from tests import fixtures

ROOT = Path(__file__).resolve().parent.parent
READY = sandbox.docker_available() and sandbox.image_exists(sandbox.image_tag())


class OptionTest(unittest.TestCase):
    def parse_error(self, argv):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            cli._parse_sanitize(argv)
        self.assertEqual(caught.exception.code, 2)
        return stderr.getvalue()

    def test_language_that_is_not_installed_is_refused_at_the_start(self):
        # It used to fail inside the sandbox with a Python traceback.
        self.assertIn("deu is not installed", self.parse_error(["--lang", "deu", "book.pdf"]))
        self.assertIn("deu is not installed", self.parse_error(["--lang", "eng+deu", "book.pdf"]))

    def test_installed_language_is_accepted(self):
        self.assertEqual(cli._parse_sanitize(["--lang", "eng", "book.pdf"]).lang, "eng")


class ReportTest(unittest.TestCase):
    def report(self, path):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli._report("shown", cli.filetype.inspect(path))
        return stdout.getvalue()

    def test_names_inside_a_zip_cannot_redraw_the_terminal(self):
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "evil.zip")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("readme\x1b[2K\x1b[1A\x1b[32mSafe copy: ok.txt", "x")
            output = self.report(path)
        self.assertIn("readme", output)
        self.assertNotIn("\x1b", output)

    def test_direction_controls_in_warnings_are_shown_escaped(self):
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "book.pdf.\u202efdp")  # warns: "Double ending"
            with open(path, "wb") as f:
                f.write(b"MZ" + b"\0" * 200)
            output = self.report(path)
        self.assertNotIn("‮", output)
        self.assertIn("\\u202e", output)


class OtherErrorTest(unittest.TestCase):
    """An OSError that is not about the safe copy is not called a write error."""

    def test_other_os_error_keeps_its_own_words(self):
        with tempfile.TemporaryDirectory() as work:
            path = os.path.join(work, "book.pdf")
            with open(path, "wb") as f:
                f.write(fixtures.risky_pdf())
            args = cli._parse_sanitize([path])
            stdout = io.StringIO()
            error = OSError(24, "Too many open files")
            with mock.patch.object(cli.sandbox, "sanitize", side_effect=error), contextlib.redirect_stdout(stdout):
                self.assertFalse(cli._sanitize_one(path, args, set()))
            self.assertEqual(os.listdir(work), ["book.pdf"])  # no .part file left
        self.assertIn("Failed: Too many open files", stdout.getvalue())
        self.assertNotIn("could not write", stdout.getvalue())


class FakeTerminal(io.StringIO):
    def isatty(self):
        return True


class ProgressTest(unittest.TestCase):
    def test_unfinished_line_is_ended_before_the_next_message(self):
        terminal = FakeTerminal()
        progress = cli._Progress(terminal)
        progress.page(3, 80)
        progress.end()
        self.assertEqual(terminal.getvalue(), "\r  page 3/80\n")

    def test_finished_line_is_not_ended_twice(self):
        terminal = FakeTerminal()
        progress = cli._Progress(terminal)
        progress.page(2, 2)
        progress.end()
        self.assertEqual(terminal.getvalue(), "\r  page 2/2\n")


@unittest.skipUnless(READY, "needs Docker and a built sandbox: run ./pdf-sanitizer setup")
class BatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.dir)])

    def book(self, relative):
        path = self.dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fixtures.risky_pdf())
        return str(path)

    def run_tool(self, *args):
        result = subprocess.run(
            [sys.executable, "-I", str(ROOT / "pdf-sanitizer"), *args],
            capture_output=True, text=True, timeout=300,
        )
        return result.returncode, result.stdout + result.stderr

    def test_folder_you_cannot_write_to_does_not_stop_the_batch(self):
        locked = self.dir / "locked"
        locked.mkdir()
        locked.chmod(0o555)
        self.addCleanup(locked.chmod, 0o755)
        code, output = self.run_tool("-o", str(locked), self.book("a.pdf"), self.book("b.pdf"))
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", output)
        self.assertEqual(output.count("Failed: could not write the safe copy"), 2)
        self.assertIn("0 of 2 sanitized.", output)

    def test_long_name_is_sanitized(self):
        # 246 characters plus ".pdf" is a valid name. The temporary file once
        # added the name again and went over the 255 limit.
        stem = "x" * 246
        code, output = self.run_tool(self.book(f"{stem}.pdf"))
        self.assertEqual(code, 0, output)
        self.assertTrue((self.dir / f"{stem}.safe.pdf").exists())

    def test_names_that_differ_only_in_case_both_get_a_copy_on_linux(self):
        # A Linux folder keeps Book.safe.pdf and book.safe.pdf apart.
        out = self.dir / "out"
        out.mkdir()
        code, output = self.run_tool("-o", str(out), self.book("one/Book.pdf"), self.book("two/book.pdf"))
        self.assertIn("2 of 2 sanitized.", output)
        self.assertEqual(sorted(os.listdir(out)), ["Book.safe.pdf", "book.safe.pdf"])


@unittest.skipUnless(READY and windows.in_wsl(), "needs WSL, Docker and a built sandbox")
class WindowsFolderBatchTest(unittest.TestCase):
    def test_names_that_differ_only_in_case_do_not_overwrite_each_other(self):
        # In a Windows folder, Book.safe.pdf and book.safe.pdf are one file.
        local = windows._powershell("[Environment]::GetFolderPath('LocalApplicationData')")
        out = Path(windows.to_wsl_path(f"{local}\\Temp\\pdf-sanitizer-test-{secrets.token_hex(4)}"))
        out.mkdir(parents=True)
        self.addCleanup(subprocess.run, ["rm", "-rf", str(out)])
        books = []
        for relative in ("one/Book.pdf", "two/book.pdf"):
            path = out / relative
            path.parent.mkdir()
            path.write_bytes(fixtures.risky_pdf())
            books.append(str(path))
        result = subprocess.run(
            [sys.executable, "-I", str(ROOT / "pdf-sanitizer"), "--force", "-o", str(out), *books],
            capture_output=True, text=True, timeout=300,
        )
        self.assertIn("this run already made", result.stdout)
        self.assertIn("1 of 2 sanitized.", result.stdout)
        self.assertEqual(sorted(p.name for p in out.glob("*.pdf")), ["Book.safe.pdf"])


if __name__ == "__main__":
    unittest.main()
