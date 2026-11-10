"""Make untrusted text safe to print in a terminal.

File names and sandbox error messages come from the outside world. They can
hold terminal escape codes that redraw the screen or fake output, and
direction controls that reverse part of a name. Print them only through
these functions.
"""


def safe(text):
    """Escape every character that is not plainly printable."""
    out = []
    for ch in text:
        if ch.isprintable():
            out.append(ch)
        else:
            out.append(ch.encode("unicode_escape").decode("ascii"))
    return "".join(out)


def safe_log(data, max_lines=12, max_line=300):
    """Turn raw sandbox stderr bytes into a few safe lines of text."""
    text = data.decode("utf-8", errors="replace")
    lines = [safe(line)[:max_line] for line in text.splitlines() if line.strip()]
    return lines[-max_lines:]
