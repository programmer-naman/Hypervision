"""
ui.py
-----
Small terminal helpers: colours, headings and aligned text tables.
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if os.name == "nt":
    os.system("")   # enables ANSI colour codes in the Windows console

USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def paint(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if USE_COLOR else text


def bold(t):   return paint(t, "1")
def dim(t):    return paint(t, "2")
def red(t):    return paint(t, "31")
def green(t):  return paint(t, "32")
def yellow(t): return paint(t, "33")
def cyan(t):   return paint(t, "36")


def heading(title):
    print("\n" + bold(cyan("== %s " % title)) + dim("=" * max(2, 60 - len(title))))


def print_table(headers, rows, max_width=48):
    """Prints a simple aligned text table."""
    headers = [str(h) for h in headers]
    text_rows = []
    for row in rows:
        cells = []
        for cell in row:
            s = str(cell)
            if len(s) > max_width:
                s = s[:max_width - 3] + "..."
            cells.append(s)
        text_rows.append(cells)

    widths = [len(h) for h in headers]
    for cells in text_rows:
        for i, s in enumerate(cells):
            widths[i] = max(widths[i], len(s))

    print("  " + "  ".join(bold(h.ljust(widths[i])) for i, h in enumerate(headers)))
    print("  " + "  ".join("-" * w for w in widths))
    for cells in text_rows:
        print("  " + "  ".join(s.ljust(widths[i]) for i, s in enumerate(cells)))
