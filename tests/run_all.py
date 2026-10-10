"""Run every test suite, each in its own process (each one sets up its own temporary data folder before importing
coopcoach, so they can't share state), and print one line per suite plus the totals.

    .venv/bin/python -m tests.run_all            # all suites
    .venv/bin/python -m tests.run_all events     # only suites whose name contains "events"

Exit code 1 if any suite fails. CI runs exactly this.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    only = sys.argv[1:]
    suites = sorted(p.stem for p in (ROOT / "tests").glob("test_*.py") if not only or any(o in p.stem for o in only))
    passed = total = failed = skipped = 0
    started = time.time()
    for name in suites:
        t = time.time()
        r = subprocess.run([sys.executable, "-m", f"tests.{name}"], cwd=ROOT, capture_output=True, text=True, timeout=900)
        out = (r.stdout + r.stderr).strip().splitlines()
        found = re.findall(r"(\d+)/(\d+) passed", r.stdout)  # the summary line (warnings go to stderr)
        m = re.match(r"(\d+)/(\d+)", "/".join(found[-1])) if found else None
        tail = next((ln for ln in reversed(r.stdout.strip().splitlines()) if ln.strip()), out[-1] if out else "")
        if tail.startswith("SKIP"):
            skipped += 1
            status = "skip"
        elif r.returncode == 0:
            status = "ok"
        else:
            failed += 1
            status = "FAIL"
        if m:
            passed, total = passed + int(m.group(1)), total + int(m.group(2))
        print(f"{status:4} {name:30} {f'{m.group(1)}/{m.group(2)} passed' if m and status == 'ok' else tail[:80]:40} "
              f"{time.time() - t:5.1f}s")
        if status == "FAIL":
            print("\n".join("     " + line for line in out[-25:]))
    print(f"\n{len(suites) - failed - skipped} suites ok, {failed} failed, {skipped} skipped · {passed}/{total} checks "
          f"· {time.time() - started:.0f}s")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
