"""Integration tests against the real running database (docker compose). Creates dataset zz_test and removes it after."""
import os, sys
import pytest
import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.test import Client

OWNER = os.environ["DATABASE_URL"]


def owner():
    return psycopg.connect(OWNER, autocommit=True)


def cleanup(cur):
    cur.execute("SELECT set_config('app.user','pytest',false)")
    cur.execute("DELETE FROM citydb.property WHERE feature_id IN (SELECT id FROM citydb.feature WHERE lineage LIKE 'zz\\_test.%')")
    cur.execute("DELETE FROM citydb.feature WHERE lineage LIKE 'zz\\_test.%'")
    cur.execute("UPDATE catalog.layer SET status='archived' WHERE status='published' AND dataset_id IN (SELECT id FROM catalog.dataset WHERE code='zz_test')")  # retires tilesets
    cur.execute("DELETE FROM catalog.tileset WHERE layer_id IN (SELECT l.id FROM catalog.layer l JOIN catalog.dataset d ON d.id=l.dataset_id WHERE d.code='zz_test')")
    cur.execute("DELETE FROM catalog.import_job_layer WHERE layer_id IN (SELECT l.id FROM catalog.layer l JOIN catalog.dataset d ON d.id=l.dataset_id WHERE d.code='zz_test')")
    cur.execute("DELETE FROM catalog.layer WHERE dataset_id IN (SELECT id FROM catalog.dataset WHERE code='zz_test')")
    cur.execute("DELETE FROM catalog.import_job WHERE dataset_id IN (SELECT id FROM catalog.dataset WHERE code='zz_test')")
    cur.execute("DELETE FROM catalog.dataset WHERE code='zz_test'")


@pytest.fixture(scope="session")
def data():
    with owner() as conn, conn.cursor() as cur:
        cleanup(cur)
        cur.execute("SELECT set_config('app.user','pytest',false)")
        cur.execute("INSERT INTO catalog.dataset (code,name,generated_by,vertical_datum,attribution) VALUES ('zz_test','Test data','manual','ellipsoid','(c) test') RETURNING id")
        ds = cur.fetchone()[0]
        out = {}
        for lod in (1, 2):
            cur.execute("INSERT INTO catalog.layer (dataset_id,theme_code,lod,title) VALUES (%s,'building',%s,%s) RETURNING id", (ds, lod, f"zz lod{lod}"))
            out[lod] = cur.fetchone()[0]
        cur.execute("INSERT INTO catalog.import_job (dataset_id,source_file,status,validation_passed) VALUES (%s,'zz.gml','succeeded',true) RETURNING id", (ds,))
        job = cur.fetchone()[0]
        cur.execute("INSERT INTO catalog.import_job_layer VALUES (%s,%s)", (job, out[1]))
        cur.execute("UPDATE catalog.layer SET status='validated', validated_by_job_id=%s WHERE id=%s", (job, out[1]))
        cur.execute("INSERT INTO catalog.tileset (layer_id,version,provider,ion_asset_id,status) VALUES (%s,1,'cesium_ion',424242,'ready') RETURNING id", (out[1],))
        out["ts1"] = cur.fetchone()[0]
        cur.execute("INSERT INTO catalog.tileset (layer_id,version,provider,ion_asset_id,status) VALUES (%s,2,'cesium_ion',424243,'ready') RETURNING id", (out[1],))
        out["ts2"] = cur.fetchone()[0]
        cur.execute("UPDATE catalog.tileset SET is_active=true WHERE id=%s", (out["ts1"],))
        cur.execute("UPDATE catalog.layer SET status='published' WHERE id=%s", (out[1],))
        # one building in the 3D city model (feature + name + height), inside the IKN area, SRID of the db
        cur.execute("""INSERT INTO citydb.feature (objectclass_id, objectid, lineage, envelope)
                       SELECT 901, 'zz-b1', 'zz_test.building.lod1',
                              ST_Transform(ST_SetSRID(ST_GeomFromText('POLYGON Z((116.70 -0.90 0, 116.71 -0.90 0, 116.71 -0.89 12, 116.70 -0.89 12, 116.70 -0.90 0))'), 4326), srid)
                       FROM citydb.database_srs RETURNING id""")
        fid = cur.fetchone()[0]
        cur.execute("SELECT id FROM citydb.datatype LIMIT 1")
        dt = cur.fetchone()[0]
        cur.execute("INSERT INTO citydb.property (feature_id, datatype_id, name, val_string) VALUES (%s,%s,'name','Gedung Uji')", (fid, dt))
        cur.execute("INSERT INTO citydb.property (feature_id, datatype_id, name, val_double, val_uom) VALUES (%s,%s,'measuredHeight',12.5,'m')", (fid, dt))
        cur.execute("INSERT INTO citydb.property (feature_id, datatype_id, name, val_lod) VALUES (%s,%s,'lod1Solid','1')", (fid, dt))
        cur.execute("SELECT set_config('app.user','pytest',false)"); cur.execute("SELECT catalog.refresh_derived()")
        out["ds"] = ds
    yield out
    with owner() as conn, conn.cursor() as cur:
        cleanup(cur)


@pytest.fixture
def client():
    return Client()
