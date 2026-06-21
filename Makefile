.PHONY: build run run-arbitrage

build:
	pip install -e .

run:
	python src/main.py

run-arbitrage:
	HACKATHON_TARGET=yield_arbitrage python src/main.py
