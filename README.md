# ebook-sanitizer

Makes a safe PDF copy of an ebook you do not trust.

- It opens the book inside a locked-down Docker container: no internet, no access to your files, no admin rights.
- It draws every page as a picture.
- It builds a new PDF from those pictures and adds OCR text, so you can still search and copy the text.
- A picture cannot carry code. The copy is safe even if the original was booby-trapped.

Before it opens anything, it checks what the file really is. A program dressed up as a book (for example `book.pdf.exe`) is refused.

```
book.epub
   │  1. file-type check: is this really a book?
   ▼
┌─ Sandbox 1: render (Docker) ───────────────┐
│ no network, no shared folders, no root     │
│ opens the book, draws each page as pixels  │
└────────────────────────────────────────────┘
   │  2. raw pixels only; the host checks every page size
   ▼
┌─ Sandbox 2: rebuild (Docker) ──────────────┐
│ pixels → PDF pages, plus OCR text          │
└────────────────────────────────────────────┘
   ▼
book.safe.pdf
```

This is the "flattening" level of Content Disarm and Reconstruction (CDR). The design follows [Dangerzone](https://dangerzone.rocks/) by the Freedom of the Press Foundation. No Dangerzone code is copied.

## Setup (once)

You need:
- Docker Desktop, running.
- Python 3.10 or newer. Nothing to install with pip: the host side uses the standard library only.

```
cd ~/workspace/ebook-sanitizer
./ebook-sanitizer setup
```

`setup` builds the sandbox image. It is the only step that uses the internet.

To type `ebook-sanitizer` from any folder:

```
mkdir -p ~/.local/bin
ln -s ~/workspace/ebook-sanitizer/ebook-sanitizer ~/.local/bin/ebook-sanitizer
```

## Use

```
ebook-sanitizer check book.epub        # what is this file really? Opens nothing.
ebook-sanitizer book.epub              # makes book.safe.pdf next to it
ebook-sanitizer -o ~/Books *.epub      # many books, saved in another folder
```

Your Windows Downloads folder is at `/mnt/c/Users/<your Windows name>/Downloads` inside WSL.

| Option | What it does |
|---|---|
| `-o DIR`, `--out-dir DIR` | Save the safe copies in DIR. Default: next to each book. |
| `--dpi N` | Picture sharpness, 72 to 300. Default 150. Higher is sharper, bigger and slower. |
| `--no-ocr` | Skip OCR. About 5 times faster, but the text is not searchable. |
| `--lang CODE` | OCR language. Default `eng`. Only English is installed for now. |
| `--timeout SECONDS` | Stop when nothing happens for this long. Default 300. |
| `--force` | Replace a safe copy that already exists. |

## Formats

| Format | Status |
|---|---|
| PDF | Supported. Tested on a real paper. |
| EPUB | Supported. Tested on a real book. |
| MOBI (classic Kindle) | Supported. Tested on a real book. |
| FB2, CBZ | Supported by the reader, but not yet tested on a real file. |
| AZW3 (Kindle KF8) | Not yet. The reader shows only the cover, so the tool refuses it. |
| DjVu | Not yet. |

## What it protects against

- **A program disguised as a book.** The file-type check refuses it before anything opens it. The check also flags double endings (`.pdf.exe`), padded names, and hidden direction characters that fake an ending.
- **A booby-trapped PDF or EPUB.** It can only attack the reader inside the sandbox. That reader has no network and cannot see your files. Only pixels come out, and the host checks every page size.
- **Scripts, links, attachments, forms and auto-run actions.** None of them survive. The tests prove it on a PDF and an EPUB that carry all of these.
- **A stuck or hijacked reader.** The host stops the containers when nothing happens for the timeout, and rejects malformed page data.

## What it does not do

- It does not tell you whether the original was malicious. It makes a copy that is safe either way.
- A bug in Docker or in the Linux kernel could let an attack escape the container. Dangerzone adds a second sandbox (gVisor) for this. ebook-sanitizer does not have it yet.
- The original stays on your disk. Delete it once you have checked the copy.
- The copy loses text reflow, clickable links and the table of contents. It is also bigger than the original.

## Speed and size

Measured on this PC (12 cores), at the default settings:

| Book | Pages | Time | Original | Safe copy |
|---|---|---|---|---|
| Alice in Wonderland (EPUB) | 100 | 55 s | 0.2 MB | 9.6 MB |
| Alice in Wonderland (MOBI) | 111 | 61 s | 0.2 MB | 8.5 MB |
| Attention Is All You Need (PDF) | 15 | 16 s | 2.1 MB | 2.8 MB |

OCR takes most of the time. Without OCR, the Alice EPUB took 10 s.

## Files

| File | Job |
|---|---|
| `ebook-sanitizer` | The command. Runs Python in isolated mode, so a planted `.py` file in your Downloads folder can never be imported. |
| `ebook_sanitizer/filetype.py` | The file-type check. Reads the first bytes, never trusts the name. |
| `ebook_sanitizer/sandbox.py` | Starts both containers, checks the pixels between them, stops them on a timeout. |
| `ebook_sanitizer/protocol.py` | The rules for the pixel stream. The container image copies this file. |
| `ebook_sanitizer/text.py` | Makes file names and error messages safe to print in a terminal. |
| `container/render.py` | Sandbox 1. The only code that ever reads the untrusted book. |
| `container/rebuild.py` | Sandbox 2. Builds the PDF and runs OCR on several pages at once. |
| `container/Dockerfile` | The sandbox image: Python 3.12, PyMuPDF 2.1, English OCR data. |

## Tests

```
python3 -m unittest discover -s tests -t .
```

The tests in `tests/test_sandbox.py` need Docker and a built sandbox. They check that:
- the container has no network, no root, no Linux capabilities, a read-only file system, no host folders, and memory and process limits;
- a PDF and an EPUB full of risky features come out with none of them, and with their text;
- a fake hijacked reader that sends oversized pages, prints terminal escape codes, hangs, or returns blank pages is caught.

## Licence

PyMuPDF, which runs inside the sandbox, is licensed under AGPL-3.0. Keep that in mind before you publish or share this project.
