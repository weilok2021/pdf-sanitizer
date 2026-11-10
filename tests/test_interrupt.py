"""Ctrl+C in the middle of a run stops cleanly.

Regression test: Ctrl+C once left the tool hung, a render container running,
and a half-written .part file. `docker rm -f` waited forever, because the
docker client was stuck handing over pixels that nobody read any more.
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from pdf_sanitizer import sandbox
from tests import fixtures

ROOT = Path(__file__).resolve().parent.parent
READY = sandbox.docker_available() and sandbox.image_exists(sandbox.image_tag())


def sandbox_containers():
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=pdf-sanitizer-", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    return result.stdout.split()


@unittest.skipUnless(READY, "needs Docker and a built sandbox: run ./pdf-sanitizer setup")
class InterruptTest(unittest.TestCase):
    def test_ctrl_c_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as work, tempfile.TemporaryDirectory() as out_dir:
            book = os.path.join(work, "long.pdf")
            with open(book, "wb") as f:
                f.write(fixtures.long_pdf(80))

            # A terminal sends Ctrl+C to the whole process group, docker clients included.
            proc = subprocess.Popen(
                [sys.executable, "-I", str(ROOT / "pdf-sanitizer"), "-o", out_dir, book],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
            )
            deadline = time.monotonic() + 60
            while len(sandbox_containers()) < 2 and time.monotonic() < deadline:
                time.sleep(0.5)
            self.assertEqual(len(sandbox_containers()), 2, "the sandboxes never started")
            time.sleep(3)  # let pixels pile up in the pipes, as in the real bug

            os.killpg(proc.pid, signal.SIGINT)
            try:
                output, _ = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                self.fail("the tool was still running 60 seconds after Ctrl+C")

            self.assertEqual(proc.returncode, 130)
            self.assertIn(b"Stopped.", output)
            self.assertNotIn(b"Traceback", output)
            self.assertEqual(os.listdir(out_dir), [])
            time.sleep(2)
            self.assertEqual(sandbox_containers(), [])


if __name__ == "__main__":
    unittest.main()
