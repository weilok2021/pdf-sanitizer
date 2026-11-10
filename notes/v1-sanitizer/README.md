# v1: pdf-sanitizer (first named ebook-sanitizer)

Built 2026-11-10. Claude wrote the code; the user reviews it. Per the global CLAUDE.md, it stays off the user's portfolio unless the user says otherwise.

## Why

The user downloads free ebooks and worries they carry malware. They chose a cleaner over a scanner: a scanner only spots attacks it already knows, while a cleaner rebuilds the document from pictures, so no code can survive. Renamed from ebook-sanitizer to pdf-sanitizer on 2026-11-10, because it works on any PDF, not only ebooks.

## What v1 does

- `pdf-sanitizer check FILE`: reads the first bytes and says what the file really is. It refuses programs, shortcuts, archives, web pages and AZW3. It flags double endings, padded names and direction-control characters.
- `pdf-sanitizer FILE`: renders each page in sandbox 1, checks the pixels on the host, rebuilds a PDF with OCR in sandbox 2, and saves `<name>.safe.pdf`.
- `pdf-sanitizer setup`: builds the sandbox image and deletes old ones.

## Decisions

- **CDR level 1 (flattening), copied from Dangerzone's design.** The research brief and sources are in the chat of 2026-11-10.
- **Two containers.** Both run with `--network none`, `--read-only`, `--cap-drop ALL`, `no-new-privileges`, user 65534, a 2 GB memory cap, 128 processes, no mounts, `--pull never` and `--log-driver none`.
- **The image tag is a hash of the container files**, so a code change forces `setup` again.
- **PyMuPDF 2.1** (MuPDF 1.28.6). It reads PDF, EPUB, MOBI, FB2 and CBZ.
- **OCR runs on the colour picture.** MuPDF's OCR finds no text in a grey picture. For a grey page, the picture data is then overwritten in place with the grey version, which cuts about 45% per text page.
- **JPEG was tested and rejected.** It made text pages bigger than Flate.
- **OCR runs in up to 8 worker processes** (forkserver, `OMP_THREAD_LIMIT=1`). Alice dropped from 217 s to 55 s.
- **A document whose pages all come out pure white is a failure.** This catches files the reader cannot really read. AZW3 did exactly this before the check refused it.

## Bugs found in testing

- **Ctrl+C hung the tool** and left a render container and a `.part` file. Cause: the docker client was stuck handing over pixels nobody read, so `docker rm -f` waited forever. Fix: kill the docker client first, then `docker rm -f` with a 30 s limit. `tests/test_interrupt.py` guards it and fails on the old code.

## Measured (12-core PC, 150 DPI, OCR on)

- Alice EPUB: 100 pages, 55 s, 0.2 MB to 9.6 MB.
- Alice MOBI: 111 pages, 61 s, 0.2 MB to 8.5 MB.
- arXiv PDF: 15 pages, 16 s, 2.1 MB to 2.8 MB. At 300 DPI: 31 s, 6.1 MB.
- 45 tests pass (`python3 -m unittest discover -s tests -t .`).

## Loose ends

- **Not reviewed yet.** Run `code-review` and `security-review` in a fresh session, not this one.
- **AZW3 (KF8) is unsupported.** Option: unpack it to EPUB inside sandbox 1 with KindleUnpack or the `mobi` package, then render.
- **DjVu is unsupported.** It is common for scanned books. Option: add djvulibre to sandbox 1.
- **FB2 and CBZ are untested on real files.** Only detection is tested.
- **No gVisor layer yet.** Dangerzone runs gVisor inside its container. Note that Docker Desktop on WSL shares one VM kernel with the user's WSL distro, so a kernel escape would reach it.
- **The copy has no table of contents.** Option: pass the bookmarks out of sandbox 1 as checked plain text.
- **OCR is English only.** Add `tesseract-ocr-chi-sim` and similar if the user reads other languages.
- **PyMuPDF is AGPL-3.0.** Pick a licence before publishing.
- **The image is 623 MB.**
- **Not done:** the `~/.local/bin` symlink (the user can add it; see the README).

## Speed findings (2026-11-10)

- Per page on one core (Alice, 150 DPI): OCR 2.56 s (94%), page compression 0.15 s, drawing 0.02 s, all Python code 0.009 s (0.3%). Saving a 390-page PDF: 0.6 s in total.
- Python is not the bottleneck. OCR is. The user accepted the current speed.
- Ideas if speed matters later: Tesseract "fast" model (not measured), 10 workers instead of 8, or taking text from the original instead of OCR (exact and fast, but hidden text can differ from the picture).

## Optional idea: run without Docker (not planned)

- Option B: rebuild the tool on WebAssembly (MuPDF.js + tesseract.js) as a normal app or an offline web page. No Docker, no VM, no admin rights.
- Only worth it if the tool is given to non-technical users. Decision on 2026-11-10: keep the Docker version as the product.
- Before any rewrite: a one or two-day spike that renders one PDF page with MuPDF.js, rebuilds it and measures speed.
- Reference: Dangerzone 0.10.0 bundles Podman so its users skip Docker Desktop (https://dangerzone.rocks/news/2025-12-02-0.10.0/).
