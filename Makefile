.PHONY: install lint test

install:
	python3 -m pip install --quiet --upgrade pip
	python3 -m pip install --quiet -r requirements.txt

lint:
	bash runner.sh

test:
	@echo "no-op"
