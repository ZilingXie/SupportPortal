# syntax=docker/dockerfile:1.7

ARG PYTHON_BASE_IMAGE=python:3.11-slim@sha256:d1e9ca7c4e78d1e8ecadb5d44bfc8e956e7a65b659a9950f569f243d72b326d0
FROM ${PYTHON_BASE_IMAGE}

ARG INSTALL_ML_DEPS=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.base.lock /app/requirements.base.lock
COPY requirements.full.lock /app/requirements.full.lock
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    requirements_file=/app/requirements.full.lock && \
    if [ "${INSTALL_ML_DEPS}" != "1" ]; then \
      requirements_file=/app/requirements.base.lock; \
    fi && \
    install_ok=0 && \
    for i in 1 2 3; do \
      if python -m pip install --require-hashes -r "${requirements_file}"; then \
        install_ok=1; \
        break; \
      fi; \
      echo "pip install failed, retry ${i}/3"; \
      sleep 5; \
    done && \
    test "${install_ok}" -eq 1 && \
    python -m pip show uvicorn fastapi >/dev/null

ARG APP_BUILD_REF=unknown
ARG APP_BUILD_TIME=
# Force layer rebuild when build args change so ENV reflects current provenance
RUN true "${APP_BUILD_REF}" "${APP_BUILD_TIME}"
ENV APP_BUILD_REF=${APP_BUILD_REF} \
    APP_BUILD_TIME=${APP_BUILD_TIME}

COPY backend /app/backend
COPY benchmarks /app/benchmarks
COPY docs /app/docs
COPY dictionary /app/dictionary
COPY ui /app/ui
COPY vendor/cusmem /app/vendor/cusmem

EXPOSE 8000
