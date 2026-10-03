"""Admin UI for non-IT editors: inventory of everything, history of every record, friendly errors from DB rules."""
from django.conf import settings
from django.contrib import admin, messages
from django.db import DatabaseError, connection, transaction
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils.html import format_html

from .models import (ChangeLog, Dataset, ImportJob, Inventory, Layer, RefGeneratedBy, RefLodLevel, RefTheme, Tileset)

admin.site.site_header = "Digital Twin Catalog"
admin.site.site_title = "Digital Twin Catalog"
admin.site.index_title = "Catalog administration"
AUDIT_FIELDS = ("created_at", "created_by", "updated_at", "updated_by")


def db_message(exc):
    """Show the readable first line of a database rule violation."""
    cause = getattr(exc, "__cause__", None) or exc
    text = str(cause).strip().splitlines()[0] if str(cause).strip() else "Database rejected the change."
    return text.replace("CONTEXT:", "").strip()


class CatalogAdmin(admin.ModelAdmin):
    """Edit switch (ADMIN_EDIT_ENABLED), friendly DB errors, and a History page from audit.change_log."""
    readonly_fields = AUDIT_FIELDS

    def _edit_ok(self, request):
        return settings.ADMIN_EDIT_ENABLED

    def has_add_permission(self, request):
        return self._edit_ok(request) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._edit_ok(request) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._edit_ok(request) and super().has_delete_permission(request, obj)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            with transaction.atomic():
                response = super().changeform_view(request, object_id, form_url, extra_context)
                with connection.cursor() as cur:   # surface deferred rules now, not at commit
                    cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    cur.execute("SET CONSTRAINTS ALL DEFERRED")
                return response
        except DatabaseError as exc:
            messages.error(request, f"Not saved: {db_message(exc)}")
            return redirect(request.path)

    def delete_view(self, request, object_id, extra_context=None):
        try:
            with transaction.atomic():
                return super().delete_view(request, object_id, extra_context)
        except DatabaseError as exc:
            messages.error(request, f"Not deleted: {db_message(exc)}")
            return redirect(request.path)

    def history_view(self, request, object_id, extra_context=None):
        table = self.model._meta.db_table
        rows = ChangeLog.objects.filter(table_name=f"catalog.{table}", pk_value=object_id).order_by("-at")[:200]
        entries = []
        for r in rows:
            old, new = r.old_row or {}, r.new_row or {}
            diff = [(k, old.get(k), new.get(k)) for k in sorted(set(old) | set(new))
                    if old.get(k) != new.get(k) and k not in ("updated_at", "updated_by")]
            entries.append({"at": r.at, "user": r.app_user, "op": r.op, "diff": diff})
        ctx = {**self.admin_site.each_context(request), "title": f"History of {object_id}", "entries": entries,
               "opts": self.model._meta}
        return TemplateResponse(request, "admin/audit_history.html", ctx)


@admin.register(Dataset)
class DatasetAdmin(CatalogAdmin):
    list_display = ("code", "name", "region", "generated_by", "vertical_datum", "status", "updated_by", "updated_at")
    list_filter = ("status", "generated_by", "vertical_datum")
    search_fields = ("code", "name", "region")
    fieldsets = (
        (None, {"fields": ("code", "name", "description", "region", "status")}),
        ("Source", {"fields": ("source_system", "owner_org", "contact_email", "generated_by", "license", "attribution")}),
        ("Coordinates and heights", {"fields": ("crs_epsg", "vertical_datum")}),
        ("Record", {"fields": AUDIT_FIELDS}),
    )


@admin.register(Layer)
class LayerAdmin(CatalogAdmin):
    list_display = ("__str__", "title", "status", "updated_by", "updated_at")
    list_filter = ("status", "lod", "theme_code", "dataset")
    search_fields = ("title", "dataset__code")
    list_select_related = ("dataset", "theme_code", "lod", "status")


@admin.register(Tileset)
class TilesetAdmin(CatalogAdmin):
    list_display = ("__str__", "provider", "ion_asset_id", "status", "is_active", "published_at")
    list_filter = ("status", "is_active", "provider")
    readonly_fields = AUDIT_FIELDS + ("published_at",)
    actions = ["make_active"]

    @admin.action(description="Make this build the active one (retires the current one)")
    def make_active(self, request, queryset):
        if not settings.ADMIN_EDIT_ENABLED:
            self.message_user(request, "Editing is switched off.", messages.WARNING)
            return
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one tileset.", messages.ERROR)
            return
        ts = queryset.first()
        try:
            with transaction.atomic():
                Tileset.objects.filter(layer_id=ts.layer_id, is_active=True).exclude(pk=ts.pk).update(is_active=False, status="retired")
                Tileset.objects.filter(pk=ts.pk).update(is_active=True)
                with connection.cursor() as cur:
                    cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    cur.execute("SET CONSTRAINTS ALL DEFERRED")
            self.message_user(request, f"{ts} is now active.")
        except DatabaseError as exc:
            self.message_user(request, f"Not switched: {db_message(exc)}", messages.ERROR)


@admin.register(ImportJob)
class ImportJobAdmin(CatalogAdmin):
    list_display = ("source_file", "dataset", "status", "validation_passed", "started_at", "created_by")
    list_filter = ("status", "validation_passed", "dataset")
    search_fields = ("source_file", "source_sha256")
    readonly_fields = AUDIT_FIELDS + ("layers_filled",)

    @admin.display(description="Layers filled by this job")
    def layers_filled(self, obj):
        if not obj or not obj.pk:
            return "-"
        with connection.cursor() as cur:
            cur.execute("""SELECT d.code || '.' || l.theme_code || '.lod' || l.lod FROM catalog.import_job_layer j
                           JOIN catalog.layer l ON l.id = j.layer_id JOIN catalog.dataset d ON d.id = l.dataset_id
                           WHERE j.job_id = %s ORDER BY 1""", [obj.pk])
            return ", ".join(r[0] for r in cur.fetchall()) or "-"


class ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(Inventory)
class InventoryAdmin(ReadOnly):
    list_display = ("dataset_code", "theme_code", "lod", "status", "feature_count", "tileset_version", "provider",
                    "stale", "updated_by", "updated_at")
    list_filter = ("status", "lod", "theme_code", "dataset_code", "is_stale")
    search_fields = ("dataset_code", "dataset_name", "title")
    ordering = ("dataset_code", "theme_code", "lod")

    @admin.display(boolean=True, description="Stale")
    def stale(self, obj): return obj.is_stale

    def has_view_permission(self, request, obj=None): return request.user.is_active and request.user.is_staff


@admin.register(ChangeLog)
class ChangeLogAdmin(ReadOnly):
    list_display = ("at", "app_user", "op", "table_name", "pk_value")
    list_filter = ("op", "table_name", "app_user")
    search_fields = ("app_user", "pk_value")
    ordering = ("-at",)
    date_hierarchy = "at"


class RefAdmin(CatalogAdmin):
    readonly_fields = ()


for model in (RefTheme, RefGeneratedBy):
    admin.site.register(model, RefAdmin)
admin.site.register(RefLodLevel, type("RefLodAdmin", (ReadOnly,), {}))
