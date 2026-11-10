"""The host's pixel-stream check: a compromised renderer cannot sneak past it."""

import io
import unittest

from pdf_sanitizer import protocol, sandbox
from pdf_sanitizer.text import safe, safe_log


def stream(pages, sizes, pixel_bytes=None, extra=b""):
    out = bytearray(protocol.MAGIC + pages.to_bytes(4, "big"))
    for width, height in sizes:
        out += width.to_bytes(4, "big") + height.to_bytes(4, "big")
        count = width * height * 3
        out += pixel_bytes if pixel_bytes is not None else bytes(range(256)) * (count // 256) + bytes(count % 256)
    return bytes(out) + extra


class RelayTest(unittest.TestCase):
    def relay(self, data):
        dst = io.BytesIO()
        outcome = sandbox.relay(io.BytesIO(data), dst)
        return outcome, dst.getvalue()

    def test_good_stream_is_copied_exactly(self):
        data = stream(2, [(3, 2), (1, 1)])
        outcome, copied = self.relay(data)
        self.assertEqual(outcome.pages, 2)
        self.assertEqual(copied, data)

    def test_white_pages_are_counted(self):
        white = stream(2, [(4, 4), (4, 4)], pixel_bytes=b"\xff" * 48)
        self.assertEqual(self.relay(white)[0].blank_pages, 2)
        one_dot = b"\xff" * 47 + b"\x00"
        mixed = stream(2, [(4, 4), (4, 4)], pixel_bytes=one_dot)
        self.assertEqual(self.relay(mixed)[0].blank_pages, 0)

    def test_progress_is_reported_per_page(self):
        seen = []
        sandbox.relay(io.BytesIO(stream(3, [(1, 1)] * 3)), io.BytesIO(), on_page=lambda n, t: seen.append((n, t)))
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_bad_streams_are_rejected(self):
        cases = {
            "wrong magic": b"EVIL" + stream(1, [(1, 1)])[4:],
            "zero pages": protocol.MAGIC + (0).to_bytes(4, "big"),
            "too many pages": protocol.MAGIC + (protocol.MAX_PAGES + 1).to_bytes(4, "big"),
            "zero width": stream(1, [(0, 5)], pixel_bytes=b""),
            "huge width": protocol.MAGIC + (1).to_bytes(4, "big") + (protocol.MAX_SIDE + 1).to_bytes(4, "big") + (1).to_bytes(4, "big"),
            "huge height": protocol.MAGIC + (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + (2**32 - 1).to_bytes(4, "big"),
            "short pixels": stream(1, [(2, 2)])[:-1],
            "missing page": stream(2, [(1, 1)]),
            "trailing bytes": stream(1, [(1, 1)], extra=b"<script>"),
            "empty": b"",
        }
        for label, data in cases.items():
            with self.subTest(label):
                with self.assertRaises(protocol.StreamError):
                    self.relay(data)


class SafeTextTest(unittest.TestCase):
    def test_terminal_escapes_are_neutralised(self):
        shown = safe("book\x1b[2J\x1b]0;pwned\x07.pdf")
        self.assertNotIn("\x1b", shown)
        self.assertNotIn("\x07", shown)
        self.assertIn("\\x1b", shown)

    def test_direction_controls_are_shown_not_applied(self):
        self.assertEqual(safe("book‮fdp.exe"), "book\\u202efdp.exe")

    def test_normal_unicode_is_kept(self):
        self.assertEqual(safe("三体 - Liu Cixin.epub"), "三体 - Liu Cixin.epub")

    def test_log_is_trimmed_and_safe(self):
        lines = safe_log(b"\x1b[31mred\n" + b"x" * 1000 + b"\n" * 3 + b"line\n" * 50)
        self.assertLessEqual(len(lines), 12)
        self.assertTrue(all("\x1b" not in line and len(line) <= 300 for line in lines))


if __name__ == "__main__":
    unittest.main()
