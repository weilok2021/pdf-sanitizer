"""Build harmless test books that carry the features attackers abuse.

Nothing here is malware. The "evil.exe" attachment is a few plain bytes.
The point is to prove that every dangerous feature is gone after sanitizing.
"""

import zipfile

PDF_MARKER = "HELLO SANDBOX"
EPUB_MARKER = "HELLO EPUB"


def _pdf(objects):
    """Assemble numbered PDF objects into a file with a correct xref table."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _stream(data):
    return b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def risky_pdf():
    """A one-page PDF with JavaScript on open, a Launch link, a tracking URL
    on page open, an attached 'program' and a form."""
    content = (
        f"BT /F1 28 Tf 72 700 Td ({PDF_MARKER}) Tj ET\n"
        "BT /F1 14 Tf 72 660 Td (The quick brown fox jumps over the lazy dog.) Tj ET\n"
    ).encode()
    objects = [
        # 1 catalog: JavaScript on open, attachments, a form
        b"<< /Type /Catalog /Pages 2 0 R /OpenAction 5 0 R"
        b" /Names << /EmbeddedFiles 7 0 R /JavaScript << /Names [(init) 5 0 R] >> >>"
        b" /AcroForm << /Fields [] >> >>",
        # 2 page tree
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        # 3 page: a Launch link and an action when the page opens
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 6 0 R >> >> /Annots [9 0 R] /AA << /O 10 0 R >> >>",
        # 4 page text
        _stream(content),
        # 5 JavaScript action
        b"<< /Type /Action /S /JavaScript /JS (app.alert\\('this would run on open'\\);) >>",
        # 6 font
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        # 7 attachment list
        b"<< /Names [(evil.exe) 8 0 R] >>",
        # 8 attachment
        b"<< /Type /Filespec /F (evil.exe) /EF << /F 11 0 R >> >>",
        # 9 link that launches a program
        b"<< /Type /Annot /Subtype /Link /Rect [72 600 300 620]"
        b" /A << /S /Launch /F (cmd.exe) >> >>",
        # 10 tracking URL on page open
        b"<< /S /URI /URI (http://tracker.invalid/opened) >>",
        # 11 attached bytes
        b"<< /Type /EmbeddedFile /Length 24 >>\nstream\nMZ harmless test bytes!!\nendstream",
    ]
    return _pdf(objects)


def risky_epub():
    """A small EPUB with inline and external scripts and a web link."""
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>"
    )
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">test-book</dc:identifier><dc:title>Test</dc:title>'
        "<dc:language>en</dc:language></metadata>"
        '<manifest><item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml" properties="scripted"/>'
        '<item id="js" href="evil.js" media-type="application/javascript"/></manifest>'
        '<spine><itemref idref="c1"/></spine></package>'
    )
    chapter = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title>'
        '<script src="evil.js"></script></head><body>'
        f"<h1>{EPUB_MARKER}</h1>"
        "<p>The quick brown fox jumps over the lazy dog.</p>"
        "<script>document.title = 'this would run in a reader';</script>"
        '<p><a href="http://tracker.invalid/clicked">a web link</a></p>'
        "</body></html>"
    )
    return {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": container,
        "OEBPS/content.opf": opf,
        "OEBPS/chapter1.xhtml": chapter,
        "OEBPS/evil.js": "fetch('http://tracker.invalid/ran');",
    }


def write_epub(path, files=None):
    files = files or risky_epub()
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), files["mimetype"], compress_type=zipfile.ZIP_STORED)
        for name, text in files.items():
            if name != "mimetype":
                zf.writestr(name, text, compress_type=zipfile.ZIP_DEFLATED)
