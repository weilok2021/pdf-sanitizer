# Fixes from the first code review (2026-11-11)

Branch `fix/review-findings`. A `/code-review` in a fresh session reviewed the whole repo and reported 10 findings. The implementing session checked each one; four were reproduced before fixing.

| # | Finding | Check | Fix |
|---|---|---|---|
| 2 | Output write errors hidden in the `_sink` thread | Reproduced. Short document: **reported success** with a cut-off 12 KB file. Long one: "nothing happened" after the timeout. | `_sink` records the error and stops both stages; `sanitize` raises "Could not write the safe copy: ...". |
| 1 | An OSError (read-only folder) stopped the batch with a traceback | Reproduced. | `_sanitize_one` catches OSError; the batch goes on. |
| 4 | `--lang deu` failed inside the sandbox with a traceback | Reproduced (`TypeError: cannot pickle`). | `--lang` is checked against `sandbox.OCR_LANGUAGES` at the start. Keep that list in step with the Dockerfile. |
| 3 | Read errors on the original hidden; render waited for the timeout | From the code. | `_feed` always closes stdin; a read error stops both stages and says "Could not read the document". A cut-off document must not render: it could give a copy with missing pages. |
| 9 | Failure message printed on the "page 3/80" line | From the code. | `_Progress.end()` before a failure message. |
| 10 | Ctrl+C test counted other runs' containers | From the code. Likely cause of the one unexplained failure while the EPUB helper ran (not proven). | Container names now carry the host process id; the test counts only its own. |
| 6 | `Book.pdf` + `book.epub` with `--force` overwrite each other on `/mnt/c` | From the code. | The same-run check ignores case. |
| 8 | Whole copy held in memory; OOM showed only "rebuild failed" | From the code. | Exit code 137 now says "ran out of memory (the limit is 2GB)". The memory use itself is unchanged; README lists it as a limit. |
| 5 | Very long pages come out smaller | Partly right: at 150 dpi an A0 page is 7,020 px, under the 10,000 limit. Only above about 214 dpi, or pages over 1.7 m. | README limit, no code change. Fixing it needs the page size in the pixel stream. |
| 7 | Silent stretches look like a hang with a short `--timeout` | Right only for short timeouts; default is 300 s. | README hint on `--timeout`. |

No container file changed, so the sandbox image tag stays the same and no `setup` is needed.

Tests added: `tests/test_cli.py` (new) and `HostFileErrorTest` plus `test_out_of_memory_says_so` in `tests/test_sandbox.py`. All 9 bug tests fail on `main` (c6a1afa) and pass on this branch.

Loose ends:
- Finding 5 and the memory use in finding 8 stay as documented limits.
- The PR needs a review in a fresh session before merging.
