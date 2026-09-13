.PHONY: run dev test test-py test-js site lint fmt typecheck clean free-port

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

test: test-py test-js

test-py:
	./.venv/bin/pytest -q

# The published reader's logic runs in the browser; node's built-in runner, no deps.
test-js:
	node --test tests/js/*.test.mjs

# Static reader + catalog + bundles into ./site (open with: python -m http.server -d site)
site:
	./.venv/bin/python -m syllabus_studio.cli site build

lint:
	./.venv/bin/ruff check src tests

fmt:
	./.venv/bin/ruff format src tests && ./.venv/bin/ruff check --fix src tests

typecheck:
	./.venv/bin/mypy

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
