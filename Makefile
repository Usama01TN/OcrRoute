.PHONY: install dev serve desktop test lint doctor docker clean
install:        ## Install with local engines and the desktop app
	pip install -e ".[local,desktop]"
dev:            ## Install everything needed for development
	pip install -e ".[local,desktop,dev]" && pre-commit install || true
serve:          ## Run API + control panel on http://127.0.0.1:20256
	ocrroute serve
desktop:        ## Run the PyQt5 app
	ocrroute desktop
test:           ## Run the test-suite (offscreen Qt)
	QT_QPA_PLATFORM=offscreen pytest -q
lint:           ## Lint the gateway (AioOCR is excluded: it is imported as given)
	ruff check ocrroute tests
doctor:         ## Print engine/database/port diagnostics
	ocrroute doctor
docker:         ## Build and run the container
	docker compose up --build
clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + ; rm -rf .pytest_cache .coverage dist build *.egg-info
help:
	@grep -E '^[a-z]+:.*##' Makefile | sed 's/:.*##/ -/'
