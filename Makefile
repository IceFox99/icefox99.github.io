VENV := .venv
PIP := $(VENV)/bin/pip3
RENDERCV := $(VENV)/bin/rendercv
STAMP := $(VENV)/.installed

.PHONY: all render copy clean

all: copy

$(VENV)/bin/python:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

$(STAMP): $(VENV)/bin/python
	$(PIP) install 'rendercv==1.14'
	touch $(STAMP)

render: $(STAMP)
	$(RENDERCV) render cv/resume.yaml

copy: render
	cp rendercv_output/*.pdf cv/resume.pdf

clean:
	rm -rf $(VENV) rendercv_output
