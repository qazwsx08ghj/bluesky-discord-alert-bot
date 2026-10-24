# Bluesky -> Discord Alert Bot: one image for both services (web and checker).
# See docs/DOCKER.md. Built by setup.bat / update.bat, or published as a prebuilt image.

# -- 1. Build the control panel ----------------------------------------------------------------
# Node.js is used only in this stage; the finished image contains just the built files.
# The output is the same on every platform, so it is built on the build machine's own one.
FROM --platform=$BUILDPLATFORM node:22-bookworm-slim AS panel
WORKDIR /panel
COPY frontend/package.json frontend/package-lock.json ./
# Exact versions from the lockfile; no install scripts run.
RUN npm ci --no-audit --no-fund --ignore-scripts
COPY frontend/ ./
# The third-party notices go into the built panel too (vite.config.ts reads ../THIRD_PARTY_NOTICES.txt).
COPY THIRD_PARTY_NOTICES.txt /THIRD_PARTY_NOTICES.txt
RUN npm run build && test -f dist/index.html && test -f dist/THIRD_PARTY_NOTICES.txt

# -- 2. Build wheels for the exact dependency versions ----------------------------------------
FROM python:3.11-slim-bookworm AS wheels
WORKDIR /build
COPY requirements-lock.txt requirements-docker.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements-docker.txt

# -- 3. The image that runs -----------------------------------------------------------------------
FROM python:3.11-slim-bookworm
ARG ALERTBOT_VERSION=dev
# 1 only for images built by the automated tests: allows their test-only overrides (a fake GitHub).
ARG ALERTBOT_TEST_BUILD=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ALERTBOT_VERSION=${ALERTBOT_VERSION}
LABEL org.opencontainers.image.title="Bluesky Discord Alert Bot" \
      org.opencontainers.image.version="${ALERTBOT_VERSION}" \
      org.opencontainers.image.licenses="MIT"

# The bot runs as an ordinary user. /data is the persistent volume (database, backups, key).
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin bot \
    && mkdir -p /data \
    && chown bot:bot /data

COPY --from=wheels /wheels /wheels
RUN pip install --no-cache-dir --no-index /wheels/* && rm -rf /wheels

WORKDIR /app
# Only the application code (with LICENSE and THIRD_PARTY_NOTICES.txt; the build fails if the notices
# don't match the packages installed above): .dockerignore keeps out .env, databases, backups, venv and node_modules.
COPY . /app
# The built control panel, served by Django (config/frontend.py).
COPY --from=panel /panel/dist /app/frontend/dist
# This version's Windows deployment files, with bundle.json (their SHA-256), for an updater to copy
# out with `docker cp`; see docs/RELEASES.md, "Inside the image". Owned by root: the bot can read
# them but not change them. The copies at the top of /app are removed: nothing there uses them.
RUN if [ "$ALERTBOT_TEST_BUILD" = "1" ]; then touch /app/.test-build; fi \
    && python docker/make_release_bundle.py --export /app/bundle/windows \
    && rm /app/setup.bat /app/start.bat /app/update.bat /app/stop.bat /app/backup.bat /app/restore.bat \
          /app/docker-compose.yml /app/docker/scripts/common.bat \
    && test -f /app/bundle/windows/bundle.json && test -f /app/bundle/windows/update.bat \
    && test ! -e /app/bundle/windows/docker/compose.env && test ! -e /app/docker/compose.env
RUN sed -i 's/\r$//' docker/entrypoint.sh \
    && chmod 755 docker/entrypoint.sh \
    && python -m compileall -q /app \
    && test ! -e /app/.env && test ! -e /app/db.sqlite3 && test ! -e /app/backups \
    && test -f /app/frontend/dist/index.html && test ! -e /app/frontend/node_modules \
    && cmp /app/THIRD_PARTY_NOTICES.txt /app/frontend/dist/THIRD_PARTY_NOTICES.txt \
    && python docker/third_party_notices.py --check --python-only

USER bot
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
