# Prefer the project venv unless one is already active, so `make` works without
# remembering to `source .venv/bin/activate` (the system python3 has no pyyaml).
PY ?= $(if $(VIRTUAL_ENV),python3,$(if $(wildcard .venv/bin/python),.venv/bin/python,python3))
RUN_ID ?= $(or $(shell ls -1t results/runs 2>/dev/null | grep -v smoke-test | head -1),smoke-test)

# .env is loaded by experiments/env.py, not by make. Make's `include` keeps the
# quotes in C4_MODELS="anthropic:... openai:...", which then reaches the runner
# as one model spec with a space in it instead of two specs.

.PHONY: help install ui smoke run catalogue analyze fill docx test clean

help:
	@echo "make install            install Python deps into the active environment"
	@echo "make ui                 open the control room: compose runs, manage miracles, read results"
	@echo "make smoke              key-free end-to-end check with scripted mock models"
	@echo "make run                run the full matrix with C4_MODELS from .env (override: MODELS=... TRIALS=... CONDITIONS=... SCENARIOS=... CONSCIENCE=...)"
	@echo "make catalogue          print the signal ladder, the conditions and the Conscience levels"
	@echo "make analyze RUN_ID=..  build summary.json + tables.md for a run (default: latest)"
	@echo "make fill    RUN_ID=..  write REPORT.filled.md from a run (default: latest)"
	@echo "make docx               render REPORT.docx (from REPORT.filled.md if present, else REPORT.md)"
	@echo "make test               run unit/smoke tests"

install:
	$(PY) -m pip install -r requirements.txt

ui:
	$(PY) -m experiments.ui

smoke:
	$(PY) -m experiments.run --models scripted:zealot scripted:obedient scripted:stubborn --trials 3 --concurrency 8 --run-id smoke-test
	$(PY) -m experiments.analyze --run-id smoke-test
	$(PY) -m experiments.fill_report --run-id smoke-test --out results/runs/smoke-test/REPORT.smoke-filled.md
	@echo "Smoke run complete. Filled example: results/runs/smoke-test/REPORT.smoke-filled.md (mock data, not evidence)"

run:
	$(PY) -m experiments.run $(if $(MODELS),--models $(MODELS)) $(if $(TRIALS),--trials $(TRIALS)) \
		$(if $(CONDITIONS),--conditions $(CONDITIONS)) $(if $(CONCURRENCY),--concurrency $(CONCURRENCY)) \
		$(if $(SCENARIOS),--scenarios $(SCENARIOS)) $(if $(CONSCIENCE),--conscience $(CONSCIENCE))

catalogue:
	$(PY) -m experiments.conditions

analyze:
	$(PY) -m experiments.analyze --run-id $(RUN_ID) --print

fill:
	$(PY) -m experiments.fill_report --run-id $(RUN_ID)

docx:
	bash scripts/build_docx.sh

test:
	$(PY) -m pytest -q tests

clean:
	rm -rf results/runs/smoke-test REPORT.filled.md REPORT.md.bak
