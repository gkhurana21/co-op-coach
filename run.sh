#!/usr/bin/env bash
# One-command start: finds Python 3.10+, creates the virtualenv on first run, installs deps, launches the app.
set -euo pipefail
cd "$(dirname "$0")"

new_enough() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }

find_python() {
  # macOS ships Python 3.9 as /usr/bin/python3, which is too old, so prefer newer installs first
  for c in python3.14 python3.13 python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$c" >/dev/null 2>&1 && new_enough "$(command -v "$c")"; then
      command -v "$c"
      return 0
    fi
  done
  return 1
}

if [ ! -x .venv/bin/python ] || ! new_enough .venv/bin/python; then
  if ! PY=$(find_python); then
    echo "Co-op Coach needs Python 3.10 or newer (this Mac only has $(python3 --version 2>&1 || echo 'no Python'))."
    echo "Install it with Homebrew:   brew install python@3.12"
    echo "or from https://www.python.org/downloads/ , then run ./run.sh again."
    exit 1
  fi
  echo "Setting up with $("$PY" --version) (first run takes a couple of minutes)…"
  rm -rf .venv
  "$PY" -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
if [ ! -d "/Applications/Google Chrome.app" ] && ! command -v google-chrome >/dev/null 2>&1; then
  echo "Note: install Google Chrome to enable PDF export and Workday status checks."
fi
if [ "${1:-}" = "demo" ]; then  # ./run.sh demo: a fictional student and sample postings, separate from your data
  shift
  exec .venv/bin/python -m coopcoach demo "$@"
fi
exec .venv/bin/python -m coopcoach serve "$@"
