.PHONY: setup
setup:
	poetry install --no-dev

.PHONY: setup-dev
setup-dev:
	poetry install

.DEFAULT_GOAL := lint
.PHONY: lint
lint: setup-dev
	poetry run black bintray_cleanup/*.py
	poetry run isort bintray_cleanup/*.py
	poetry run flake8 bintray_cleanup/*.py
	poetry run mypy bintray_cleanup/*.py