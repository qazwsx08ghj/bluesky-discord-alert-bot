# Bluesky -> Discord Alert Bot: one image for both services (web and checker).
# See docs/DOCKER.md. Built by setup.bat / update.bat, or published as a prebuilt image.

# -- 1. Build wheels for the exact dependency versions ----------------------------------------
FROM python:3.11-slim-bookworm AS wheels
WORKDIR /build
COPY requirements-lock.txt requirements-docker.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements-docker.txt

# -- 2. The image that runs -----------------------------------------------------------------------
FROM python:3.11-slim-bookworm
ARG ALERTBOT_VERSION=dev
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ALERTBOT_VERSION=${ALERTBOT_VERSION}
LABEL org.opencontainers.image.title="Bluesky Discord Alert Bot" \
      org.opencontainers.image.version="${ALERTBOT_VERSION}"

# The bot runs as an ordinary user. /data is the persistent volume (database, backups, key).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin bot \
    && mkdir -p /data \
    && chown bot:bot /data

COPY --from=wheels /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/* && rm -rf /wheels

WORKDIR /app
# Only the application code: .dockerignore keeps out .env, databases, backups and venv.
COPY . /app
RUN sed -i 's/\r$//' docker/entrypoint.sh \
    && chmod 755 docker/entrypoint.sh \
    && python -m compileall -q /app \
    && test ! -e /app/.env && test ! -e /app/db.sqlite3 && test ! -e /app/backups

USER bot
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
