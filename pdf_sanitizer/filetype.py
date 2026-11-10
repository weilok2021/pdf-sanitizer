"""Find out what a file really is, from its bytes, not its name.

This is the one scanner check that pdf-sanitizer keeps. It catches the most
common trick: a program or shortcut dressed up as a document. It never opens
the document itself. Only the sandbox does that.
"""

import os
import zipfile
from dataclasses import dataclass, field

# Formats the sandbox can draw. The value is the PyMuPDF file type name.
BOOK_KINDS = {
    "pdf": "PDF document",
    "epub": "EPUB ebook",
    "mobi": "MOBI/AZW3 ebook",
    "fb2": "FB2 ebook",
    "cbz": "CBZ comic",
}

# File endings that run code or point Windows somewhere else when opened.
RISKY_ENDINGS = {
    ".exe", ".dll", ".scr", ".com", ".pif", ".cpl", ".msi", ".msp",
    ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".jse", ".wsf", ".wsh",
    ".hta", ".jar", ".lnk", ".url", ".library-ms", ".searchconnector-ms",
    ".reg", ".iso", ".img", ".vhd", ".vhdx", ".appx", ".msix", ".chm",
}

# The ending a user expects to see on a document.
BOOK_ENDINGS = {".pdf", ".epub", ".mobi", ".azw", ".azw3", ".fb2", ".cbz", ".djvu", ".txt"}

IMAGE_ENDINGS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# Unicode controls that can reorder or hide parts of a file name.
BIDI_CONTROLS = {
    "‪", "‫", "‬", "‭", "‮",
    "⁦", "⁧", "⁨", "⁩", "‎", "‏",
}

HEAD_SIZE = 64 * 1024  # enough to reach an ISO 9660 signature at 0x8001


@dataclass
class Verdict:
    ok: bool  # True when the sandbox may open the file
    kind: str | None  # one of BOOK_KINDS when ok
    what: str  # plain name of what the file really is
    reason: str  # one plain sentence for the user
    warnings: list[str] = field(default_factory=list)


def _reject(what, reason, warnings):
    return Verdict(False, None, what, reason, warnings)


def _accept(kind, warnings, extra=None):
    reason = "Real document. It can be sanitized."
    if extra:
        reason = f"{reason} {extra}"
    return Verdict(True, kind, BOOK_KINDS[kind], reason, warnings)


def name_warnings(name):
    """Warnings about tricks in the file name itself."""
    warnings = []
    if any(ch in BIDI_CONTROLS for ch in name):
        warnings.append("The name hides direction-control characters, a trick to fake the ending.")
    parts = name.lower().split(".")
    if len(parts) >= 3:
        inner = "." + parts[-2].strip()
        outer = "." + parts[-1].strip()
        if inner in BOOK_ENDINGS and outer not in BOOK_ENDINGS:
            warnings.append(f"Double ending: it looks like '{inner}' but really ends in '{outer}'.")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem != stem.rstrip(" ._"):
        warnings.append("Spaces or dots pad the name before the ending, a trick to push the real ending out of view.")
    return warnings


def _ending(name):
    base = name.lower().rstrip(" .")
    return os.path.splitext(base)[1]


def _program_kind(head):
    """Name the program or shortcut type if head starts like one."""
    if head[:2] == b"MZ":
        return "Windows program (.exe, .dll or .scr)"
    if head[:4] == b"\x7fELF":
        return "Linux program"
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return "macOS program"
    if head[:4] == b"\xca\xfe\xba\xbe":
        return "macOS program or Java class file"
    if head[:2] == b"#!":
        return "script"
    if head[:8] == b"\x4c\x00\x00\x00\x01\x14\x02\x00":
        return "Windows shortcut (.lnk)"
    if head[:4] == b"ITSF":
        return "Windows help file (.chm), which can run scripts"
    return None


def _archive_kind(head):
    if head[:7] == b"Rar!\x1a\x07\x00" or head[:8] == b"Rar!\x1a\x07\x01\x00":
        return "RAR archive"
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7-Zip archive"
    if head[:2] == b"\x1f\x8b":
        return "gzip archive"
    if head[0x8001:0x8006] == b"CD001":
        return "disk image (.iso)"
    return None


def _mobi_version(head):
    """The MOBI format version: 6 for classic MOBI, 8 for AZW3 (KF8).

    The first PalmDB record starts at the offset stored at byte 78. Its MOBI
    header holds the version 36 bytes in. Return 0 if it cannot be read.
    """
    if len(head) < 82:
        return 0
    record0 = int.from_bytes(head[78:82], "big")
    if len(head) < record0 + 40 or head[record0 + 16 : record0 + 20] != b"MOBI":
        return 0
    return int.from_bytes(head[record0 + 36 : record0 + 40], "big")


def _looks_like_markup(head, tag):
    text = head[:4096].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return tag in text


def _inspect_zip(path, warnings):
    try:
        with zipfile.ZipFile(path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            names = [i.filename for i in infos]
            risky = [n for n in names if _ending(n) in RISKY_ENDINGS]
            if risky:
                shown = ", ".join(risky[:5])
                return _reject(
                    "ZIP archive with programs inside",
                    f"It hides programs or shortcuts ({shown}). Delete it.",
                    warnings,
                )
            if "[Content_Types].xml" in names and any(n.startswith(("word/", "xl/", "ppt/")) for n in names):
                return _reject("Word, Excel or PowerPoint file", "Office files are not supported yet.", warnings)
            mimetype = next((i for i in infos if i.filename == "mimetype"), None)
            if mimetype is not None and mimetype.file_size <= 100:
                if zf.read(mimetype).strip() == b"application/epub+zip":
                    scripts = [n for n in names if _ending(n) == ".js"]
                    extra = None
                    if scripts:
                        extra = "It carries scripts; sanitizing removes them."
                    return _accept("epub", warnings, extra)
            if names and all(_ending(n) in IMAGE_ENDINGS or n.lower() == "comicinfo.xml" for n in names):
                return _accept("cbz", warnings)
            shown = ", ".join(names[:5]) or "nothing"
            return _reject(
                "ZIP archive",
                f"It is an archive, not a document. It contains: {shown}.",
                warnings,
            )
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError):
        return _reject("broken ZIP archive", "It looks like a ZIP file but cannot be read.", warnings)


def inspect(path):
    """Return a Verdict for the file at path."""
    name = os.path.basename(path)
    warnings = name_warnings(name)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(HEAD_SIZE)
    except OSError as e:
        return _reject("unreadable file", f"It cannot be read: {e.strerror}.", warnings)

    if size == 0:
        return _reject("empty file", "The file is empty.", warnings)

    program = _program_kind(head)
    if program:
        claimed = _ending(name)
        if claimed in BOOK_ENDINGS:
            warnings.append(f"The name pretends to be '{claimed}'.")
        return _reject(program, "It is a program, not a document. Do not open it. Delete it.", warnings)

    # Old Office files and Windows installers share one container format.
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        if _ending(name) in BOOK_ENDINGS:
            warnings.append(f"The name pretends to be '{_ending(name)}'.")
        return _reject(
            "old Office file or Windows installer (.doc, .xls, .ppt, .msi)",
            "Office files are not supported yet. If it is an .msi installer, do not run it.",
            warnings,
        )

    archive = _archive_kind(head)
    if archive:
        return _reject(archive, "It is an archive or disk image, not a document.", warnings)

    # ZIP comes before PDF: a ZIP that stores a PDF uncompressed also has
    # "%PDF-" near its start, and must not pass as a plain PDF.
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        verdict = _inspect_zip(path, warnings)
        if verdict.ok:
            _note_mismatch(name, verdict.kind, warnings)
        return verdict
    if b"%PDF-" in head[:1024]:
        kind = "pdf"
    elif head[:8] == b"AT&TFORM":
        return _reject("DjVu document", "DjVu is not supported yet.", warnings)
    elif len(head) >= 68 and head[60:68] in (b"BOOKMOBI", b"TEXtREAd"):
        if _mobi_version(head) >= 8:
            return _reject(
                "AZW3 (Kindle KF8) ebook",
                "AZW3 is not supported yet: the sandbox reader only shows its cover.",
                warnings,
            )
        kind = "mobi"
    elif _looks_like_markup(head, b"<fictionbook"):
        kind = "fb2"
    elif _looks_like_markup(head, b"<html") or _looks_like_markup(head, b"<!doctype html"):
        return _reject("web page (HTML)", "It is a web page, which can run scripts in your browser.", warnings)
    elif _looks_like_markup(head, b"<svg"):
        return _reject("SVG image", "It is an SVG image, which can run scripts in your browser.", warnings)
    else:
        return _reject("unknown file type", "It is not a format pdf-sanitizer knows.", warnings)

    _note_mismatch(name, kind, warnings)
    return _accept(kind, warnings)


_KIND_ENDINGS = {
    "pdf": {".pdf"},
    "epub": {".epub"},
    "mobi": {".mobi", ".azw", ".azw3", ".prc"},
    "fb2": {".fb2"},
    "cbz": {".cbz", ".zip"},
}


def _note_mismatch(name, kind, warnings):
    ending = _ending(name)
    if ending not in _KIND_ENDINGS[kind]:
        warnings.append(f"The name ends in '{ending or '(nothing)'}' but the content is {BOOK_KINDS[kind]}.")
