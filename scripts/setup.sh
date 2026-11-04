#!/usr/bin/env bash
# Creates .venv, installs talent-agent with the JobSpy extra, and seeds .env.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv
  else
    python3 -m venv .venv
  fi
fi

if command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python -e '.[jobspy]'
else
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e '.[jobspy]'
fi

[ -f .env ] || cp .env.example .env

echo "Done. Activate with: source .venv/bin/activate"
echo "Then set OPENROUTER_API_KEY in .env and run: talent-agent run --search"
