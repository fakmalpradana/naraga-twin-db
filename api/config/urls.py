from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import path
from django.views.decorators.cache import never_cache
from django.http import Http404
from django.views.static import serve as _serve

serve = never_cache(_serve)   # viewer/client are edited often; avoid stale cached copies
from django.conf import settings
from catalog.api import api, rows

def stable_tile(request, dataset, theme, lod, name):
    """Version-less tile URL: /tiles/<dataset>/<theme>/lod<N>/tileset.json (and its .glb) = the layer's ACTIVE tileset version."""
    r = rows("""SELECT t.url FROM catalog.tileset t JOIN catalog.layer l ON l.id = t.layer_id JOIN catalog.dataset d ON d.id = l.dataset_id
                WHERE d.code = %s AND l.theme_code = %s AND l.lod = %s AND t.is_active AND t.provider = 'self_hosted'""", [dataset, theme, lod])
    if not r: raise Http404
    vdir = r[0]["url"].rsplit("/", 1)[0].removeprefix("/tiles/")   # e.g. oikn_kipp/building/lod1/v3
    return serve(request, f"{vdir}/{name}", document_root=settings.ROOT / "tiles")


urlpatterns = [
    path("", lambda r: HttpResponseRedirect("/viewer/")),
    path("admin/", admin.site.urls),
    path("api/v1/", api.urls),
    path("viewer/", serve, {"document_root": settings.ROOT / "viewer", "path": "index.html"}),
    path("viewer/<path:path>", serve, {"document_root": settings.ROOT / "viewer"}),
    path("tiles/<str:dataset>/<str:theme>/lod<int:lod>/<str:name>", stable_tile),
    path("tiles/<path:path>", serve, {"document_root": settings.ROOT / "tiles"}),   # self-hosted 3D Tiles (volume ./tiles)
    path("client/<path:path>", serve, {"document_root": settings.ROOT / "client"}),
]
