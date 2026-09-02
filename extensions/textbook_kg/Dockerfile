FROM python:3.11-slim

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PYTHONPATH=/opt/textbook-kg/vendor/concept_kg_module

WORKDIR /opt/textbook-kg

COPY vendor/concept_kg_module/requirements.txt /tmp/concept-requirements.txt
COPY vendor/concept_kg_module/ragflow_sidecar/requirements.txt /tmp/sidecar-requirements.txt
RUN python -m pip install --no-cache-dir \
      -r /tmp/concept-requirements.txt \
      -r /tmp/sidecar-requirements.txt \
    && useradd --create-home --uid 10001 textbookkg

COPY --chown=textbookkg:textbookkg vendor/ /opt/textbook-kg/vendor/

USER textbookkg

ENV TEXTBOOK_KG_HOST=127.0.0.1 \
    TEXTBOOK_KG_PORT=8890 \
    TEXTBOOK_KG_JOB_ROOT=/var/lib/textbook-kg \
    TEXTBOOK_KG_CONCEPT_ROOT=/opt/textbook-kg/vendor/concept_kg_module \
    TEXTBOOK_KG_MINERU_ROOT=/opt/textbook-kg/vendor/mineru_textbook_tree

EXPOSE 8890

HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8890/healthz', timeout=3)"

CMD ["python", "-m", "ragflow_sidecar"]
