"""Stage 1, inside the sandbox: draw every page of an untrusted book as pixels.

Input:  the book's bytes on stdin.
Output: the pixel stream described in protocol.py, on stdout.
Usage:  python -I render.py <kind> <dpi>

This is the only code that ever parses the untrusted book. Assume a bad
book can take it over. The container around it has no network, no shared
folders and no root, and the host checks every number this stage sends.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import protocol  # noqa: E402
import pymupdf  # noqa: E402

KINDS = {"pdf", "epub", "mobi", "fb2", "cbz"}
MAX_INPUT = 1024 * 1024 * 1024  # 1 GiB

# Page layout for reflowing books (EPUB, MOBI, FB2), in points.
# These match MuPDF's own defaults and read well on a laptop screen.
REFLOW_WIDTH = 450
REFLOW_HEIGHT = 600
REFLOW_FONT_SIZE = 12


def fail(message):
    print(f"render: {message}", file=sys.stderr)
    sys.exit(2)


def zoom_for(page, dpi):
    """Zoom factor for dpi, shrunk if the page would pass MAX_SIDE pixels."""
    zoom = dpi / 72
    rect = page.rect
    longest = max(rect.width, rect.height) * zoom
    limit = protocol.MAX_SIDE - 2  # room for rounding
    if longest > limit:
        zoom *= limit / longest
    return zoom


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in KINDS:
        fail("usage: render.py <pdf|epub|mobi|fb2|cbz> <dpi>")
    kind = sys.argv[1]
    dpi = int(sys.argv[2])

    data = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        fail("the book is larger than 1 GiB")

    try:
        doc = pymupdf.open(stream=data, filetype=kind)
    except Exception as e:  # MuPDF raises many types for broken files
        fail(f"cannot open the book: {e}")

    if doc.needs_pass:
        fail("the book is password-protected")
    if doc.is_reflowable:
        doc.layout(width=REFLOW_WIDTH, height=REFLOW_HEIGHT, fontsize=REFLOW_FONT_SIZE)

    pages = doc.page_count
    if not 1 <= pages <= protocol.MAX_PAGES:
        fail(f"the book has {pages} pages; the limit is 1 to {protocol.MAX_PAGES}")

    out = sys.stdout.buffer
    out.write(protocol.pack_header(pages))
    for number in range(pages):
        try:
            page = doc[number]
            zoom = zoom_for(page, dpi)
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(zoom, zoom),
                colorspace=pymupdf.csRGB,
                alpha=False,
            )
        except Exception as e:
            fail(f"cannot draw page {number + 1}: {e}")
        if pix.n != protocol.CHANNELS or pix.stride != pix.width * protocol.CHANNELS:
            fail(f"page {number + 1} has an unexpected pixel layout")
        out.write(protocol.pack_page_size(pix.width, pix.height))
        out.write(pix.samples_mv)
    out.flush()


if __name__ == "__main__":
    main()
