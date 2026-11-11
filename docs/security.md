# Security

pdf-sanitizer does what security people call Content Disarm and Reconstruction (CDR) at its simplest level: flattening. It doesn't try to decide whether a document is malicious. It throws the document away and keeps only a picture of each page, so the copy is safe either way.

## How a file moves through the tool

1. **File-type check (host).** `pdf_sanitizer/filetype.py` reads the first bytes of the file and ignores its name. Anything that isn't a supported document is refused here, before any reader touches it.
2. **Render (container 1).** `container/render.py` opens the document with PyMuPDF and writes each page out as raw pixels. This is the only code that reads the untrusted file.
3. **Check (host).** `pdf_sanitizer/sandbox.py` reads the pixel stream. It rejects a page whose size is malformed or too large, and stops both containers if nothing arrives before the timeout. The stream format lives in `pdf_sanitizer/protocol.py`.
4. **Rebuild (container 2).** `container/rebuild.py` turns the pixels into PDF pages and runs OCR on them.

Both containers run with `--network none`, a read-only file system, every Linux capability dropped, `no-new-privileges`, an unprivileged user, a 2 GB memory limit, a 128-process limit and no host folders mounted. The image tag is a hash of the container files, so changing the container code forces a fresh `setup`.

The `pdf-sanitizer` launcher runs Python in isolated mode, so a planted `.py` file in your Downloads folder can't be imported.

## What it protects against

A program disguised as a document. The file-type check refuses it. It also refuses any file whose real extension makes Windows run it (`.cmd`, `.js`, `.hta` and others) or open it in a browser (`.html`, `.svg` and others), even when the content looks like a document. It flags double extensions (`.pdf.exe`), names padded with spaces or dots to push the real extension out of view, and hidden direction characters that fake an extension.

A booby-trapped PDF or ebook. It can only attack the reader inside container 1, which has no network and can't see your files. Only pixels come out, and the host checks them.

Scripts, links, attachments, forms and auto-run actions. None of them survive into the copy. The tests prove this on a PDF and an EPUB that carry all of them.

A stuck or hijacked reader. The host stops the containers after the timeout and rejects malformed page data.

A crafted file name that fakes the tool's output. Every name the tool prints, including names inside a ZIP file, is escaped, so terminal codes and direction characters show up as plain text.

## What it doesn't protect against

A container escape. A bug in Docker or the Linux kernel could let an attack break out of container 1. There is no second layer, such as gVisor, yet. On Docker Desktop for Windows, the containers share one VM kernel with your WSL distro, so an escape would reach it.

The original file. It stays on your disk. Delete it once you've checked the copy.

## Known limits

- A page picture is at most 10,000 pixels on its longest side. A page longer than that at your `--dpi` (an A0 poster at `--dpi 300`, or a 1.7 m banner at the default 150) is drawn at lower sharpness, so it comes out physically smaller in the copy. Nothing on it is lost.
- Container 2 holds the whole copy in memory, up to 2 GB. A very large copy, such as a long colour comic, fails with an out-of-memory message.
- A document whose pages all come out blank is treated as a failure, because it usually means the reader couldn't really read the file.
