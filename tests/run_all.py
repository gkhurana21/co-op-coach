"""Run every test suite, each in its own process (each one sets up its own temporary data folder before importing
coopcoach, so they can't share state), and print one line per suite plus the totals.

    .venv/bin/python -m tests.run_all            # all suites
    .venv/bin/python -m tests.run_all events     # only suites whose name contains "events"
    .venv/bin/python -m tests.run_all --cov      # also measure line coverage of coopcoach/ (needs `coverage`)
    .venv/bin/python -m tests.run_all --postgres # on Postgres: COOPCOACH_DATABASE_URL, or your Supabase project's
                                                 # scratch schema (COOPCOACH_PG_SETTINGS=data/database.json)

Exit code 1 if any suite fails. CI runs exactly this.
"""

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    import os
    cov, pg = "--cov" in sys.argv, "--postgres" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    env = dict(os.environ)
    if pg:  # each suite starts from an empty scratch schema, never the real one
        env.update(COOPCOACH_PG_SCHEMA=os.environ.get("COOPCOACH_PG_TEST_SCHEMA", "coopcoach_test"))
        if env.get("COOPCOACH_PG_SETTINGS"):  # the MCP server and CLIs some suites start run from other folders
            env["COOPCOACH_PG_SETTINGS"] = str(Path(env["COOPCOACH_PG_SETTINGS"]).resolve())
    if cov:
        subprocess.run([sys.executable, "-m", "coverage", "erase"], cwd=ROOT)
    suites = sorted(p.stem for p in (ROOT / "tests").glob("test_*.py") if not only or any(o in p.stem for o in only))
    passed = total = failed = skipped = 0
    started = time.time()
    for name in suites:
        t = time.time()
        run = ["-m", "coverage", "run", "-p", "--source=coopcoach", "-m"] if cov else ["-m"]
        r = subprocess.run([sys.executable, *run, f"tests.{name}"], cwd=ROOT, capture_output=True, text=True, timeout=900,
                           env={**env, "COOPCOACH_PG_RESET": "1"} if pg else env)
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
        if status == "FAIL":  # the failed checks first (a stray traceback on stderr mustn't hide them)
            fails = [ln for ln in r.stdout.splitlines() if ln.startswith("FAIL ")]
            print("\n".join("     " + line for line in (fails + ["     ..."] if fails else []) + out[-25:]))
    print(f"\n{'Postgres' if pg else 'SQLite'}: {len(suites) - failed - skipped} suites ok, {failed} failed, {skipped} skipped · {passed}/{total} checks "
          f"· {time.time() - started:.0f}s")
    if cov:
        subprocess.run([sys.executable, "-m", "coverage", "combine", "-q"], cwd=ROOT)
        rep = subprocess.run([sys.executable, "-m", "coverage", "report", "--format=total"], cwd=ROOT,
                             capture_output=True, text=True)
        print(f"line coverage of coopcoach/: {rep.stdout.strip()}%")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
