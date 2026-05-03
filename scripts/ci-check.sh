#!/usr/bin/env bash
# Mirrors the CI quality job exactly. Run before every commit.
set -euo pipefail

echo "==> ruff format"
python -m ruff format --check .

echo "==> ruff lint"
python -m ruff check .

echo "==> pytest"
python -m pytest

echo "==> mypy"
mapfile -t modules < <(find . -name "*.py" \
  ! -path "./.venv/*" ! -path "./.git/*" ! -path "./__pycache__/*" \
  ! -name "scraper.py" \
  ! -name "TelegramChannelSummarizer.py" \
  ! -name "crypto.py")

if [ "${#modules[@]}" -eq 0 ]; then
  echo "No modules to type-check; skipping."
else
  python -m mypy "${modules[@]}"
fi

echo "==> all checks passed"
