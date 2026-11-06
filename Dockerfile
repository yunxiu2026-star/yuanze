FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements-production.txt && useradd -u 10001 --create-home app
COPY . .
RUN python scripts/build_site.py && mkdir -p /data/api /data/geo /app/.local && chown -R app:app /data /app
USER app
CMD ["python","scripts/run_service.py","site"]
