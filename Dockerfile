FROM python:3.13-slim

WORKDIR /app
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY src/ /app/src/

ENTRYPOINT ["python", "-m", "lupita"]
