# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

.PHONY: setup lint format typecheck test clean

# Activate git hooks and install development dependencies.
setup:
	git config --local core.hooksPath .githooks
	pip install -e ".[dev]"

# Check code formatting with black.
lint:
	black --check src/ tests/

# Fix code formatting with black.
format:
	black src/ tests/

# Run strict type checking with basedpyright.
typecheck:
	basedpyright src/ tests/

# Run the test suite.
test:
	python -m pytest tests/ -v

# Remove Python bytecode caches.
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache
