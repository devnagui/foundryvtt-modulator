FROM node:20-alpine AS ui-build

WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY resolver ./resolver
COPY backend ./backend
COPY frontend ./frontend
COPY --from=ui-build /ui/dist ./frontend/dist
COPY README.md ./README.md

RUN pip install --no-cache-dir -r backend/requirements.txt

RUN useradd --create-home --shell /bin/bash appuser \
  && mkdir -p /app/reports /app/state /app/.cache \
  && chown -R appuser:appuser /app

USER appuser

EXPOSE 8787

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8787"]
