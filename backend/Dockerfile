FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip && \
    install_ok=0 && \
    for i in 1 2 3; do \
      if python -m pip install --no-cache-dir -r /app/requirements.txt; then \
        install_ok=1; \
        break; \
      fi; \
      echo "pip install failed, retry ${i}/3"; \
      sleep 5; \
    done && \
    test "${install_ok}" -eq 1 && \
    python -m pip show uvicorn fastapi >/dev/null

COPY backend /app/backend
COPY dictionary /app/dictionary
COPY ui /app/ui

EXPOSE 8000
