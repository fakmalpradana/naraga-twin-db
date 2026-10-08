from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import path
from django.views.decorators.cache import never_cache
from django.views.static import serve as _serve

serve = never_cache(_serve)   # viewer/client are edited often; avoid stale cached copies
from django.conf import settings
from catalog.api import api

urlpatterns = [
    path("", lambda r: HttpResponseRedirect("/viewer/")),
    path("admin/", admin.site.urls),
    path("api/v1/", api.urls),
    path("viewer/", serve, {"document_root": settings.ROOT / "viewer", "path": "index.html"}),
    path("viewer/<path:path>", serve, {"document_root": settings.ROOT / "viewer"}),
    path("tiles/<path:path>", serve, {"document_root": settings.ROOT / "tiles"}),   # self-hosted 3D Tiles (volume ./tiles)
    path("client/<path:path>", serve, {"document_root": settings.ROOT / "client"}),
]
