FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# Installing requirements before copying the code keeps this layer cached
# when only application code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

# The API handles untrusted uploads, so it must not run as root.
RUN useradd --create-home changelens \
    && mkdir -p /data/storage /data/model-cache \
    && chown -R changelens /data/storage /data/model-cache
USER changelens

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
