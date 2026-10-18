FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt requirements-prod.txt ./
RUN grep -v '^pytest' requirements.txt > r.txt && pip install --no-cache-dir -r r.txt \
    && grep -v '^locust' requirements-prod.txt > rp.txt && pip install --no-cache-dir -r rp.txt
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY static ./static
RUN useradd -r -u 1001 appuser
USER appuser
EXPOSE 8000
# migrations on boot, then several workers (API is stateless: JWT + DB)
CMD ["sh", "-c", "alembic upgrade head && exec gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-4} -b 0.0.0.0:8000 --forwarded-allow-ips='*' --access-logfile -"]
