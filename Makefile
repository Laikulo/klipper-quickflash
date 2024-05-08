
PYTHON_INTERPRETER=/usr/bin/env python3

PY_FILES=$(wildcard kqf/**.py)
PY_TARGET_VER=3.7
PY_TARGET_VER_NO_DOTS=$(subst .,,${PY_TARGET_VER})

.PHONY: all
all: pyz whl


.PHONY: whl
whl: $(PY_FILES) pyproject.toml
	pyproject-build

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

.PHONY: generate
generate: kqf/version_gen.py

kqf/version_gen.py: KQF_PACK_VERSION=0.0.0-alpha
kqf/version_gen.py: KQF_PACK_GITHASH=$(shell git rev-parse --short HEAD)
kqf/version_gen.py: KQF_PACK_DATE=$(shell date +%Y-%m-%d)
kqf/version_gen.py: SHELL=/usr/bin/bash
kqf/version_gen.py: .FORCE
	echo -e "KQF_PACK_VERSION = '$(KQF_PACK_VERSION)'\nKQF_PACK_GITHASH = '$(KQF_PACK_GITHASH)'\nKQF_PACK_DATE = '$(KQF_PACK_DATE)'" > kqf/version_gen.py

#.IGNORE: build/
build/:
	[ ! -d build ] && mkdir build

#.IGNORE: build/kqf/
build/kqf/: kqf/ build/ generate
	rm -rf build/kqf
	cp -rp kqf build/kqf
	cp COPYING build/kqf/GPL3.txt

.PHONY: .FORCE

.FORCE:
