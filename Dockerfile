# syntax=docker/dockerfile:1
FROM python:3.11.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

COPY config ./config

RUN useradd --create-home --uid 10001 iris && mkdir -p /app/data && chown -R iris /app/data
USER iris

ENTRYPOINT ["iris-peering"]
CMD ["--config", "config/config.example.yaml", "run", "--strict"]

# ---------------------------------------------------------------------------
FROM runtime AS test
USER root
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
RUN chown -R iris /app
USER iris
ENTRYPOINT ["python", "-m", "pytest"]
CMD ["-q"]
