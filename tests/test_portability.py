"""Runs on Windows as well as Mac and Linux: no Mac/Linux-only date formats (%-d, %-I crash on Windows), every text
file read and written as UTF-8 (Windows would use cp1252 and choke on "é" or "·"), the Windows launchers keep Windows
line endings, and dates print the same everywhere.

Run: .venv/bin/python -m tests.test_portability
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    from coopcoach.textutil import long_date, when_text
    checks = {}
    src = {p: p.read_text(encoding="utf-8") for p in sorted((ROOT / "coopcoach").rglob("*.py"))}
    posix_dates = [f"{p.relative_to(ROOT)}:{i}" for p, s in src.items() for i, line in enumerate(s.splitlines(), 1)
                   if re.search(r"strftime\([^)]*%-[a-zA-Z]", line)]
    checks["no %-d / %-I date formats (they crash on Windows)"] = not posix_dates
    no_enc = [f"{p.relative_to(ROOT)}:{i}" for p, s in src.items() for i, line in enumerate(s.splitlines(), 1)
              if re.search(r"\.read_text\(\s*\)", line)]
    checks["every read_text() says UTF-8"] = not no_enc
    kills = [f"{p.relative_to(ROOT)}:{i}" for p, s in src.items() for i, line in enumerate(s.splitlines(), 1)
             if re.search(r"os\.kill\(", line) and "nt" not in s.splitlines()[i - 2]]
    checks["os.kill never runs on Windows (signal 0 there is Ctrl-C)"] = not kills
    bare_open = [f"{p.relative_to(ROOT)}:{i}" for p, s in src.items() for i, line in enumerate(s.splitlines(), 1)
                 if re.search(r"(?<![\w.])open\([^)]*\)", line) and "encoding" not in line
                 and not re.search(r"""["'](?:r|w|a)?b["']|webbrowser|gzip\.open|urlopen|def open""", line)]
    checks["every text open() says its encoding"] = not bare_open
    for bat in ("run.bat", "run-demo.bat"):
        raw = (ROOT / bat).read_bytes()
        checks[f"{bat} has Windows line endings"] = raw.count(b"\r\n") == raw.count(b"\n") > 0
    checks["run.bat sets UTF-8 and opens the browser when ready"] = b"PYTHONUTF8=1" in (ROOT / "run.bat").read_bytes() \
        and b"--open" in (ROOT / "run.bat").read_bytes()
    import datetime
    import time
    ts = time.mktime((2026, 10, 14, 14, 5, 0, 0, 0, -1))
    checks["dates print the same on every computer"] = when_text(ts) == "Wed Oct 14, 2:05 PM" and \
        long_date(datetime.date(2026, 10, 4)) == "October 4, 2026"

    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    for label, found in (("Mac-only date formats", posix_dates), ("read_text() without encoding", no_enc),
                         ("open() without encoding", bare_open)):
        if found:
            print(f"  {label}: {', '.join(found[:8])}")
    print(f"\n{sum(map(bool, checks.values()))}/{len(checks)} passed")
    raise SystemExit(0 if all(map(bool, checks.values())) else 1)


if __name__ == "__main__":
    main()
