# Right-click menu on Windows

From inside WSL, in the `pdf-sanitizer` folder:

```
./pdf-sanitizer install-menu
```

This adds two entries to the File Explorer right-click menu:

- **Open with → pdf-sanitizer** for one file. On Windows 11 it's on the menu itself. It appears for `.pdf`, `.epub`, `.mobi`, `.fb2` and `.cbz` files.
- **Send to → Sanitize document** for many files at once. On Windows 11 it's under **Show more options** (or hold Shift while you right-click). It appears for any file, and the file-type check refuses whatever isn't a document.

A window shows the progress, each safe copy appears next to its original, and Enter closes the window.

Your default PDF app doesn't change, so double-clicking a PDF opens it as before. If no other app on your PC opens an ebook type (for example, you have no EPUB reader), pdf-sanitizer becomes the only app listed for it, and double-clicking that file makes a safe copy.

To remove both entries:

```
./pdf-sanitizer remove-menu
```

## How it works

Open with is a per-user file type entry in the registry, under `HKEY_CURRENT_USER` only, so it needs no admin rights. File Explorer puts the chosen file's path in place of `"%1"` in its command. A Windows file name can't contain a double quote, so the path always stays one argument.

Send to is a shortcut in your Send to folder. File Explorer adds the chosen files' paths to the end of its command.

Both entries start `wsl.exe` directly, which runs pdf-sanitizer with `--from-windows` (the paths are Windows paths) and `--pause` (keep the window open). File names never pass through `cmd.exe` or a `.bat` file, so a crafted name such as `a&mkdir PWNED&.pdf` can't run a command. `tests/test_windows.py` launches both entries with names like that.

A "Sanitize document" item directly on the Windows 11 menu isn't possible this way. Windows 11 shows registry entries only under Show more options, and an item on the new menu needs a signed app package ([Microsoft docs](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/integrate-packaged-app-with-file-explorer)). Open with is already on the new menu, which is why it's the main entry.
