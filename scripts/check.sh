#!/usr/bin/env bash
# Local mirror of CI: the same lint, type, and test gates GitHub runs.
# Creates a .venv on first run. Pass PYTHON=... to pick an interpreter.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

PY="${PYTHON:-python3}"

if [ ! -x .venv/bin/python ]; then
  echo "== creating .venv =="
  "$PY" -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install -e ".[dev]" >/dev/null
fi

echo "== ruff (lint) =="
./.venv/bin/ruff check .

echo "== ruff (format) =="
./.venv/bin/ruff format --check .

echo "== pyright =="
./.venv/bin/pyright

echo "== pytest =="
./.venv/bin/pytest

if command -v actionlint >/dev/null 2>&1; then
  echo "== actionlint =="
  actionlint
fi

if command -v shellcheck >/dev/null 2>&1; then
  echo "== shellcheck =="
  shellcheck scripts/*.sh packaging/*.sh
fi

echo "All checks passed."
