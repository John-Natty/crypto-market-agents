.DEFAULT_GOAL := help

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)
PYTHON3 ?= python3
PROJECT_DIRS := src tests scripts
IMAGE_NAME := crypto-market-agents
COVERAGE_MIN := 80

.PHONY: help install test lint format format-check coverage ci-local docker-build mock orchestrator-mock clean

help:
	@printf "Commandes disponibles:\n"
	@printf "  make install           Installer le projet et les outils de dev\n"
	@printf "  make test              Compiler puis lancer les tests unitaires\n"
	@printf "  make lint              Lancer Ruff check\n"
	@printf "  make format            Formater le code Python avec Ruff\n"
	@printf "  make format-check      Verifier le format sans modifier les fichiers\n"
	@printf "  make coverage          Lancer les tests avec Coverage et seuil %s%%\n" "$(COVERAGE_MIN)"
	@printf "  make ci-local          Rejouer localement les controles principaux de la CI\n"
	@printf "  make docker-build      Construire l'image Docker locale\n"
	@printf "  make mock              Lancer le pipeline mocke sans API externe\n"
	@printf "  make orchestrator-mock Lancer l'orchestrateur mocke sans API externe\n"
	@printf "  make clean             Supprimer les caches et artefacts locaux\n"

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

test:
	$(PYTHON3) -m compileall $(PROJECT_DIRS)
	$(PYTHON) -m unittest discover -s tests

lint:
	$(PYTHON) -m ruff check $(PROJECT_DIRS)

format:
	$(PYTHON) -m ruff format $(PROJECT_DIRS)

format-check:
	$(PYTHON) -m ruff format --check $(PROJECT_DIRS)

coverage:
	$(PYTHON) -m coverage run -m unittest discover -s tests
	$(PYTHON) -m coverage report --fail-under=$(COVERAGE_MIN)

ci-local:
	$(PYTHON3) -m compileall $(PROJECT_DIRS)
	$(PYTHON) -m ruff check $(PROJECT_DIRS)
	$(PYTHON) -m ruff format --check $(PROJECT_DIRS)
	$(PYTHON) -m unittest discover -s tests
	$(PYTHON) -m coverage run -m unittest discover -s tests
	$(PYTHON) -m coverage report --fail-under=$(COVERAGE_MIN)
	$(PYTHON3) scripts/test_full_pipeline_mock.py
	$(PYTHON3) scripts/test_orchestrator_mock.py

docker-build:
	docker build -t $(IMAGE_NAME) .

mock:
	$(PYTHON3) scripts/test_full_pipeline_mock.py

orchestrator-mock:
	$(PYTHON3) scripts/test_orchestrator_mock.py

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -maxdepth 3 -type d -name "*.egg-info" -prune -exec rm -rf {} +
	rm -rf .coverage htmlcov .pytest_cache .ruff_cache build dist
