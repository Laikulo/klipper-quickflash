
PYTHON_INTERPRETER=/usr/bin/env python3

PY_FILES=$(wildcard kqf/**.py)
PY_TARGET_VER=3.7

.PHONY: all
all: pyz

.PHONY: pyz
pyz: kqf.pyz

kqf.pyz: $(PY_FILES)
	python -m zipapp kqf -p="${PYTHON_INTERPRETER}" -c -o kqf.pyz

.PHONY: dev
dev: lint pyz test


.PHONY: test
test: 
	echo "TODO: tests"

.PHONY: lint
lint: lint_flake8 lint_vermin

.PHONY: lint_vermin
test_vermin:
	vermin --violations --target="${PY_TARGET_VER}" kqf/



.PHONY: lint_flake8
lint_flake8:
	flake8 kqf/