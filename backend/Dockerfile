FROM python:3.11-slim

ARG INSTALL_ML_DEPS=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.base.txt /app/requirements.base.txt
COPY requirements.ml.txt /app/requirements.ml.txt
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip && \
    install_ok=0 && \
    for i in 1 2 3; do \
      if python -m pip install --no-cache-dir -r /app/requirements.base.txt; then \
        install_ok=1; \
        break; \
      fi; \
      echo "pip install failed, retry ${i}/3"; \
      sleep 5; \
    done && \
    test "${install_ok}" -eq 1 && \
    if [ "${INSTALL_ML_DEPS}" = "1" ]; then \
      install_ok=0; \
      for i in 1 2 3; do \
        if python -m pip install --no-cache-dir -r /app/requirements.ml.txt; then \
          install_ok=1; \
          break; \
        fi; \
        echo "pip install failed, retry ${i}/3"; \
        sleep 5; \
      done; \
      test "${install_ok}" -eq 1; \
    fi && \
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

EXPOSE 8000
