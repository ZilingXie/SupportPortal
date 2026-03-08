FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip && \
    for i in 1 2 3; do \
      pip install --no-cache-dir -r /app/requirements.txt && break; \
      echo "pip install failed, retry ${i}/3"; \
      sleep 5; \
    done

COPY backend /app/backend
COPY client_ui /app/client_ui
COPY engineer_ui /app/engineer_ui
COPY dashboard /app/dashboard

EXPOSE 8000
