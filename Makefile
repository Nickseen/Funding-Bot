.PHONY: help install test lint clean run

help:
	@echo "Available commands:"
	@echo "  make install    - Install dependencies"
	@echo "  make test       - Run tests"
	@echo "  make lint       - Run linters"
	@echo "  make clean      - Clean cache files"
	@echo "  make run        - Run the bot"

install:
	pip install -r requirements.txt

test:
	pytest

test-coverage:
	pytest --cov=src --cov-report=html --cov-report=term

lint:
	flake8 src tests
	mypy src

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .pytest_cache
	rm -rf htmlcov
	rm -rf .coverage

run:
	python -m src.main

run-testnet:
	BOT_MODE=testnet python -m src.main

run-mainnet:
	BOT_MODE=mainnet python -m src.main
