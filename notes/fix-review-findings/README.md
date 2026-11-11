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

## Security review findings (same branch, 2026-11-11)

A security review in a fresh session read `main` and reported 5 findings. All checked here.

| # | Finding | Check | Fix |
|---|---|---|---|
| 1 | `invoice.cmd` holding `REM %PDF-1.7` passed as a "Real document"; `.hta` too, via the PDF or FB2 marker | Reproduced. Cause: `%PDF-` may sit anywhere in the first 1 KiB, and the outer ending was only compared for ZIP entries. | The file's own ending is checked before its content: `RISKY_ENDINGS` and `.js` are refused as programs, `.html .htm .xhtml .mht .mhtml .svg` as web pages. `.js` and `.xhtml` are not in the ZIP entry list, because EPUBs hold them. |
| 2 | ZIP entry names and name parts reached the terminal unescaped | Reproduced (raw ESC in the "contains:" list). | `_report` runs `what`, `reason` and every warning through `safe()`. |
| 3 | A long name stopped the batch | Already fixed on this branch by the OSError catch. | The `.part` name no longer repeats the document's name, so a 246-character name now works. |
| 4 | No total time or CPU cap | Right; only the user's own machine, and Ctrl+C stops it. | Not fixed. Loose end. |
| 5 | Defence in depth: no check of the final PDF, runc not gVisor, `_ps_literal` missed curly quotes, missing endings | `_ps_literal`: a test showed `’` ended the string and ran a second command. Only fixed strings and the install path reach it, never a downloaded name. | `_ps_literal` doubles all five PowerShell quote marks. Added `.msc .appref-ms .settingcontent-ms .sct .wsc .scf .inf .cab` to `RISKY_ENDINGS`. gVisor and an output check stay loose ends. |

All new tests fail on 19cd855 (before these fixes) and pass after.

Loose ends from the security review:
- A total time cap per document, and `--cpus` on the containers.
- gVisor (`--runtime=runsc`) as a second wall around both stages.
- No check of the final PDF beyond `%PDF-`.

## Code review of the PR (round 2, 2026-11-11)

The fresh-session review of this branch: security clean; five correctness findings, all checked and fixed.

| Finding | Check | Fix |
|---|---|---|
| Rebuild out of memory showed "stopped early", not the memory message | Confirmed: that branch killed both stages before reading the exit code. | Read the exit code first (`_exit_code`), then kill, then `_failed`. Test: a fake rebuild that takes 3 GB. |
| `except OSError` called every OSError a write error | Confirmed. | "could not write the safe copy" only when `e.filename` is the `.part` or final file; otherwise the OS's own words. |
| Case-folded duplicate check: false skips on Linux, and casefold is not NTFS's rule (`ß`) | Confirmed. | Compare file ids (`st_dev`, `st_ino`) instead of names. Measured on `/mnt/c`: both spellings give one id, stable across renames. Tests on a Linux folder (both copies made) and a Windows folder (second skipped). |
| `--lang` checked only in the CLI | Confirmed. | `sandbox.check_language()`; `sanitize()` calls it before any container starts, and the CLI uses it for the early usage error. |
| Two error helpers; timeout message in three places | Confirmed. | `_explain` merged into `_failed(stage, what, code=None)`; the timeout message lives only in `check_failures()`. |
