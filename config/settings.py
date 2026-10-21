import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
DEBUG = os.getenv("DEBUG", "True") == "True"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_apscheduler",
    "botconfig",
    "bluesky_monitor",
    "discord_alerts",
    "activity",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

APSCHEDULER_DATETIME_FORMAT = "N j, Y, f:s a"
APSCHEDULER_RUN_NOW_TIMEOUT = 25

_RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == "test"

# Bot settings live in the database since step 3 (botconfig/store.py). These .env values
# are read only once, to fill the database the first time. Edits here are ignored after that.
# Tests never see the real values; they set their own.
def _legacy(key, default=""):
    return default if _RUNNING_TESTS else os.getenv(key, default)


BLUESKY_HANDLE = _legacy("BLUESKY_HANDLE")
BLUESKY_PASSWORD = _legacy("BLUESKY_PASSWORD")
DISCORD_WEBHOOK_URL = _legacy("DISCORD_WEBHOOK_URL")
POLL_INTERVAL_MINUTES = _legacy("POLL_INTERVAL_MINUTES", "5")

# Saved settings are also copied into this .env file so older versions keep working after a
# rollback. Temporary; removed in step 10 (see docs/SETTINGS_MIGRATION.md). Empty = off.
# Off while running tests, so tests can never touch the real .env.
SETTINGS_ENV_MIRROR_PATH = os.environ.get(
    "SETTINGS_ENV_MIRROR_PATH", "" if _RUNNING_TESTS else str(BASE_DIR / ".env"))

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
# /api/status/: a check running longer than this counts as stuck; a check later than
# this past its planned time counts as overdue.
SCHEDULER_STALL_MINUTES = int(os.getenv("SCHEDULER_STALL_MINUTES", "10"))
SCHEDULER_OVERDUE_GRACE_SECONDS = int(os.getenv("SCHEDULER_OVERDUE_GRACE_SECONDS", "120"))

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
    },
}
