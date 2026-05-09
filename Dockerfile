FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY resolver ./resolver
COPY service ./service
COPY README.md ./README.md

RUN useradd --create-home --shell /bin/bash appuser \
  && mkdir -p /app/reports /app/state /app/.cache \
  && chown -R appuser:appuser /app

USER appuser

EXPOSE 8787

CMD ["python", "-m", "service.server"]
