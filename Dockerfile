FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY simlab ./simlab
COPY sample_data ./sample_data
COPY .env.example .env.example

RUN mkdir -p /data && useradd --create-home --uid 10001 simlab \
    && chown -R simlab:simlab /app /data
USER simlab

ENV PYTHONUNBUFFERED=1 \
    SIMLAB_DATABASE_URL=sqlite:////data/simlab.db \
    SIMLAB_DEV_EXPOSE_MAGIC_LINK=false \
    SIMLAB_COOKIE_SECURE=true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["uvicorn", "simlab.main:app", "--host", "0.0.0.0", "--port", "8000"]
