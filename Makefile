.PHONY: help test test-e2e lint type docs check check-oo update-oo check-coupling update-coupling check-suppressions update-suppressions check-comments check-docstrings build-api-docs report format build install clean depot metrics coverage coverage-compare prfaq clean-tex zspec zspec-test

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

test: ## Run the default tier in parallel
	uv run pytest -n auto

.PHONY: test-push test-changed hooks
test-push: ## Run all local credential-free tiers in parallel
	uv run pytest -n auto -m "not integration and not sdk and not e2e and not roundtrip and not quarantine"

test-changed: ## Run default-tier tests importing staged Python modules (or REF)
	uv run python tools/test_changed.py $(if $(REF),--ref "$(REF)")

hooks: ## Install the repository's local quality gates
	git config core.hooksPath .githooks

.PHONY: test-playback-capture test-playback-capture-live
test-playback-capture: ## Real daemon to PulseAudio monitor to Whisper
	uv run --group roundtrip pytest tests/roundtrip/test_playback_capture.py -m "playback and not integration" -s

test-playback-capture-live: ## Credentialed daemon playback capture
	uv run --group roundtrip pytest tests/roundtrip/test_playback_capture.py -m "playback and integration" -s

.PHONY: test-roundtrip test-roundtrip-live test-agent
test-roundtrip: ## Offline real espeak-ng to CPU Whisper intelligibility check
	uv run --group roundtrip pytest tests/roundtrip -m "roundtrip and not integration and not agent and not quarantine" -s

test-roundtrip-live: ## Credentialed TTS to Scribe/Whisper matrix
	uv run --group roundtrip pytest tests/roundtrip -m "roundtrip and integration and not quarantine" -s

test-agent: ## Headless Claude Code to vox plugin to CPU Whisper
	uv run --group roundtrip pytest tests/roundtrip -m "agent and not quarantine" -s

test-e2e: ## Packaging e2e -- drive the INSTALLED `vox` binary and `vox mcp` server (run `make install` first)
	uv run pytest tests/e2e -v -m "e2e and not quarantine"

.PHONY: qa
qa: ## Record the installed release-candidate flight (VOX_QA_WHEEL=/path/to/wheel)
	bash scripts/release-qa.sh

lint: check-comments check-docstrings ## Lint and format check (Python + shell)
	uv run ruff check .
	uv run ruff format --check .
	shellcheck -x plugin/hooks/*.sh scripts/*.sh install.sh .githooks/pre-commit .githooks/pre-push
	bash scripts/check-plugin-surface.sh
	uv run python scripts/check-logging-sources.py

check-comments: ## Reject commented-out code and tracker references in Python prose
	uv run ruff check --select ERA001 .
	uv run python tools/check_python_docs.py comments src tests tools scripts

check-docstrings: ## Check public Python docstrings and documented argument names
	uv run ruff check --select D,D401 --ignore D105,D107,D417 src/
	uv run python tools/check_python_docs.py docstrings src/

build-api-docs: ## Build and verify the generated Python API reference
	uv run --group docs mkdocs build --strict
	uv run python tools/check_python_docs.py api .tmp/api-site/objects.inv

type: ## Type check with mypy and pyright
	uv run mypy src/ tests/
	uv run pyright src/ tests/

docs: ## Lint markdown files (matches CI docs job)
	# Keep this pin equal to the cli2 version bundled by the markdownlint-cli2-action
	# tag in .github/workflows/docs.yml. A lower pin here lets a rule the newer
	# linter enforces pass locally and fail in CI.
	npx --yes markdownlint-cli2@0.24.0 "**/*.md"

# Base-comparison flags injected by CI (e.g. --base-ref <merge-base> --require-base).
# Empty locally, where the tools default base to `git merge-base origin/main HEAD`.
OO_BASE ?=
COUPLING_BASE ?=
SUPPRESSION_BASE ?=

check: lint type docs build-api-docs test check-oo check-coupling check-suppressions ## Run all quality gates

check-oo: ## OO ratchet — must improve over baseline, never regress
	uv run oo-ratchet oo src/punt_vox/ --check $(OO_BASE)

update-oo: ## Update OO baseline after improvements (stage .oo-baseline.json and .oo-audit.jsonl)
	uv run oo-ratchet oo src/punt_vox/ --update $(OO_BASE)

check-coupling: ## Coupling ratchet — merge-base scoped, must not regress
	uv run oo-ratchet coupling src/punt_vox/ --check $(COUPLING_BASE)

update-coupling: ## Update coupling baseline (stage .oo-coupling-baseline.json and .oo-coupling-audit.jsonl)
	uv run oo-ratchet coupling src/punt_vox/ --update $(COUPLING_BASE)

check-suppressions: ## Suppression ratchet — base-commit scoped, count must not increase
	uv run oo-ratchet suppressions src/punt_vox/ --check $(SUPPRESSION_BASE)

update-suppressions: ## Update suppression baseline
	uv run oo-ratchet suppressions src/punt_vox/ --update

report: ## Full diagnostics (OO score + all checks, no fail-fast)
	-uv run oo-ratchet oo src/punt_vox/ --threshold
	-uv run mypy src/ tests/
	-uv run ruff format --check src/ tests/
	-uv run ruff check --preview --select PLR6301,PLR0913,UP035,UP040,UP007,N,I,SIM,PLC1901,S101 src/ tests/
	-uv run pyright src/ tests/
	-shellcheck -x plugin/hooks/*.sh scripts/*.sh install.sh
	-bash scripts/check-plugin-surface.sh
	-uv run pytest
	@echo "Report complete."

format: ## Auto-format code
	uv run ruff format .
	uv run ruff check --fix .

build: ## Build wheel and sdist
	rm -rf dist/
	uv build
	uvx twine check dist/*

install: build ## Build and install locally
	# The [call] extra (sounddevice, PortAudio) must ship in every standard
	# install -- a bare `uv tool install dist/*.whl` silently produces a
	# `vox` binary that crashes with ModuleNotFoundError the moment `vox
	# call start` runs. `dist/*.whl[call]` as one unquoted token does NOT
	# work: the shell's glob engine treats the trailing `[call]` as part of
	# the pathname pattern (a bracket expression), which matches no real
	# file and the whole thing fails to expand. The glob is expanded first,
	# in isolation, via command substitution; the extra is appended to the
	# already-expanded literal path.
	uv tool install --force "$$(echo dist/*.whl)[call]"

clean: ## Remove build artifacts
	rm -rf dist/ .tmp/

DEPOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))../.depot

depot: build ## Build and copy wheel to local depot
	@mkdir -p $(DEPOT)
	@cp dist/*.whl $(DEPOT)/
	@echo "depot: $$(ls dist/*.whl | xargs -n1 basename) -> $(DEPOT)/"

metrics: ## Run ABC complexity metrics on src/
	uv run python tools/run_metrics.py

coverage: install ## Measure line and branch coverage across all credential-free tiers
	uv run python tools/run_coverage.py

COVERAGE_BASE ?=

coverage-compare: ## Publish coverage; reject regressions when COVERAGE_BASE names a base JSON report
	uv run python tools/compare_coverage.py coverage.json $(if $(COVERAGE_BASE),--base "$(COVERAGE_BASE)")

# LaTeX intermediate files to remove after compilation
LATEX_ARTIFACTS = *.aux *.log *.out *.bbl *.bcf *.blg *.run.xml *.fls \
                  *.fdb_latexmk *.synctex.gz *.toc

TEX_FILES = prfaq.tex

prfaq: ## Compile .tex to .pdf and clean artifacts
	@for f in $(TEX_FILES); do \
	  echo "Compiling $$f ..."; \
	  dir=$$(dirname "$$f"); base=$$(basename "$$f" .tex); \
	  pdflatex -interaction=nonstopmode -output-directory="$$dir" "$$f" > /dev/null 2>&1; \
	  if [ -f "$$dir/$$base.bib" ] && command -v biber > /dev/null 2>&1; then \
	    (cd "$$dir" && biber "$$base") > /dev/null 2>&1 || true; \
	    pdflatex -interaction=nonstopmode -output-directory="$$dir" "$$f" > /dev/null 2>&1; \
	  fi; \
	  pdflatex -interaction=nonstopmode -output-directory="$$dir" "$$f" > /dev/null 2>&1; \
	  if [ -f "$$dir/$$base.pdf" ]; then \
	    echo "  $$dir/$$base.pdf"; \
	  else \
	    echo "Error: $$f failed to compile" >&2; exit 1; \
	  fi; \
	done
	@rm -f $(LATEX_ARTIFACTS)

clean-tex: ## Remove LaTeX intermediate files
	@rm -f $(LATEX_ARTIFACTS)

# Z specification targets
PROBCLI ?= $(HOME)/Applications/ProB/probcli
ZSPEC = docs/vox-notify.tex

zspec: ## Type-check and compile Z spec
	fuzz -t $(ZSPEC)
	cd docs && pdflatex -interaction=nonstopmode vox-notify.tex > vox-notify.log 2>&1
	cd docs && pdflatex -interaction=nonstopmode vox-notify.tex >> vox-notify.log 2>&1
	@if [ -f docs/vox-notify.pdf ]; then \
		echo "  docs/vox-notify.pdf"; \
	else \
		echo "Error: docs/vox-notify.pdf was not produced; see docs/vox-notify.log for details." >&2; \
		exit 1; \
	fi

zspec-test: zspec ## Animate and model-check Z spec with ProB
	@if [ ! -x "$$(command -v probcli 2>/dev/null || echo "$(PROBCLI)")" ] && [ ! -x "$(PROBCLI)" ]; then \
		echo "Error: probcli not found. Set PROBCLI or install ProB." >&2; \
		exit 1; \
	fi
	$(PROBCLI) $(ZSPEC) -init
	$(PROBCLI) $(ZSPEC) -animate 20
	$(PROBCLI) $(ZSPEC) -cbc_assertions
	$(PROBCLI) $(ZSPEC) -cbc_deadlock
	$(PROBCLI) $(ZSPEC) -model_check \
		-p DEFAULT_SETSIZE 2 \
		-p MAX_INITIALISATIONS 100 \
		-p MAX_OPERATIONS 1000 \
		-p TIME_OUT 30000
