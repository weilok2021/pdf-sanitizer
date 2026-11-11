# README rewrite (2026-11-11)

The user said the old README looked fully AI generated and pointed at Dangerzone and pi as references.

## What changed

- `README.md` went from about 1,800 words of tables and bold-led bullets to about 630 words of plain prose, shaped like Dangerzone's: a one-line tagline, a "works like this" paragraph that states the trust boundary, then Getting started, Usage, Formats, Limitations, Development and License.
- The option table is gone; the README points to `pdf-sanitizer --help`, which already lists every option.
- The threat model, the code tour and the size limits moved to `docs/security.md`.
- The right-click menu details moved to `docs/windows.md`.
- Dropped: the ASCII pipeline diagram, the speed table (one sentence remains) and the file-by-file table.

## Loose ends

- No screenshots. Dangerzone shows two; a terminal run and the right-click menu would be the obvious ones.
