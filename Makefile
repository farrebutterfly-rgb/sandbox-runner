PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python

.PHONY: install demo test verify live lint nodash check clean

$(PY):
	$(PYTHON) -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
	$(PYTHON) -m venv $(VENV)

install: $(PY)   ## .venv with the standard library only (nothing to download), then probe the backends
	$(PY) -m sandbox_runner.cli.probe

demo: $(PY)      ## fixture demo, scripted model, real sandbox, no API key, no cost
	$(PY) -m sandbox_runner.cli.demo

test: $(PY)      ## every control against every backend that works here
	$(PY) -m unittest discover -s tests -t . -v

verify: $(PY)    ## control checks for the selected backend, written to evidence/
	$(PY) -m sandbox_runner.cli.verify

live: $(PY)      ## real model run, needs ANTHROPIC_API_KEY, costs money
	$(PY) -m sandbox_runner.cli.live

lint: $(PY)      ## byte-compile everything (catches syntax errors on the target Python)
	$(PY) -W error -m compileall -q sandbox_runner tests data

nodash:          ## no em or en dashes anywhere
	! LC_ALL=C.UTF-8 grep -rnP --exclude-dir=.venv --exclude-dir=.git '\x{2014}|\x{2013}' .

check: lint nodash test demo

clean:
	rm -rf $(VENV) __pycache__ */__pycache__ */*/__pycache__
