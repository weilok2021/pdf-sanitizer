"""The file-type check: real books pass, disguised programs do not."""

import os
import tempfile
import unittest
import zipfile

from ebook_sanitizer import filetype
from tests import fixtures


class FileTypeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def make(self, name, data):
        path = os.path.join(self.dir.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def make_zip(self, name, entries):
        path = os.path.join(self.dir.name, name)
        with zipfile.ZipFile(path, "w") as zf:
            for entry, data in entries.items():
                zf.writestr(entry, data)
        return path

    # Real books pass.

    def test_pdf_passes(self):
        verdict = filetype.inspect(self.make("book.pdf", fixtures.risky_pdf()))
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.kind, "pdf")
        self.assertEqual(verdict.warnings, [])

    def test_epub_passes_and_notes_scripts(self):
        path = os.path.join(self.dir.name, "book.epub")
        fixtures.write_epub(path)
        verdict = filetype.inspect(path)
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.kind, "epub")
        self.assertIn("scripts", verdict.reason)

    @staticmethod
    def mobi(version):
        """A PalmDB header whose first record holds a MOBI header of `version`."""
        record0 = 96
        head = bytearray(b"\0" * 60 + b"BOOKMOBI" + b"\0" * 10 + record0.to_bytes(4, "big"))
        head += b"\0" * (record0 - len(head))
        head += b"\0" * 16 + b"MOBI" + b"\0" * 16 + version.to_bytes(4, "big") + b"\0" * 64
        return bytes(head)

    def test_classic_mobi_passes(self):
        verdict = filetype.inspect(self.make("book.mobi", self.mobi(6)))
        self.assertEqual((verdict.ok, verdict.kind), (True, "mobi"))

    def test_azw3_is_not_supported_yet(self):
        # MuPDF shows only the cover of a KF8 book, so the copy would be empty.
        verdict = filetype.inspect(self.make("book.azw3", self.mobi(8)))
        self.assertFalse(verdict.ok)
        self.assertIn("AZW3", verdict.what)

    def test_fb2_passes(self):
        data = b'\xef\xbb\xbf<?xml version="1.0"?>\n<FictionBook xmlns="x"></FictionBook>'
        verdict = filetype.inspect(self.make("book.fb2", data))
        self.assertEqual((verdict.ok, verdict.kind), (True, "fb2"))

    def test_cbz_passes(self):
        path = self.make_zip("comic.cbz", {"001.jpg": b"x", "002.png": b"y", "ComicInfo.xml": b"<x/>"})
        verdict = filetype.inspect(path)
        self.assertEqual((verdict.ok, verdict.kind), (True, "cbz"))

    def test_wrong_ending_is_a_warning_not_a_rejection(self):
        verdict = filetype.inspect(self.make("book.epub", fixtures.risky_pdf()))
        self.assertTrue(verdict.ok)
        self.assertEqual(verdict.kind, "pdf")
        self.assertTrue(any("'.epub'" in w for w in verdict.warnings))

    # Disguised programs fail.

    def test_windows_program_named_pdf_fails(self):
        verdict = filetype.inspect(self.make("book.pdf", b"MZ\x90\x00" + b"\0" * 200))
        self.assertFalse(verdict.ok)
        self.assertIn("Windows program", verdict.what)
        self.assertTrue(any("pretends to be '.pdf'" in w for w in verdict.warnings))

    def test_double_ending_is_flagged(self):
        verdict = filetype.inspect(self.make("book.pdf.exe", b"MZ" + b"\0" * 200))
        self.assertFalse(verdict.ok)
        self.assertTrue(any("Double ending" in w for w in verdict.warnings))

    def test_padded_name_is_flagged(self):
        verdict = filetype.inspect(self.make("book.pdf          .exe", b"MZ" + b"\0" * 200))
        self.assertTrue(any("pad the name" in w for w in verdict.warnings))

    def test_direction_trick_is_flagged(self):
        # Shows as "bookexe.pdf" in many file managers, but ends in .exe.
        verdict = filetype.inspect(self.make("book‮fdp.exe", b"MZ" + b"\0" * 200))
        self.assertFalse(verdict.ok)
        self.assertTrue(any("direction-control" in w for w in verdict.warnings))

    def test_other_programs_fail(self):
        samples = {
            "a.pdf": b"\x7fELF" + b"\0" * 60,
            "b.pdf": b"#!/bin/sh\nrm -rf ~\n",
            "c.pdf": b"\x4c\x00\x00\x00\x01\x14\x02\x00" + b"\0" * 60,
            "d.pdf": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 60,
            "e.pdf": b"\xcf\xfa\xed\xfe" + b"\0" * 60,
        }
        for name, data in samples.items():
            with self.subTest(name=name):
                self.assertFalse(filetype.inspect(self.make(name, data)).ok)

    def test_zip_hiding_a_program_fails(self):
        path = self.make_zip("book.zip", {"book.pdf": b"%PDF-1.7", "read me.pdf.lnk": b"x"})
        verdict = filetype.inspect(path)
        self.assertFalse(verdict.ok)
        self.assertIn("read me.pdf.lnk", verdict.reason)

    def test_epub_hiding_a_program_fails(self):
        files = fixtures.risky_epub()
        files["OEBPS/setup.exe"] = "MZ"
        path = os.path.join(self.dir.name, "book.epub")
        fixtures.write_epub(path, files)
        self.assertFalse(filetype.inspect(path).ok)

    def test_library_ms_in_zip_fails(self):
        # CVE-2025-24054: extracting this file type leaked Windows password hashes.
        path = self.make_zip("book.zip", {"book.library-ms": b"<xml/>"})
        self.assertFalse(filetype.inspect(path).ok)

    def test_plain_zip_of_a_book_is_rejected_with_its_contents(self):
        path = self.make_zip("book.zip", {"The Book.pdf": b"%PDF-1.7"})
        verdict = filetype.inspect(path)
        self.assertFalse(verdict.ok)
        self.assertIn("The Book.pdf", verdict.reason)

    def test_archives_and_web_pages_fail(self):
        samples = {
            "a.rar": b"Rar!\x1a\x07\x01\x00" + b"\0" * 20,
            "b.7z": b"7z\xbc\xaf\x27\x1c" + b"\0" * 20,
            "c.html": b"<!DOCTYPE html><html><script>x()</script></html>",
            "d.svg": b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>',
            "e.iso": b"\0" * 0x8001 + b"CD001" + b"\0" * 100,
        }
        for name, data in samples.items():
            with self.subTest(name=name):
                self.assertFalse(filetype.inspect(self.make(name, data)).ok)

    def test_djvu_is_not_supported_yet(self):
        verdict = filetype.inspect(self.make("scan.djvu", b"AT&TFORM\0\0\0\0DJVM"))
        self.assertFalse(verdict.ok)
        self.assertIn("DjVu", verdict.what)

    def test_empty_and_unknown_fail(self):
        self.assertFalse(filetype.inspect(self.make("empty.pdf", b"")).ok)
        self.assertFalse(filetype.inspect(self.make("noise.pdf", b"hello world")).ok)

    def test_broken_zip_fails(self):
        verdict = filetype.inspect(self.make("book.epub", b"PK\x03\x04" + b"\xff" * 50))
        self.assertFalse(verdict.ok)


class NameWarningTest(unittest.TestCase):
    def test_normal_names_have_no_warnings(self):
        for name in ("book.pdf", "J. R. R. Tolkien - The Hobbit.epub", "vol.2.azw3", "notes.v1.2.pdf"):
            with self.subTest(name=name):
                self.assertEqual(filetype.name_warnings(name), [])


if __name__ == "__main__":
    unittest.main()
