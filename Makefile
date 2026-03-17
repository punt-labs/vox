.PHONY: help test lint type check format build clean prfaq clean-tex zspec zspec-test

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

test: ## Run tests
	uv run pytest

lint: ## Lint and format check (Python + shell)
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/
	shellcheck -x hooks/*.sh scripts/*.sh install.sh

type: ## Type check with mypy and pyright
	uv run mypy src/ tests/
	uv run pyright src/ tests/

check: lint type test ## Run all quality gates

format: ## Auto-format code
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

build: ## Build wheel and sdist
	rm -rf dist/
	uv build
	uvx twine check dist/*

clean: ## Remove build artifacts
	rm -rf dist/ .tmp/

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
