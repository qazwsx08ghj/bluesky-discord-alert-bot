import os
import sys
from pathlib import Path
from dotenv import dotenv_values, load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _secret_key():
    """SECRET_KEY from the environment, else from SECRET_KEY_FILE (Docker: generated once per
    install by `manage.py prepare_data` and kept in the data volume)."""
    if os.getenv("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.getenv("SECRET_KEY_FILE")
    if path and Path(path).is_file():
        key = Path(path).read_text(encoding="utf-8").strip()
        if key:
            return key
    return "change-me-in-production"


SECRET_KEY = _secret_key()
# Shown by /api/health/; set when the Docker image is built (ALERTBOT_VERSION).
APP_VERSION = os.getenv("ALERTBOT_VERSION", "dev")
# Set by docker/entrypoint.sh when this version must not use the data ("newer_data": a newer
# version updated it). The web service then only shows a warning page (config/compat_middleware.py).
ALERTBOT_BLOCKED = os.getenv("ALERTBOT_BLOCKED", "")
DEBUG = os.getenv("DEBUG", "False") == "True"
# Local only: the panel has no login, so it answers only to this computer's own names.
# This also blocks DNS-rebinding attacks. See config/security.py and docs/API.md.
ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if h.strip()]

# The Django admin (/admin/) was removed because nothing used it. Its tables stay in the
# database untouched; nothing reads them.
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "botconfig",
    "bluesky_monitor",
    "discord_alerts",
    "activity",
]

MIDDLEWARE = [
    "config.compat_middleware.BlockedMiddleware",  # first: a blocked install answers nothing else
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "config.security.RequestGuardMiddleware",  # replaces CsrfViewMiddleware; also covers the API
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.security.ApiHeadersMiddleware",
]

# CSRF token cookie (read by the control panel's JavaScript, so not HttpOnly).
CSRF_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_HTTPONLY = False
# Extra origins allowed to make changes, e.g. the React dev server: http://localhost:5173
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
# Error pages and reports hide webhook URLs and tokens too.
DEFAULT_EXCEPTION_REPORTER_FILTER = "config.security.SafeReporterFilter"
# config.security.RequestGuardMiddleware replaces (and extends) CsrfViewMiddleware, so Django's
# "CSRF middleware missing" deployment warning doesn't apply.
SILENCED_SYSTEM_CHECKS = ["security.W003"]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.getenv("DATABASE_PATH") or BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 20},
    }
}
# Where `manage.py backup_db` puts copies (Docker: /data/backups, inside the data volume).
BACKUP_DIR = Path(os.getenv("BACKUP_DIR") or BASE_DIR / "backups")

# Docker runs several web workers; a file cache lets them share the profile lookup cache and
# the login test limit. Without CACHE_DIR (and in tests) each process has its own memory cache.
if os.getenv("CACHE_DIR"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
                          "LOCATION": os.environ["CACHE_DIR"]}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# The built React control panel (config/frontend.py). Docker builds it into the image.
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST") or BASE_DIR / "frontend" / "dist")
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    # JSON in, JSON out. No browsable HTML pages, no form posts, no login backends
    # (the panel is local-only; cross-site requests are stopped by config.security).
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "EXCEPTION_HANDLER": "config.security.api_exception_handler",
}


_RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == "test"

# Bot settings live in the database (botconfig/store.py). These .env values
# are read only once, to fill the database the first time. Edits here are ignored after that.
# Tests never see the real values; they set their own.
# LEGACY_ENV_FILE: read them from that file instead (Docker setup: the old install's .env,
# mounted read-only, so its settings can be brought over once).
_LEGACY_FILE = os.getenv("LEGACY_ENV_FILE", "")
_LEGACY_VALUES = dotenv_values(_LEGACY_FILE) if _LEGACY_FILE and Path(_LEGACY_FILE).is_file() else None


def _legacy(key, default=""):
    if _RUNNING_TESTS:
        return default
    if _LEGACY_FILE:
        return (_LEGACY_VALUES or {}).get(key) or default
    return os.getenv(key, default)


BLUESKY_HANDLE = _legacy("BLUESKY_HANDLE")
BLUESKY_PASSWORD = _legacy("BLUESKY_PASSWORD")
DISCORD_WEBHOOK_URL = _legacy("DISCORD_WEBHOOK_URL")
POLL_INTERVAL_MINUTES = _legacy("POLL_INTERVAL_MINUTES", "5")

# Checker coordination (see bluesky_monitor/locks.py and poller.py)
SCHEDULER_LEASE_SECONDS = int(os.getenv("SCHEDULER_LEASE_SECONDS", "90"))
SCHEDULER_HEARTBEAT_SECONDS = int(os.getenv("SCHEDULER_HEARTBEAT_SECONDS", "30"))
ACCOUNT_LOCK_SECONDS = int(os.getenv("ACCOUNT_LOCK_SECONDS", "300"))
SEND_STALE_SECONDS = int(os.getenv("SEND_STALE_SECONDS", "120"))
SEND_MAX_ATTEMPTS = int(os.getenv("SEND_MAX_ATTEMPTS", "4"))  # first try + 3 retries
SEND_RETRY_WINDOW_HOURS = int(os.getenv("SEND_RETRY_WINDOW_HOURS", "24"))
# Activity log (see activity/retention.py): keep 30 days, never more than 10,000 events.
ACTIVITY_RETENTION_DAYS = int(os.getenv("ACTIVITY_RETENTION_DAYS", "30"))
ACTIVITY_MAX_EVENTS = int(os.getenv("ACTIVITY_MAX_EVENTS", "10000"))
# Post history (bluesky_monitor/retention.py): finished post records kept per account.
SEEN_POSTS_KEPT_PER_ACCOUNT = int(os.getenv("SEEN_POSTS_KEPT_PER_ACCOUNT", "1000"))
# /api/status/: a check running longer than this counts as stuck; a check later than
# this past its planned time counts as overdue.
SCHEDULER_STALL_MINUTES = int(os.getenv("SCHEDULER_STALL_MINUTES", "10"))
SCHEDULER_OVERDUE_GRACE_SECONDS = int(os.getenv("SCHEDULER_OVERDUE_GRACE_SECONDS", "120"))
# A standalone checker (`run_scheduler`, the Docker checker service) exits if its own
# heartbeat hasn't run for this long, so Docker restarts it. 0 turns the watchdog off.
SCHEDULER_WATCHDOG_SECONDS = int(os.getenv("SCHEDULER_WATCHDOG_SECONDS", "300"))

# Profile lookup, login test, "Check all"
BLUESKY_PUBLIC_API_URL = os.getenv("BLUESKY_PUBLIC_API_URL", "https://public.api.bsky.app")  # read-only, no login
BLUESKY_REQUEST_TIMEOUT_SECONDS = int(os.getenv("BLUESKY_REQUEST_TIMEOUT_SECONDS", "10"))
PROFILE_LOOKUP_CACHE_SECONDS = int(os.getenv("PROFILE_LOOKUP_CACHE_SECONDS", "60"))
LOGIN_TEST_LIMIT = int(os.getenv("LOGIN_TEST_LIMIT", "5"))  # tests per window
LOGIN_TEST_WINDOW_SECONDS = int(os.getenv("LOGIN_TEST_WINDOW_SECONDS", "300"))
# "Check all" runs in the background (bluesky_monitor/jobs.py). New accounts stop being
# started after this long; the rest are left for the next scheduled check.
CHECK_ALL_TIME_LIMIT_SECONDS = int(os.getenv("CHECK_ALL_TIME_LIMIT_SECONDS", "600"))
CHECK_JOB_POLL_SECONDS = float(os.getenv("CHECK_JOB_POLL_SECONDS", "2"))  # how often the checker looks for jobs
CHECK_JOB_QUEUE_TIMEOUT_SECONDS = int(os.getenv("CHECK_JOB_QUEUE_TIMEOUT_SECONDS", "120"))  # then "expired"
CHECK_JOBS_KEPT = int(os.getenv("CHECK_JOBS_KEPT", "50"))  # finished jobs kept for the panel

_LOG_LEVEL = os.getenv("BOT_LOG_LEVEL", "WARNING" if _RUNNING_TESTS else "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact_webhooks": {"()": "discord_alerts.logfilter.RedactWebhooks"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "filters": ["redact_webhooks"]}},
    "loggers": {
        # Django's own messages (request errors, server log) also go through the filter.
        "django": {"handlers": ["console"], "level": "ERROR" if _RUNNING_TESTS else "INFO", "propagate": False},
        "apscheduler": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "urllib3": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "bluesky_monitor": {"handlers": ["console"], "level": _LOG_LEVEL},
        "discord_alerts": {"handlers": ["console"], "level": _LOG_LEVEL},
        "botconfig": {"handlers": ["console"], "level": _LOG_LEVEL},
        "activity": {"handlers": ["console"], "level": _LOG_LEVEL},
        "config": {"handlers": ["console"], "level": "CRITICAL" if _RUNNING_TESTS else "INFO"},
    },
}
