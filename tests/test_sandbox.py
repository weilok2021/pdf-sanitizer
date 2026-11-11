"""Integration tests. They need Docker and a built sandbox (`pdf-sanitizer setup`).

They prove three things:
  1. The sandbox walls hold: no network, no root, no host files, limits set.
  2. Risky books come out with every dangerous feature gone and the text kept.
  3. A compromised or stuck renderer is caught and stopped.
"""

import io
import json
import os
import secrets
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from pdf_sanitizer import sandbox
from tests import fixtures

TAG = sandbox.image_tag()
READY = sandbox.docker_available() and sandbox.image_exists(TAG)
SKIP_REASON = "needs Docker and a built sandbox: run ./pdf-sanitizer setup"


def run_in_sandbox(code, stdin=b""):
    name = f"pdf-sanitizer-test-{secrets.token_hex(4)}"
    command = sandbox.sandbox_command(name, TAG, ["python", "-I", "-c", code])
    return subprocess.run(command, input=stdin, capture_output=True, timeout=180)


def probe(code):
    result = run_in_sandbox(code)
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return json.loads(result.stdout)


# Runs inside the sandbox image to inspect a finished PDF.
INSPECT_PDF = r"""
import json, re, sys, pymupdf
doc = pymupdf.open("pdf", sys.stdin.buffer.read())
risky = re.compile(r"/(JavaScript|JS|OpenAction|AA|Launch|EmbeddedFiles?|URI|Annots|AcroForm"
                   r"|Names|RichMedia|XFA|SubmitForm|ImportData|GoToR|GoToE)\b")
found = set()
for xref in range(1, doc.xref_length()):
    found.update(risky.findall(doc.xref_object(xref, compressed=True)))
found.update(risky.findall(doc.pdf_trailer(compressed=True)))
print(json.dumps({
    "pages": doc.page_count,
    "text": " ".join(page.get_text() for page in doc),
    "risky": sorted(found),
    "attachments": doc.embfile_count(),
    "links": sum(len(page.get_links()) for page in doc),
}))
"""


def inspect_pdf(data):
    result = run_in_sandbox(INSPECT_PDF, stdin=data)
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return json.loads(result.stdout)


def container_exists(name):
    result = subprocess.run(
        ["docker", "ps", "-a", "-q", "--filter", f"name=^{name}$"], capture_output=True, text=True
    )
    return bool(result.stdout.strip())


@unittest.skipUnless(READY, SKIP_REASON)
class SandboxWallsTest(unittest.TestCase):
    def test_no_network(self):
        info = probe(
            "import json, socket\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 53), timeout=5); reached = True\n"
            "except OSError:\n"
            "    reached = False\n"
            "print(json.dumps({'reached': reached, 'nics': sorted(n for _, n in socket.if_nameindex())}))"
        )
        self.assertFalse(info["reached"])
        self.assertEqual(info["nics"], ["lo"])

    def test_not_root_and_no_powers(self):
        info = probe(
            "import json, os\n"
            "status = dict(l.split(':\\t', 1) for l in open('/proc/self/status').read().splitlines() if ':\\t' in l)\n"
            "print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(),"
            " 'cap_eff': status['CapEff'], 'cap_bnd': status['CapBnd'], 'no_new_privs': status['NoNewPrivs']}))"
        )
        self.assertEqual((info["uid"], info["gid"]), (65534, 65534))
        self.assertEqual(int(info["cap_eff"], 16), 0)
        self.assertEqual(int(info["cap_bnd"], 16), 0)
        self.assertEqual(info["no_new_privs"], "1")

    def test_file_system_is_read_only_and_tmp_cannot_run_programs(self):
        info = probe(
            "import json, os, subprocess\n"
            "def can_write(path):\n"
            "    try:\n"
            "        open(path, 'w').write('x'); return True\n"
            "    except OSError:\n"
            "        return False\n"
            "open('/tmp/run.sh', 'w').write('#!/bin/sh\\necho ran\\n'); os.chmod('/tmp/run.sh', 0o755)\n"
            "try:\n"
            "    subprocess.run(['/tmp/run.sh']); ran = True\n"
            "except PermissionError:\n"
            "    ran = False\n"
            "print(json.dumps({'app': can_write('/app/x'), 'etc': can_write('/etc/x'),"
            " 'tmp': can_write('/tmp/x'), 'ran_from_tmp': ran}))"
        )
        self.assertEqual(info, {"app": False, "etc": False, "tmp": True, "ran_from_tmp": False})

    def test_host_files_are_not_visible(self):
        info = probe(
            "import json, os\n"
            "mounts = [l.split()[1] for l in open('/proc/mounts')]\n"
            "print(json.dumps({'mnt_c': os.path.exists('/mnt/c'), 'home': os.listdir('/home'),"
            " 'host_mounts': [m for m in mounts if m.startswith(('/mnt', '/home', '/root', '/host', '/run/desktop'))]}))"
        )
        self.assertEqual(info, {"mnt_c": False, "home": [], "host_mounts": []})

    def test_memory_and_process_limits(self):
        info = probe(
            "import json\n"
            "print(json.dumps({'mem': open('/sys/fs/cgroup/memory.max').read().strip(),"
            " 'pids': open('/sys/fs/cgroup/pids.max').read().strip()}))"
        )
        self.assertEqual(info, {"mem": str(2 * 1024**3), "pids": sandbox.PIDS})


@unittest.skipUnless(READY, SKIP_REASON)
class SanitizeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, data):
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def sanitize(self, path, kind, **options):
        out = io.BytesIO()
        outcome = sandbox.sanitize(path, out, kind, timeout=120, **options)
        return outcome.pages, out.getvalue()

    def test_inspector_sees_the_dangers_in_the_original(self):
        # Control: proves the inspector would catch what the next tests look for.
        report = inspect_pdf(fixtures.risky_pdf())
        for key in ("JavaScript", "JS", "OpenAction", "AA", "Launch", "EmbeddedFiles", "URI", "Annots", "AcroForm"):
            self.assertIn(key, report["risky"])
        self.assertEqual(report["attachments"], 1)

    def test_risky_pdf_comes_out_clean_with_text(self):
        path = self.write("risky.pdf", fixtures.risky_pdf())
        pages, data = self.sanitize(path, "pdf")
        report = inspect_pdf(data)
        self.assertEqual(pages, 1)
        self.assertEqual(report["pages"], 1)
        self.assertEqual(report["risky"], [])
        self.assertEqual(report["attachments"], 0)
        self.assertEqual(report["links"], 0)
        self.assertIn(fixtures.PDF_MARKER, report["text"])

    def test_risky_epub_comes_out_clean_with_text(self):
        path = os.path.join(self.dir.name, "risky.epub")
        fixtures.write_epub(path)
        pages, data = self.sanitize(path, "epub")
        report = inspect_pdf(data)
        self.assertGreaterEqual(pages, 1)
        self.assertEqual(report["risky"], [])
        self.assertEqual(report["links"], 0)
        self.assertIn(fixtures.EPUB_MARKER, report["text"])

    def test_without_ocr_there_is_no_text(self):
        path = self.write("risky.pdf", fixtures.risky_pdf())
        _, data = self.sanitize(path, "pdf", ocr=False)
        report = inspect_pdf(data)
        self.assertEqual(report["risky"], [])
        self.assertEqual(report["text"].strip(), "")

    def test_broken_book_fails_with_a_plain_message(self):
        path = self.write("broken.pdf", b"%PDF-1.7\nthis is not really a pdf\n")
        with self.assertRaises(sandbox.SanitizeError) as caught:
            self.sanitize(path, "pdf")
        self.assertIn("could not open", str(caught.exception))


@unittest.skipUnless(READY, SKIP_REASON)
class CompromisedRendererTest(unittest.TestCase):
    """Pretend a bad book took over the renderer, and check the host holds."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.book = os.path.join(self.dir.name, "book.pdf")
        with open(self.book, "wb") as f:
            f.write(fixtures.risky_pdf())

    def run_with_renderer(self, code, timeout=120):
        fake = lambda kind, dpi: ["python", "-I", "-c", code]  # noqa: E731
        with mock.patch.object(sandbox, "render_argv", fake):
            return sandbox.sanitize(self.book, io.BytesIO(), "pdf", timeout=timeout)

    def test_oversized_page_is_rejected(self):
        code = (
            "import sys; w = sys.stdout.buffer\n"
            "w.write(b'BCP1' + (1).to_bytes(4, 'big') + (50000).to_bytes(4, 'big') + (50000).to_bytes(4, 'big'))"
        )
        with self.assertRaises(sandbox.SanitizeError) as caught:
            self.run_with_renderer(code)
        self.assertIn("malformed", str(caught.exception))

    def test_all_blank_output_is_a_failure(self):
        # What a book the reader cannot really parse looks like (AZW3 did this).
        code = (
            "import sys; w = sys.stdout.buffer\n"
            "w.write(b'BCP1' + (1).to_bytes(4, 'big') + (20).to_bytes(4, 'big') + (10).to_bytes(4, 'big'))\n"
            "w.write(b'\\xff' * 600)"
        )
        with self.assertRaises(sandbox.SanitizeError) as caught:
            self.run_with_renderer(code)
        self.assertIn("blank", str(caught.exception))

    def test_terminal_escapes_in_errors_are_neutralised(self):
        code = "import sys; sys.stderr.write('\\x1b[2J\\x1b]0;owned\\x07 fake message\\n'); sys.exit(2)"
        with self.assertRaises(sandbox.SanitizeError) as caught:
            self.run_with_renderer(code)
        message = str(caught.exception)
        self.assertIn("fake message", message)
        self.assertNotIn("\x1b", message)
        self.assertNotIn("\x07", message)

    def test_stuck_renderer_is_stopped(self):
        names = []
        real_stage = sandbox.Stage

        def recording_stage(*args, **kwargs):
            stage = real_stage(*args, **kwargs)
            names.append(stage.name)
            return stage

        with mock.patch.object(sandbox, "Stage", recording_stage):
            with self.assertRaises(sandbox.SanitizeError) as caught:
                self.run_with_renderer("import time; time.sleep(600)", timeout=10)
        self.assertIn("nothing happened", str(caught.exception))
        for name in names:
            self.assertFalse(container_exists(name), f"{name} is still running")

    def test_out_of_memory_says_so(self):
        code = "x = bytearray(3 * 1024 ** 3)"  # more than the 2 GB limit
        with self.assertRaises(sandbox.SanitizeError) as caught:
            self.run_with_renderer(code)
        self.assertIn("ran out of memory", str(caught.exception))


class DiskFull(io.BytesIO):
    """An output file on a disk with room for only 1,000 bytes."""

    def write(self, data):
        if self.tell() + len(data) > 1000:
            raise OSError(28, "No space left on device")
        return super().write(data)


@unittest.skipUnless(READY, SKIP_REASON)
class HostFileErrorTest(unittest.TestCase):
    """Errors on the host side are reported as they are, and quickly."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def sanitize(self, path, out):
        started = time.monotonic()
        with self.assertRaises(sandbox.SanitizeError) as caught:
            sandbox.sanitize(path, out, "pdf", timeout=120)
        self.assertLess(time.monotonic() - started, 60, "it waited for the timeout")
        return str(caught.exception)

    def test_full_disk_is_reported_for_short_and_long_documents(self):
        # A short document once came back as a "success" with a cut-off file.
        for name, data in (("short", fixtures.risky_pdf()), ("long", fixtures.long_pdf(20))):
            with self.subTest(name):
                path = os.path.join(self.dir.name, f"{name}.pdf")
                with open(path, "wb") as f:
                    f.write(data)
                message = self.sanitize(path, DiskFull())
                self.assertIn("Could not write the safe copy: No space left on device", message)

    def test_unreadable_document_is_reported(self):
        # A folder cannot be read as a file, like a document Windows has locked.
        message = self.sanitize(self.dir.name, io.BytesIO())
        self.assertIn("Could not read the document", message)


if __name__ == "__main__":
    unittest.main()
