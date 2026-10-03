"""Unmanaged models over the SQL-migrated tables: SQL migrations own the schema, Django only reads/writes rows.
Triggers fill created_*/updated_*; bbox is computed in the database and is not mapped."""
import uuid
from django.db import models
from django.utils import timezone


class Audit(models.Model):
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    created_by = models.TextField(default="", editable=False)
    updated_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_by = models.TextField(default="", editable=False)

    class Meta:
        abstract = True
        managed = False


class RefLodLevel(models.Model):
    lod = models.SmallIntegerField(primary_key=True)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)

    class Meta:
        managed = False; db_table = "ref_lod_level"; verbose_name = "LOD level"
    def __str__(self): return f"LOD{self.lod}"


class RefTheme(models.Model):
    code = models.TextField(primary_key=True)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    sort = models.IntegerField(default=100)

    class Meta:
        managed = False; db_table = "ref_theme"; verbose_name = "theme"
    def __str__(self): return self.name


class RefGeneratedBy(models.Model):
    code = models.TextField(primary_key=True)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)

    class Meta:
        managed = False; db_table = "ref_generated_by"; verbose_name = "producer type"
    def __str__(self): return self.name


class RefLayerStatus(models.Model):
    code = models.TextField(primary_key=True)
    name = models.TextField()
    sort = models.IntegerField()

    class Meta:
        managed = False; db_table = "ref_layer_status"
    def __str__(self): return self.name


class RefTilesetStatus(models.Model):
    code = models.TextField(primary_key=True)
    name = models.TextField()
    sort = models.IntegerField()

    class Meta:
        managed = False; db_table = "ref_tileset_status"
    def __str__(self): return self.name


class Dataset(Audit):
    VDATUM = [(v, v) for v in ("ellipsoid", "egm2008", "egm96", "local", "unknown")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.TextField(unique=True, help_text="Short unique name (a-z, 0-9, _). Cannot be changed once data is imported.")
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    region = models.TextField(null=True, blank=True)
    source_system = models.TextField(null=True, blank=True)
    owner_org = models.TextField(null=True, blank=True)
    contact_email = models.TextField(null=True, blank=True)
    generated_by = models.ForeignKey(RefGeneratedBy, models.DO_NOTHING, db_column="generated_by", default="external")
    crs_epsg = models.IntegerField(null=True, blank=True, help_text="Coordinate system of the source files (record only).")
    vertical_datum = models.TextField(choices=VDATUM, default="unknown")
    license = models.TextField(null=True, blank=True)
    attribution = models.TextField(null=True, blank=True, help_text="Credit line shown on the map.")
    status = models.TextField(choices=[("active", "active"), ("archived", "archived")], default="active")

    class Meta:
        managed = False; db_table = "dataset"
    def __str__(self): return self.code


class ImportJob(Audit):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, models.DO_NOTHING)
    tool = models.TextField(default="citydb-tool")
    tool_version = models.TextField(null=True, blank=True)
    citygml_version = models.TextField(null=True, blank=True)
    source_file = models.TextField()
    source_sha256 = models.TextField(null=True, blank=True)
    source_size_bytes = models.BigIntegerField(null=True, blank=True)
    import_mode = models.TextField(default="import_all")
    lineage_tag = models.TextField(null=True, blank=True)
    status = models.TextField(default="running")
    validation_passed = models.BooleanField(null=True, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    report = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False; db_table = "import_job"; verbose_name = "import job (upload)"
    def __str__(self): return f"{self.source_file} ({self.status})"


class Layer(Audit):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, models.DO_NOTHING)
    theme_code = models.ForeignKey(RefTheme, models.DO_NOTHING, db_column="theme_code", verbose_name="theme")
    lod = models.ForeignKey(RefLodLevel, models.DO_NOTHING, db_column="lod")
    title = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    status = models.ForeignKey(RefLayerStatus, models.DO_NOTHING, db_column="status", default="draft")
    validated_by_job = models.ForeignKey(ImportJob, models.DO_NOTHING, null=True, blank=True)

    class Meta:
        managed = False; db_table = "layer"
    def __str__(self): return f"{self.dataset_id and self.dataset.code}.{self.theme_code_id}.lod{self.lod_id}"


class Tileset(Audit):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    layer = models.ForeignKey(Layer, models.DO_NOTHING)
    version = models.IntegerField(default=1)
    provider = models.TextField(choices=[("cesium_ion", "cesium_ion"), ("self_hosted", "self_hosted")])
    ion_asset_id = models.BigIntegerField(null=True, blank=True)
    url = models.TextField(null=True, blank=True)
    converter_name = models.TextField(null=True, blank=True)
    converter_version = models.TextField(null=True, blank=True)
    source_snapshot_at = models.DateTimeField(null=True, blank=True)
    height_offset_m = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    status = models.ForeignKey(RefTilesetStatus, models.DO_NOTHING, db_column="status", default="building")
    is_active = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        managed = False; db_table = "tileset"
    def __str__(self): return f"{self.layer} v{self.version}"


class Inventory(models.Model):
    """Read-only view catalog.v_layer: the inventory page for non-IT users."""
    layer_id = models.UUIDField(primary_key=True)
    dataset_code = models.TextField()
    dataset_name = models.TextField()
    theme_code = models.TextField()
    lod = models.SmallIntegerField()
    title = models.TextField(null=True)
    status = models.TextField()
    feature_count = models.BigIntegerField()
    tileset_version = models.IntegerField(null=True)
    provider = models.TextField(null=True)
    ion_asset_id = models.BigIntegerField(null=True)
    is_stale = models.BooleanField()
    updated_at = models.DateTimeField()
    updated_by = models.TextField()

    class Meta:
        managed = False; db_table = "v_layer"; verbose_name = "inventory (all layers)"; verbose_name_plural = "inventory (all layers)"


class ChangeLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    at = models.DateTimeField()
    app_user = models.TextField()
    table_name = models.TextField()
    pk_value = models.TextField(db_column="pk")
    op = models.TextField()
    old_row = models.JSONField(null=True)
    new_row = models.JSONField(null=True)

    class Meta:
        managed = False; db_table = '"audit"."change_log"'; verbose_name = "change history"; verbose_name_plural = "change history"
