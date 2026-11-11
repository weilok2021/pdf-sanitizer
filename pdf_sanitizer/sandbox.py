"""Run the two sandbox stages and pass checked pixels between them.

    document ──► [render container] ──pixels──► host checks ──► [rebuild container] ──► PDF

Both containers run with no network, no shared folders, a read-only file
system, no root, no Linux capabilities, and memory and process limits.
The document goes in through stdin. Pixels and the finished PDF come out
through stdout. Nothing else crosses the boundary.
"""

import hashlib
import os
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import protocol
from .text import safe, safe_log

ROOT = Path(__file__).resolve().parent.parent
IMAGE_REPO = "pdf-sanitizer-sandbox"
# The image tag is a hash of these files, so a code change forces a rebuild.
IMAGE_FILES = (
    "container/Dockerfile",
    "container/render.py",
    "container/rebuild.py",
    "pdf_sanitizer/protocol.py",
)
MEMORY = "2g"
PIDS = "128"
# OCR languages the image installs. Keep in step with the Dockerfile.
OCR_LANGUAGES = ("eng",)
# A container killed with SIGKILL exits with 137. Docker's memory limit does that.
KILLED = 137
CHUNK = 1024 * 1024
STDERR_CAP = 64 * 1024


class SanitizeError(Exception):
    """The document could not be sanitized. The message is safe to print."""


def image_tag():
    digest = hashlib.sha256()
    for rel in IMAGE_FILES:
        digest.update(rel.encode() + b"\0" + (ROOT / rel).read_bytes() + b"\0")
    return f"{IMAGE_REPO}:{digest.hexdigest()[:12]}"


def docker_available():
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def image_exists(tag):
    result = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
    return result.returncode == 0


def build_image(tag):
    """Build the sandbox image. This is the only step that uses the network."""
    command = ["docker", "build", "-t", tag, "-f", str(ROOT / "container" / "Dockerfile"), str(ROOT)]
    return subprocess.run(command).returncode == 0


def remove_old_images(current):
    """Delete sandbox images left over from earlier versions of the code."""
    listing = subprocess.run(
        ["docker", "image", "ls", IMAGE_REPO, "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True, text=True,
    )
    old = [ref for ref in listing.stdout.split() if ref.startswith(f"{IMAGE_REPO}:") and ref != current]
    if old:
        subprocess.run(["docker", "image", "rm", *old], capture_output=True)
    return old


def sandbox_command(name, tag, argv):
    """The docker run command for one locked-down stage."""
    return [
        "docker", "run",
        "--rm", "-i",
        "--pull", "never",  # never fetch an image with this name from the internet
        "--name", name,
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "65534:65534",
        "--memory", MEMORY,
        "--memory-swap", MEMORY,
        "--pids-limit", PIDS,
        "--log-driver", "none",  # pixels must not pile up in Docker's logs
        tag,
        *argv,
    ]


class Stage:
    """One running sandbox container."""

    def __init__(self, label, tag, argv):
        # The process id lets tests and `docker ps` tell runs apart.
        self.name = f"pdf-sanitizer-{label}-{os.getpid()}-{secrets.token_hex(6)}"
        self.proc = subprocess.Popen(
            sandbox_command(self.name, tag, argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._stderr = bytearray()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self):
        for chunk in iter(lambda: self.proc.stderr.read1(4096), b""):
            self._stderr += chunk
            if len(self._stderr) > STDERR_CAP:
                del self._stderr[: len(self._stderr) - STDERR_CAP]

    def stderr_lines(self):
        self._stderr_thread.join(timeout=5)
        return safe_log(bytes(self._stderr))

    def kill(self):
        """Stop the container.

        Kill the docker client first. While the client is stuck handing us
        output that we no longer read, Docker cannot remove the container,
        and `docker rm -f` would wait forever.
        """
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            pass

    def wait(self, timeout=None):
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            return self.proc.wait()

    def close(self):
        """Stop the stage if it still runs, and release its pipes."""
        if self.proc.poll() is None:
            self.kill()
        self.proc.wait()
        self._stderr_thread.join(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


class Watchdog:
    """Kill the stages when no data has moved for `timeout` seconds."""

    def __init__(self, timeout, on_expire):
        self.timeout = timeout
        self.on_expire = on_expire
        self.fired = False
        self._last = time.monotonic()
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def touch(self):
        self._last = time.monotonic()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(1):
            if time.monotonic() - self._last > self.timeout:
                self.fired = True
                self.on_expire()
                return


@dataclass
class Outcome:
    pages: int
    blank_pages: int  # pages that are pure white


def relay(src, dst, on_page=None, touch=None):
    """Copy a pixel stream from src to dst, checking every number.

    The host writes a fresh header and fresh page sizes from the checked
    values. It copies only the pixel bytes as they are. It also counts pure
    white pages, so a document the sandbox could not really read is caught.
    """
    pages = protocol.read_header(src)
    dst.write(protocol.pack_header(pages))
    blank_pages = 0
    for number in range(1, pages + 1):
        width, height = protocol.read_page_size(src)
        dst.write(protocol.pack_page_size(width, height))
        left = protocol.page_bytes(width, height)
        blank = True
        while left:
            chunk = src.read(min(CHUNK, left))
            if not chunk:
                raise protocol.StreamError(f"stream ended inside page {number}")
            if blank and chunk.count(b"\xff") != len(chunk):
                blank = False
            dst.write(chunk)
            left -= len(chunk)
            if touch:
                touch()
        blank_pages += blank
        if on_page:
            on_page(number, pages)
    protocol.expect_end(src)
    return Outcome(pages, blank_pages)


def _feed(path, stage, watchdog, failures, stop):
    """Send the document to the render stage.

    If the document cannot be read to the end, stop everything: a cut-off
    document could still render, and the copy would miss pages.
    """
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK), b""):
                try:
                    stage.proc.stdin.write(chunk)
                except (OSError, ValueError):
                    return  # the stage stopped reading; its exit code tells the story
                watchdog.touch()
    except OSError as e:
        failures.append(f"Could not read the document: {safe(e.strerror or str(e))}")
        stop()
    finally:
        try:
            stage.proc.stdin.close()
        except (OSError, ValueError):
            pass


def _sink(stage, out_file, watchdog, failures, stop):
    """Write the finished PDF. If writing fails (a full disk), stop everything."""
    for chunk in iter(lambda: stage.proc.stdout.read1(CHUNK), b""):
        try:
            out_file.write(chunk)
        except OSError as e:
            failures.append(f"Could not write the safe copy: {safe(e.strerror or str(e))}")
            stop()
            return
        watchdog.touch()


def _failed(stage, what, code=None):
    """The error for a stage that failed: its own last words, or a memory stop."""
    if code == KILLED:
        return SanitizeError(f"{what} It was stopped, most likely because it ran out of memory (the limit is {MEMORY.upper()}B).")
    lines = stage.stderr_lines()
    detail = ("\n    " + "\n    ".join(lines)) if lines else ""
    return SanitizeError(f"{what}{detail}")


def _exit_code(stage, seconds=10):
    """Wait a little for a stage to end by itself. None if it is still running."""
    try:
        return stage.proc.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        return None


def check_language(language):
    """Raise SanitizeError unless every OCR language in it (eng+deu) is installed."""
    missing = [code for code in language.split("+") if code not in OCR_LANGUAGES]
    if missing:
        installed = ", ".join(OCR_LANGUAGES)
        raise SanitizeError(f"{safe(missing[0])} is not installed in the sandbox (installed: {installed})")


def render_argv(kind, dpi):
    return ["python", "-I", "/app/render.py", kind, str(dpi)]


def rebuild_argv(dpi, ocr, language):
    return ["python", "-I", "/app/rebuild.py", str(dpi), "ocr" if ocr else "no-ocr", language]


def sanitize(src_path, out_file, kind, dpi=150, ocr=True, language="eng", timeout=300, on_page=None):
    """Turn the document at src_path into a safe PDF written to out_file.

    Return an Outcome. Raise SanitizeError on any failure.
    """
    if ocr:
        check_language(language)
    tag = image_tag()
    render = Stage("render", tag, render_argv(kind, dpi))
    rebuild = Stage("rebuild", tag, rebuild_argv(dpi, ocr, language))
    stages = (render, rebuild)

    def kill_all():
        for stage in stages:
            stage.kill()

    # A read or write failure stops both stages. Its message beats the
    # "malformed data" or "stopped early" that the stopped stages cause.
    failures = []

    def check_failures():
        if failures:
            raise SanitizeError(failures[0])
        if watchdog.fired:
            raise SanitizeError(f"Stopped: nothing happened for {timeout} seconds.")

    watchdog = Watchdog(timeout, kill_all)
    feeder = threading.Thread(target=_feed, args=(src_path, render, watchdog, failures, kill_all), daemon=True)
    sink = threading.Thread(target=_sink, args=(rebuild, out_file, watchdog, failures, kill_all), daemon=True)
    feeder.start()
    sink.start()

    try:
        try:
            outcome = relay(render.proc.stdout, rebuild.proc.stdin, on_page, watchdog.touch)
            rebuild.proc.stdin.close()
        except protocol.StreamError as e:
            check_failures()
            # A render stage that gave up on its own exits with an error code.
            # Read it before killing: a kill would hide it behind code 137.
            render_code = _exit_code(render)
            kill_all()
            if render_code not in (0, None):
                raise _failed(render, "The sandbox could not open the document.", render_code) from None
            raise SanitizeError(
                f"The sandbox sent malformed page data ({e}). The file may be malicious. Delete it."
            ) from None
        except (BrokenPipeError, OSError):
            # The rebuild stage stopped reading, for example because it ran
            # out of memory. Read its exit code before killing, as above.
            check_failures()
            rebuild_code = _exit_code(rebuild)
            kill_all()
            raise _failed(rebuild, "The rebuild step stopped early.", rebuild_code) from None

        render_code = render.wait(timeout=60)
        rebuild_code = rebuild.wait()
        sink.join()
        feeder.join()
        check_failures()
        if render_code != 0:
            raise _failed(render, "The sandbox could not open the document.", render_code)
        if rebuild_code != 0:
            raise _failed(rebuild, "The rebuild step failed.", rebuild_code)
        if outcome.blank_pages == outcome.pages:
            raise SanitizeError(
                "Every page came out blank, so the sandbox could not really read this document."
            )
        return outcome
    finally:
        watchdog.stop()
        for stage in stages:
            if stage.proc.poll() is None:
                stage.kill()
        feeder.join(timeout=5)
        sink.join(timeout=5)
        for stage in stages:
            stage.close()
