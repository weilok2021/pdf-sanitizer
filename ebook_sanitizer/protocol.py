"""The pixel stream that passes between the two sandbox stages.

The render stage writes this stream. The host checks every number in it,
then writes a fresh copy for the rebuild stage. Only page sizes and raw
RGB colour values travel this way, so no code can travel with them.

Stream layout. Every number is an unsigned 32-bit big-endian integer.

    magic   b"BCP1"
    pages   how many pages follow
    then, for each page:
        width   in pixels
        height  in pixels
        width * height * 3 bytes of RGB pixels, row by row

This file must use the standard library only. The sandbox image copies it.
"""

import struct

MAGIC = b"BCP1"
MAX_PAGES = 5000
MAX_SIDE = 10000  # pixels, for both width and height
CHANNELS = 3  # RGB

_U32 = struct.Struct(">I")
_SIZE = struct.Struct(">II")


class StreamError(Exception):
    """The pixel stream broke a rule. Treat the whole document as unsafe."""


def read_exact(stream, n):
    """Read exactly n bytes, or raise StreamError."""
    chunks = []
    left = n
    while left > 0:
        chunk = stream.read(left)
        if not chunk:
            raise StreamError(f"stream ended early: expected {n} bytes, got {n - left}")
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def check_pages(pages):
    if not 1 <= pages <= MAX_PAGES:
        raise StreamError(f"page count {pages} is outside 1..{MAX_PAGES}")
    return pages


def check_size(width, height):
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE):
        raise StreamError(f"page size {width}x{height} is outside 1..{MAX_SIDE} pixels")
    return width, height


def page_bytes(width, height):
    return width * height * CHANNELS


def pack_header(pages):
    return MAGIC + _U32.pack(check_pages(pages))


def pack_page_size(width, height):
    return _SIZE.pack(*check_size(width, height))


def read_header(stream):
    """Read the magic and page count. Return the page count."""
    magic = read_exact(stream, len(MAGIC))
    if magic != MAGIC:
        raise StreamError("stream does not start with the expected magic bytes")
    (pages,) = _U32.unpack(read_exact(stream, _U32.size))
    return check_pages(pages)


def read_page_size(stream):
    """Read one page's width and height. Return (width, height)."""
    width, height = _SIZE.unpack(read_exact(stream, _SIZE.size))
    return check_size(width, height)


def expect_end(stream):
    """Raise StreamError if any bytes follow the last page."""
    if stream.read(1):
        raise StreamError("extra bytes after the last page")
