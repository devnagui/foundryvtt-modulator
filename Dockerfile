FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY resolver ./resolver
COPY backend ./backend
COPY frontend ./frontend
COPY README.md ./README.md

RUN pip install --no-cache-dir -r backend/requirements.txt

RUN useradd --create-home --shell /bin/bash appuser \
  && mkdir -p /app/reports /app/state /app/.cache \
  && chown -R appuser:appuser /app

USER appuser

EXPOSE 8787

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8787"]
