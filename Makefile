PYTHON ?= python3

.PHONY: check test validate list audit-articles

check: test validate
	git diff --check

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v

validate:
	$(PYTHON) scripts/research.py check --strict

list:
	$(PYTHON) scripts/research.py list

audit-articles:
	$(PYTHON) scripts/article_library.py audit
