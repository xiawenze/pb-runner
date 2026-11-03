.PHONY: install lint test

install:
	python3 -m pip install --quiet --upgrade pip
	python3 -m pip install --quiet -r requirements.txt

lint:
	RUN_MINUTES=330 python3 -u runner.py

test:
	@echo "no-op"
