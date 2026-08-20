# Every target here also runs in CI. If it passes locally it passes there,
# and the deploy gate runs the same `validate` this does.

SHELL := /bin/bash
GO    ?= go
# Dependencies are vendored so the control plane builds without network
# access — a deploy tool that needs a module proxy is a deploy tool that
# fails during an incident.
export GOFLAGS = -mod=vendor
PY    ?= python3

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: deps
deps: ## Install Python dependencies for tooling and services
	$(PY) -m pip install --quiet pyyaml jsonschema pytest ruff -r services/requirements.txt

.PHONY: lint
lint: lint-go lint-py ## Lint both planes

.PHONY: lint-go
lint-go: ## golangci-lint + canonical formatting
	@command -v golangci-lint >/dev/null || { \
	  echo "golangci-lint not installed: https://golangci-lint.run/welcome/install/"; exit 1; }
	golangci-lint run ./...
	@test -z "$$(gofmt -l cmd internal)" || { echo "gofmt -w these:"; gofmt -l cmd internal; exit 1; }

.PHONY: lint-py
lint-py: ## ruff over services and tools
	$(PY) -m ruff check services tools

.PHONY: validate
validate: ## Schema + fleet invariants (the first gate of every deploy)
	$(PY) tools/validate.py

.PHONY: build
build: ## Build the alertctl binary into bin/
	$(GO) build -o bin/alertctl ./cmd/alertctl

.PHONY: test
test: test-go test-py ## Run all tests

.PHONY: test-go
test-go: ## Control-plane tests
	$(GO) test ./...

.PHONY: test-py
test-py: build ## Service and cross-language contract tests
	$(PY) -m pytest services/tests -q

.PHONY: observability
observability: ## Regenerate Prometheus and Grafana config from the specs
	$(PY) tools/gen_observability.py

.PHONY: check
check: lint validate test ## Everything CI runs
	$(PY) tools/gen_observability.py --check

.PHONY: plan
plan: build validate ## Plan against the production target
	./bin/alertctl plan -out plan.json

.PHONY: console
console: build ## Read-only operator console on http://127.0.0.1:8600
	./bin/alertctl serve

.PHONY: status
status: build ## What is deployed right now
	./bin/alertctl status

.PHONY: drift
drift: build ## Non-zero exit if the host has drifted from desired state
	./bin/alertctl drift

.PHONY: clean
clean:
	rm -rf bin/ .local-state/ plan.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
