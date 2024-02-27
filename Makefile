
PYTHON_INTERPRETER=/usr/bin/env python3

PY_FILES=$(wildcard kqf/**.py)
PY_TARGET_VER=3.7
PY_TARGET_VER_NO_DOTS=$(subst .,,${PY_TARGET_VER})

.PHONY: all
all: pyz

.PHONY: pyz
pyz: kqf.pyz

kqf.pyz: $(PY_FILES) build/kqf/
	python -m zipapp build -p="${PYTHON_INTERPRETER}" -c -o kqf.pyz -m "kqf.entrypoint:entrypoint"

.PHONY: dev
dev: lint pyz test


.PHONY: test
test: 
	echo "TODO: tests"

.PHONY: lint
lint: lint_flake8 lint_vermin lint_vermin_ep lint_compile

.PHONY: lint_vermin
lint_vermin:
	vermin --quiet --violations --target="${PY_TARGET_VER}" kqf/

.PHONY: lint_vermin_ep
lint_vermin_ep:
	vermin --quiet --violations --target=2.0 kqf/entrypoint.py
	vermin --quiet --violations --target=3.0 kqf/entrypoint.py

.PHONY: lint_flake8
lint_flake8:
	flake8 kqf/

.PHONY: lint_compile
lint_compile:
	python -m compileall -q kqf

.PHONY: format
format: format_black

.PHONY: format_black
format_black:
	black --target-version "py${PY_TARGET_VER_NO_DOTS}" kqf/

.PHONY: clean
clean:
	rm -rf build/
	rm -f kqf.pyz

.IGNORE: build/
build/:
	[[ ! -d build ]] && mkdir build

.IGNORE: build/kqf/
build/kqf/: kqf/ build/
	rm -rf build/kqf
	cp -rp kqf build/kqf
