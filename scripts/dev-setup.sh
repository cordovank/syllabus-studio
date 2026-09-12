#!/usr/bin/env bash
# First-run setup. Safe to re-run: it never overwrites an existing .env.
#
#   bash scripts/dev-setup.sh
#
# Creates the virtualenv, installs the package, and writes .env and the
# Makefile.

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
say() { printf '\033[1m▸\033[0m %s\n' "$*"; }

# --- python ----------------------------------------------------------------
PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'; then
  echo "Need Python 3.11+. Found: $("$PY" --version)" >&2
  echo "Install one (brew install python@3.12) or re-run with PYTHON=/path/to/python3.12" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  say "Creating .venv with $("$PY" --version)"
  "$PY" -m venv .venv
else
  say "Reusing the existing .venv"
fi

say "Installing the package and dev tools"
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -e ".[dev]"

# --- config ----------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  say "Wrote .env (offline provider; add ANTHROPIC_API_KEY to write real lessons)"
else
  say "Kept your existing .env"
fi

mkdir -p data

# --- Makefile --------------------------------------------------------------
if [ ! -f Makefile ]; then
cat > Makefile <<'MK'
.PHONY: run dev test lint fmt typecheck clean free-port

PORT := $(shell grep -m1 '^SS_PORT=' .env 2>/dev/null | cut -d= -f2)
PORT := $(if $(PORT),$(PORT),8000)

run: free-port
	./.venv/bin/python -m syllabus_studio

dev: free-port
	SS_RELOAD=true ./.venv/bin/python -m syllabus_studio

# A reload worker orphaned by a previous Ctrl+C can leave the port bound;
# clear it before starting so "Address already in use" doesn't recur.
free-port:
	@bash scripts/free-port.sh $(PORT)

test:
	./.venv/bin/pytest -q

lint:
	./.venv/bin/ruff check src tests

fmt:
	./.venv/bin/ruff format src tests && ./.venv/bin/ruff check --fix src tests

typecheck:
	./.venv/bin/mypy

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
MK
say "Wrote Makefile"
fi

# --- check -----------------------------------------------------------------
say "Running the test suite"
./.venv/bin/pytest -q

cat <<EOF

Done.

  Start it   ./.venv/bin/python -m syllabus_studio     (or: make dev)
  Open       http://127.0.0.1:8000
  API docs   http://127.0.0.1:8000/docs

Running on the offline provider. For real lessons, put your key in .env:
  ANTHROPIC_API_KEY=sk-ant-...
  SS_LLM_PROVIDER=anthropic
EOF
