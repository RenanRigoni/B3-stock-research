.PHONY: help install test test-all lint fmt typecheck check doctor init status clean

VENV := .venv/Scripts
PY   := $(VENV)/python.exe
CLI  := $(VENV)/stock-research.exe

help:
	@echo "install    instala o projeto e as dependencias de desenvolvimento"
	@echo "doctor     diagnostica configuracao e conexao"
	@echo "init       cria data/ e carrega o universo de instrumentos"
	@echo "status     cobertura de dados por instrumento"
	@echo "test       suite offline (sem rede)"
	@echo "test-all   inclui os testes de integracao (bate nas fontes reais)"
	@echo "lint       ruff"
	@echo "fmt        ruff format + autofix"
	@echo "typecheck  mypy"
	@echo "check      lint + typecheck + test"

install:
	uv venv --python 3.12
	uv pip install -e ".[dev]"

doctor:
	$(CLI) doctor

init:
	$(CLI) init

status:
	$(CLI) status

test:
	$(VENV)/pytest.exe -q

test-all:
	$(VENV)/pytest.exe -q -m "integration or not integration"

lint:
	$(VENV)/ruff.exe check .

fmt:
	$(VENV)/ruff.exe format .
	$(VENV)/ruff.exe check --fix .

typecheck:
	$(VENV)/mypy.exe src

check: lint typecheck test

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
