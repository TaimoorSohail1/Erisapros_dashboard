FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install --target=/opt/python -r requirements.txt

FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/python \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY --from=builder /opt/python /opt/python
RUN python -m playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 app \
    && chmod -R a+rX /ms-playwright

WORKDIR /app

COPY app ./app
RUN chown -R app:app /app

USER app

EXPOSE 8001

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
