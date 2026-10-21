"""Serving the React control panel (step 9). The API itself is unchanged.

The panel is built by `npm run build` (in Docker: the image's frontend stage) into
FRONTEND_DIST, normally frontend/dist:
    index.html          one page for every panel address (/, /setup/, /accounts/, …)
    assets/<file>       the scripts, styles and fonts, with a content hash in each name
    favicon.svg
    THIRD_PARTY_NOTICES.txt  the licenses of the software the bot ships, at /third-party-notices.txt

Django only reads that folder. Only names that look like built files are served, and only
from FRONTEND_DIST/assets, so nothing else on disk (the database, .env, backups) can ever
be reached through these addresses.

If the panel hasn't been built (only possible in a source checkout run without Docker),
a short page says how to build it. The API keeps working either way.
"""
import logging
import re
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_safe

logger = logging.getLogger("config.frontend")

# Strict, because the panel has no login: scripts, styles and fonts from this site only,
# avatars from Bluesky's image server, API calls to this site only, no framing, no forms
# posting elsewhere, no plugins.
APP_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "font-src 'self'",
    "img-src 'self' data: https://cdn.bsky.app",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,150}$")
ASSET_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

_page_cache = {"path": None, "mtime": None, "html": None}

NOT_BUILT = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Control panel not built</title>
<body style="font:15px/1.5 system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem">
<h1 style="font-size:1.3rem">The control panel isn't built yet</h1>
<p>This copy of the bot runs from its source code, and the control panel hasn't been built.
The Docker version (<b>setup.bat</b>) always includes it.</p>
<p>Developers: in the <code>frontend</code> folder run <code>npm ci</code> and <code>npm run build</code>,
then reload this page. See docs/FRONTEND.md.</p></body></html>"""


def _dist() -> Path:
    return Path(settings.FRONTEND_DIST)


def built_page() -> str | None:
    """The built index.html, read again only when the file changes."""
    path = _dist() / "index.html"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if _page_cache["path"] != path or _page_cache["mtime"] != mtime:
        _page_cache.update(path=path, mtime=mtime, html=path.read_text(encoding="utf-8"))
    return _page_cache["html"]


@require_safe
@ensure_csrf_cookie  # the panel's requests send this cookie's token back (config/security.py)
def app(request, *args, **kwargs):
    html = built_page()
    if html is None:
        logger.warning("The control panel isn't built (%s)", _dist())
        response = HttpResponse(NOT_BUILT, content_type="text/html; charset=utf-8", status=503)
        response["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
        response["X-Alertbot-Panel"] = "not-built"
        return response
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
    response["Content-Security-Policy"] = APP_CSP
    response["Cache-Control"] = "no-cache"  # a new version shows up on the next load
    response["X-Alertbot-Panel"] = "react"
    return response


def _file(path: Path, cache: str) -> FileResponse:
    content_type = ASSET_TYPES.get(path.suffix.lower())
    if content_type is None or not path.is_file():
        raise Http404("Not found")
    response = FileResponse(path.open("rb"), content_type=content_type)
    response["Cache-Control"] = cache
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_safe
def asset(request, name):
    if not ASSET_NAME.match(name) or ".." in name:
        raise Http404("Not found")
    folder = (_dist() / "assets").resolve()
    path = (folder / name).resolve()
    if path.parent != folder:
        raise Http404("Not found")
    # Names carry a content hash, so a file never changes: browsers may keep it.
    return _file(path, "public, max-age=31536000, immutable")


@require_safe
def favicon(request):
    return _file(_dist() / "favicon.svg", "public, max-age=86400")


@require_safe
def third_party_notices(request):
    """The license and copyright notices of the third-party software in the bot and the panel."""
    path = _dist() / "THIRD_PARTY_NOTICES.txt"
    if not path.is_file():
        raise Http404("Not found")
    response = FileResponse(path.open("rb"), content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["X-Content-Type-Options"] = "nosniff"
    return response
