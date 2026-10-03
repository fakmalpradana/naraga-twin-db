from django.conf import settings
from django.db import connections, transaction


class CorsMiddleware:
    """Read-only API: allow GET from the configured frontend origins (ALLOWED_ORIGINS)."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.headers.get("Origin")
        if request.method == "OPTIONS" and origin and request.path.startswith("/api/"):
            from django.http import HttpResponse
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)
        if origin and request.path.startswith("/api/") and ("*" in settings.ALLOWED_ORIGINS or origin in settings.ALLOWED_ORIGINS):
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type"
            response["Vary"] = "Origin"
        return response


class AppUserMiddleware:
    """Every admin request that changes data runs in one transaction with app.user set to the
    signed-in user, so the database stamps and audits the real person (empty user => write rejected)."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if request.path.startswith("/admin/") and user is not None and user.is_authenticated:
            with transaction.atomic(using="default"):
                with connections["default"].cursor() as cur:
                    cur.execute("SELECT set_config('app.user', %s, true)", [user.get_username()])
                return self.get_response(request)
        return self.get_response(request)
