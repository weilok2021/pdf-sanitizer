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
from pathlib import Path

from pdf_sanitizer import cli, sandbox
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

    def test_names_that_differ_only_in_case_do_not_overwrite_each_other(self):
        # On /mnt/c, Book.safe.pdf and book.safe.pdf are the same file.
        out = self.dir / "out"
        out.mkdir()
        first, second = self.book("one/Book.pdf"), self.book("two/book.pdf")
        code, output = self.run_tool("--force", "-o", str(out), first, second)
        self.assertIn("this run already made", output)
        self.assertIn("1 of 2 sanitized.", output)
        self.assertEqual(os.listdir(out), ["Book.safe.pdf"])


if __name__ == "__main__":
    unittest.main()
