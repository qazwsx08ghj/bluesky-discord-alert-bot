"""Request security for a panel that runs on the owner's own computer, with no login.

The threat: a web page the owner visits in the same browser tries to send requests to
http://127.0.0.1:8000 (cross-site request forgery), or uses a DNS name that points at
127.0.0.1 (DNS rebinding). Several independent layers stop that; none relies on a single
header (see docs/API.md, "Security"):

1. Only local host names are served (ALLOWED_HOSTS: localhost, 127.0.0.1, [::1]), and the
   server listens on 127.0.0.1 only. Requests for any other name get 400 (DNS rebinding).
2. For every request that changes something (POST, PUT, PATCH, DELETE), all of:
   a. Fetch metadata: a browser marks requests from another site with
      `Sec-Fetch-Site: cross-site`. Those are refused.
   b. Origin: when sent, it must be this site (or one listed in CSRF_TRUSTED_ORIGINS).
   c. CSRF token: the `X-CSRFToken` header must match the `csrftoken` cookie. Another
      site can't read the cookie, so it can't send the token. Django's own check, applied
      to the API too (DRF normally exempts its views because it expects a login).
3. Request bodies must be JSON (DRF's JSONParser only). HTML forms can't send JSON, and a
   cross-site script sending JSON needs CORS permission, which is never given (no CORS headers).
4. API responses are never cached, can't be framed, and aren't sniffed as other types.

Refusals are JSON: {"code": "cross_site_request" | "csrf_failed", "message": ...}, status 403.
"""
import logging
import re

from django.conf import settings
from django.core.checks import Warning, register
from django.http import JsonResponse
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.views.debug import SafeExceptionReporterFilter
from rest_framework import exceptions
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger("config.security")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})

CROSS_SITE_MESSAGE = ("This request came from another website, so it was refused. "
                      "Use the control panel at http://127.0.0.1:8000 to make changes.")
CSRF_MESSAGE = ("This request is missing its security token, so it was refused. "
                "Reload the control panel and try again.")


def _has_body(request) -> bool:
    if request.META.get("HTTP_TRANSFER_ENCODING"):
        return True
    try:
        return int(request.META.get("CONTENT_LENGTH") or 0) > 0
    except ValueError:
        return True


def _is_json(content_type: str) -> bool:
    content_type = (content_type or "").lower()
    return content_type == "application/json" or (content_type.startswith("application/")
                                                  and content_type.endswith("+json"))


def _checked_view(request, *args, **kwargs):
    """Stand-in for the real view when running Django's CSRF check: it is never csrf_exempt."""


class RequestGuardMiddleware(CsrfViewMiddleware):
    """Replaces django.middleware.csrf.CsrfViewMiddleware; see the module docstring (layer 2)."""

    def process_view(self, request, callback, callback_args, callback_kwargs):
        if request.method in UNSAFE_METHODS:
            site = request.META.get("HTTP_SEC_FETCH_SITE", "")
            origin = request.META.get("HTTP_ORIGIN", "")
            if site == "cross-site" or (site == "same-site" and origin not in settings.CSRF_TRUSTED_ORIGINS):
                return self._refuse(request, "cross_site_request", f"Sec-Fetch-Site: {site}")
            if _has_body(request) and not _is_json(request.content_type):
                # Layer 3, checked here before anything reads the body: forms and plain text never get in.
                response = JsonResponse({"code": "json_required", "message": ERROR_CODES["unsupported_media_type"][1]},
                                        status=415)
                response["Cache-Control"] = "no-store"
                return response
        # Django's Origin and token checks, even for views marked csrf_exempt (all DRF views are).
        return super().process_view(request, _checked_view, callback_args, callback_kwargs)

    def _reject(self, request, reason):
        """Called by Django's check with its reason text (no secrets in it)."""
        code = "cross_site_request" if reason.startswith(("Origin checking failed", "Referer")) else "csrf_failed"
        return self._refuse(request, code, reason)

    def _refuse(self, request, code, reason):
        logger.warning("Refused %s %s (%s): %s", request.method, request.path, code, reason)
        response = JsonResponse({"code": code, "message": CROSS_SITE_MESSAGE if code == "cross_site_request"
                                 else CSRF_MESSAGE}, status=403)
        response["Cache-Control"] = "no-store"
        return response


class ApiHeadersMiddleware:
    """Extra headers on /api/ responses (layer 4)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/api/"):
            response.setdefault("Cache-Control", "no-store")
            response.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            response.setdefault("X-Content-Type-Options", "nosniff")
        return response


@api_view(["GET"])
def csrf_token(request):
    """GET /api/csrf/ — sets the `csrftoken` cookie and returns the same token.

    Browsers: the control panel page already sets the cookie; read it, or call this once.
    Other clients (e.g. a React Native app): call this first, keep the cookie, and send
    the token back in the X-CSRFToken header on every POST, PUT, PATCH and DELETE.
    """
    return Response({"csrf_token": get_token(request), "header_name": "X-CSRFToken",
                     "cookie_name": settings.CSRF_COOKIE_NAME})


# -- uniform JSON errors --------------------------------------------------------------------------
ERROR_CODES = {
    "parse_error": ("invalid_json", "The request body isn't valid JSON."),
    "unsupported_media_type": ("json_required", "Send the request body as JSON (Content-Type: application/json)."),
    "not_found": ("not_found", "Not found."),
    "method_not_allowed": ("method_not_allowed", None),
    "not_acceptable": ("not_acceptable", None),
}


def api_exception_handler(exc, context):
    """DRF errors keep their `detail` and also get `code` and `message`, like every other API error.

    Field validation errors ({"field": ["..."]}) are left exactly as before. Unexpected
    errors become a JSON 500 without any technical details (those go to the log).
    """
    response = exception_handler(exc, context)
    if response is None:
        logger.error("Unexpected error in %s", context.get("request").path if context.get("request") else "API",
                     exc_info=(type(exc), exc, exc.__traceback__))
        return Response({"code": "internal", "message": "Something went wrong on the bot's side. "
                         "Try again; if it keeps happening, check the activity log."}, status=500)
    if isinstance(response.data, dict) and set(response.data) == {"detail"}:
        if isinstance(exc, exceptions.APIException):
            default = exc.default_code
        else:  # Django's Http404 / PermissionDenied, converted by DRF
            default = "not_found" if response.status_code == 404 else "permission_denied"
        code, message = ERROR_CODES.get(default, (default, None))
        response.data = {"code": code, "message": message or str(response.data["detail"]), **response.data}
    return response


class SafeReporterFilter(SafeExceptionReporterFilter):
    """Error pages (DEBUG) and error reports also hide webhook URLs and CSRF values."""

    hidden_settings = re.compile("API|TOKEN|KEY|SECRET|PASS|SIGNATURE|HTTP_COOKIE|WEBHOOK|CSRF", flags=re.I)


# -- startup warning ------------------------------------------------------------------------------
@register("security")
def check_local_only(app_configs=None, **kwargs):
    """Warn when the panel accepts host names other than this computer's. It has no login."""
    others = [h for h in settings.ALLOWED_HOSTS if h.lower() not in LOCAL_HOSTS | {"testserver"}]
    if not others:
        return []
    return [Warning(
        f"ALLOWED_HOSTS includes {', '.join(others)}. The control panel has no login, so anyone who can "
        "reach it under that name can change the bot.",
        hint="Keep ALLOWED_HOSTS=localhost,127.0.0.1 unless you have put a login in front of it.",
        id="alertbot.W001",
    )]


# -- Django's own 404 / 500 pages (used when DEBUG is off) ----------------------------------------
def page_not_found(request, exception):
    """JSON for unknown /api/ addresses, so API clients always get the same error shape."""
    from django.views import defaults

    if request.path.startswith("/api/"):
        return JsonResponse({"code": "not_found", "message": "There's no API endpoint at this address."}, status=404)
    return defaults.page_not_found(request, exception)


def server_error(request):
    from django.views import defaults

    if request.path.startswith("/api/"):
        return JsonResponse({"code": "internal", "message": "Something went wrong on the bot's side. Try again; "
                             "if it keeps happening, check the activity log."}, status=500)
    return defaults.server_error(request)
