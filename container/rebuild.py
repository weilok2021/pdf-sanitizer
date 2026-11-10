"""Stage 2, inside the sandbox: build a new PDF from page pixels.

Input:  the pixel stream described in protocol.py, on stdin. The host has
        already checked it, and this stage checks it again.
Output: the finished PDF on stdout.
Usage:  python -I rebuild.py <dpi> <ocr|no-ocr> <language>

This stage never sees the original document. Each page of the new PDF is one
picture, plus invisible OCR text when OCR is on, so you can still search
and select the text. OCR is slow, so it runs on several pages at once in
worker processes, and the pages are put back in order.
"""

import concurrent.futures
import multiprocessing
import os
import re
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import protocol  # noqa: E402
import pymupdf  # noqa: E402

MAX_WORKERS = 8


def fail(message):
    print(f"rebuild: {message}", file=sys.stderr)
    sys.exit(2)


def worker_count():
    """Leave two cores for the rest of the computer, and use at most 8."""
    return max(1, min(MAX_WORKERS, (os.cpu_count() or 1) - 2))


def gray_samples(samples):
    """Return one value per pixel if the page is grey, else None.

    A grey page stores one value per pixel instead of three, which makes
    a text page about half the size in the finished PDF.
    """
    red = samples[0::3]
    if red == samples[1::3] and red == samples[2::3]:
        return red
    return None


def use_gray_image(doc, width, height, gray):
    """Swap the page picture in a one-page OCR PDF for its grey version.

    OCR must run on the colour picture (MuPDF finds no text in a grey one),
    so this overwrites the picture's data afterwards, in place. If the PDF
    does not hold exactly the picture expected, it stays in colour.
    """
    images = doc[0].get_images()
    if len(images) != 1:
        return
    xref, _, img_width, img_height, bits, colorspace = images[0][:6]
    if (img_width, img_height, bits, colorspace) != (width, height, 8, "DeviceRGB"):
        return
    doc.update_stream(xref, gray, compress=True)
    doc.xref_set_key(xref, "ColorSpace", "/DeviceGray")
    doc.xref_set_key(xref, "DecodeParms", "null")


def ocr_page(width, height, samples, dpi, language):
    """Runs in a worker process. Return a one-page PDF with OCR text."""
    gray = gray_samples(samples)
    pix = pymupdf.Pixmap(pymupdf.csRGB, width, height, samples, 0)
    pix.set_dpi(dpi, dpi)
    with pymupdf.open("pdf", pix.pdfocr_tobytes(compress=True, language=language)) as doc:
        if gray is not None:
            use_gray_image(doc, width, height, gray)
        return doc.tobytes()


def add_image_page(out, width, height, samples, dpi):
    """Add one page that holds only the picture (no OCR)."""
    gray = gray_samples(samples)
    if gray is not None:
        pix = pymupdf.Pixmap(pymupdf.csGRAY, width, height, gray, 0)
    else:
        pix = pymupdf.Pixmap(pymupdf.csRGB, width, height, samples, 0)
    page = out.new_page(width=width * 72 / dpi, height=height * 72 / dpi)
    page.insert_image(page.rect, pixmap=pix)


def read_page(inp):
    width, height = protocol.read_page_size(inp)
    samples = protocol.read_exact(inp, protocol.page_bytes(width, height))
    return width, height, samples


def append_pdf(out, one_page):
    with pymupdf.open("pdf", one_page) as src:
        out.insert_pdf(src)


def build_with_ocr(inp, out, pages, dpi, language):
    workers = worker_count()
    context = multiprocessing.get_context("forkserver")
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
        pending = deque()
        for _ in range(pages):
            width, height, samples = read_page(inp)
            pending.append(pool.submit(ocr_page, width, height, samples, dpi, language))
            # Keep only a few pages in memory at once.
            while len(pending) > workers + 2:
                append_pdf(out, pending.popleft().result())
        protocol.expect_end(inp)
        while pending:
            append_pdf(out, pending.popleft().result())


def build_without_ocr(inp, out, pages, dpi):
    for _ in range(pages):
        add_image_page(out, *read_page(inp), dpi)
    protocol.expect_end(inp)


def main():
    if len(sys.argv) != 4 or sys.argv[2] not in ("ocr", "no-ocr"):
        fail("usage: rebuild.py <dpi> <ocr|no-ocr> <language>")
    dpi = int(sys.argv[1])
    ocr = sys.argv[2] == "ocr"
    language = sys.argv[3]
    if not re.fullmatch(r"[a-z_]{3,20}(\+[a-z_]{3,20}){0,4}", language):
        fail("bad OCR language name")

    inp = sys.stdin.buffer
    out = pymupdf.open()
    try:
        pages = protocol.read_header(inp)
        if ocr:
            build_with_ocr(inp, out, pages, dpi, language)
        else:
            build_without_ocr(inp, out, pages, dpi)
    except protocol.StreamError as e:
        fail(f"bad pixel stream: {e}")

    sys.stdout.buffer.write(out.tobytes(garbage=3, deflate=True))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
