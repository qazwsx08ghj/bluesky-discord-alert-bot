"""While this version must not use the data, every request gets a warning instead of the panel.

docker/entrypoint.sh sets ALERTBOT_BLOCKED=newer_data when prepare_data finds that a newer
version updated the data (botconfig/compat.py). Then:
- pages: a short page saying what happened and what to do (no scripts, no database);
- /api/...: 503 {"code": "newer_data", "status": "newer_data", "message", "version"}. The
  health check is one of them, so Docker, the scripts and the panel see that it isn't running.
Nothing reads or changes the database while blocked.
"""
from html import escape

from django.conf import settings
from django.http import HttpResponse, JsonResponse

from botconfig import compat

HEADERS = {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}

PAGE_ZH = ("這是比上次使用你資料的版本還「舊」的機器人版本。為了不改動或損壞你的資料，這個舊版本不會執行。"
           "要繼續用新版本：在你「最新下載」的資料夾裡點兩下 start.bat。"
           "要刻意回到這個舊版本：在這個舊版本的資料夾裡點兩下 restore.bat，選一個更新前的備份"
           "（檔名開頭是 db-before-update）。")


def _page():
    items = "".join(f"<li>{escape(line[2:])}</li>" for line in compat.MESSAGE.splitlines() if line.startswith("- "))
    first = escape(compat.MESSAGE.splitlines()[0])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Alert Bot: older version</title>'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"></head><body>'
            f"<h1>{escape(compat.TITLE)}</h1><p>{first}</p><ul>{items}</ul>"
            f'<p lang="zh-Hant-TW">{escape(PAGE_ZH)}</p>'
            f"<p><small>Version {escape(settings.APP_VERSION)}</small></p></body></html>")


class BlockedMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.ALERTBOT_BLOCKED:
            return self.get_response(request)
        if request.path.startswith("/api/"):
            response = JsonResponse({"code": compat.BLOCKED_NEWER_DATA, "status": compat.BLOCKED_NEWER_DATA,
                                     "message": f"{compat.TITLE}. {compat.MESSAGE}",
                                     "version": settings.APP_VERSION}, status=503)
        else:
            response = HttpResponse(_page(), status=503, content_type="text/html; charset=utf-8")
        for name, value in HEADERS.items():
            response[name] = value
        return response
