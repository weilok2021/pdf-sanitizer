# README rewrite (2026-11-11)

The user said the old README looked fully AI generated and pointed at Dangerzone and pi as references.

## What changed

- `README.md` went from about 1,800 words of tables and bold-led bullets to about 630 words of plain prose, shaped like Dangerzone's: a one-line tagline, a "works like this" paragraph that states the trust boundary, then Getting started, Usage, Formats, Limitations, Development and License.
- The option table is gone; the README points to `pdf-sanitizer --help`, which already lists every option.
- The threat model, the code tour and the size limits moved to `docs/security.md`.
- The right-click menu details moved to `docs/windows.md`.
- Dropped: the ASCII pipeline diagram, the speed table (one sentence remains) and the file-by-file table.

## Loose ends

- No screenshot of the right-click menu yet (see round 2).

## Round 2 (same day)

- The user asked to drop every Dangerzone mention ("we didn't look like it") and to make the README less dull for non-technical readers.
- Removed Dangerzone from `README.md` and `docs/security.md`. The gVisor gap is still stated, without the comparison. The internal notes still name Dangerzone as the design reference.
- Added `assets/logo.svg`, `assets/how-it-works.svg` (four-step picture) and `assets/terminal.svg`. The terminal text is real output from a run on 2026-11-11 (a 3-page test PDF and a fake `invoice.pdf.exe`); the colours are added for the picture.
- The images have their own light backgrounds, so they read on GitHub's light and dark themes. Checked with a headless Chrome screenshot.
- Still open: a real screenshot of the Windows right-click menu for `docs/windows.md`.
