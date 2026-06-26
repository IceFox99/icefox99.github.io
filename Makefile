VENV := .venv
# rendercv==1.14 pins PyMuPDF 1.24.10, which only ships prebuilt wheels up to
# Python 3.12. On 3.13/3.14 pip falls back to compiling MuPDF from source and
# fails, so pin the venv to 3.12 (install via: brew install python@3.12).
PYTHON := python3.12
PIP := $(VENV)/bin/pip3
RENDERCV := $(VENV)/bin/rendercv
STAMP := $(VENV)/.installed

.PHONY: all render copy clean

all: copy

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)
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
