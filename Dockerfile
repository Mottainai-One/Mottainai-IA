FROM python:3.13.0-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY requirements.txt ./
RUN pip install --requirement requirements.txt

COPY --chown=appuser:appuser . .
USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "interfaces.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
