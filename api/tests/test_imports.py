"""Upload (POST /imports) and download (GET /datasets/{code}/export) against the real database and citydb-tool.
Uses its own dataset zz_imp (LOD1 layer, so the tile rebuild runs too) and removes it after."""
import io, json, shutil, time
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client, override_settings

from catalog import edit
from .conftest import owner

TOKEN = "pytest-token"
H = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}", "HTTP_X_USER": "pytest"}


def square(lon, lat, d=0.0002):
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]


def cleanup():
    edit._citydb(["delete"], ["--delete-mode=delete", "-f", "lineage = 'zz_imp.building.lod1'"])
    with owner() as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.user','pytest',false)")
        cur.execute("DELETE FROM catalog.tileset WHERE layer_id IN (SELECT l.id FROM catalog.layer l JOIN catalog.dataset d ON d.id=l.dataset_id WHERE d.code='zz_imp')")
        cur.execute("DELETE FROM catalog.import_job_layer WHERE layer_id IN (SELECT l.id FROM catalog.layer l JOIN catalog.dataset d ON d.id=l.dataset_id WHERE d.code='zz_imp')")
        cur.execute("DELETE FROM catalog.layer WHERE dataset_id IN (SELECT id FROM catalog.dataset WHERE code='zz_imp')")
        cur.execute("DELETE FROM catalog.import_job WHERE dataset_id IN (SELECT id FROM catalog.dataset WHERE code='zz_imp')")
        cur.execute("DELETE FROM catalog.dataset WHERE code='zz_imp'")
    shutil.rmtree(Path(settings.ROOT) / "tiles" / "zz_imp", ignore_errors=True)


@pytest.fixture(scope="module")
def layer():
    cleanup()
    with owner() as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('app.user','pytest',false)")
        cur.execute("INSERT INTO catalog.dataset (code,name,generated_by,vertical_datum) VALUES ('zz_imp','Import test','manual','ellipsoid') RETURNING id")
        cur.execute("INSERT INTO catalog.layer (dataset_id,theme_code,lod,title) VALUES (%s,'building',1,'zz imp lod1') RETURNING id", (cur.fetchone()[0],))
        lid = cur.fetchone()[0]
    with override_settings(WRITE_API_TOKEN=TOKEN):
        yield str(lid)
    cleanup()


def upload(content, name="up.geojson", **form):
    f = io.BytesIO(content if isinstance(content, bytes) else json.dumps(content).encode()); f.name = name
    return Client().post("/api/v1/imports", {"file": f, "dataset": "zz_imp", **form}, **H)


def wait(job, timeout=180):
    for _ in range(timeout):
        body = Client().get(f"/api/v1/imports/{job}").json()
        if body["status"] != "running": return body
        time.sleep(1)
    raise AssertionError("import still running")


def test_sniff_format():
    assert edit.sniff_format(b'\xef\xbb\xbf<?xml version="1.0"?><core:CityModel') == "citygml"
    assert edit.sniff_format(b' {"type":"CityJSON","version":"2.0"') == "cityjson"
    assert edit.sniff_format(b'{"type": "FeatureCollection", "features": []}') == "geojson"
    assert edit.sniff_format(b"name,height\n") is None


def test_import_requires_token():
    r = Client().post("/api/v1/imports", {"dataset": "zz_imp"})
    assert r.status_code in (401, 503) and r.json()["error"]["code"] in ("unauthorized", "writes_disabled")


def test_import_rejects_bad_input(layer):
    r = upload(b"objectid;height\nA;3\n", name="x.csv")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_parameter"
    r = upload({"type": "FeatureCollection", "features": []}, dataset="zz_imp", theme="building", lod="2")
    assert r.status_code == 404
    assert Client().get("/api/v1/imports/not-a-uuid").status_code == 422
    assert Client().get("/api/v1/imports/00000000-0000-0000-0000-000000000000").status_code == 404


def test_geojson_upload_then_export(layer):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [square(116.7000, -0.9000)]},
         "properties": {"objectid": "ZZI_1", "height_m": 12.5, "base_z": 30, "nama": "Gedung Impor", "lantai": 3}},
        {"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": [[square(116.7010, -0.9000)]]},
         "properties": {"objectid": "ZZI_2", "height_m": 8}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [square(116.7020, -0.9000)]},
         "properties": {"objectid": "ZZI_3", "height_m": -1}},                      # skipped: bad height
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.7, -0.9]}, "properties": {"height_m": 5}}]}  # skipped
    r = upload(fc)
    assert r.status_code == 202, r.content
    job = r.json()["jobId"]
    assert r.json()["status"] == "running" and r.json()["format"] == "geojson"
    st = wait(job)
    assert st["status"] == "succeeded", st
    assert st["counts"] == {"Building": 2} and st["layerId"] == layer and st["createdBy"] == "api:pytest"
    assert [s["index"] for s in st["report"]["skipped"]] == [2, 3]
    assert st["report"]["tileset"]["buildings"] == 2 and st["finishedAt"]

    item = Client().get("/api/v1/catalog?dataset=zz_imp&status=all").json()["items"][0]
    assert item["featureCount"] == 2 and item["tileset"]["provider"] == "self_hosted" and item["isStale"] is False

    r = Client().get("/api/v1/datasets/zz_imp/export")
    assert r.status_code == 200 and r["Content-Disposition"] == 'attachment; filename="zz_imp.geojson"'
    feats = {f["properties"]["objectid"]: f for f in r.json()["features"]}
    assert set(feats) == {"ZZI_1", "ZZI_2"}
    p = feats["ZZI_1"]["properties"]
    assert p["height_m"] == pytest.approx(12.5, abs=0.01) and p["base_z"] == pytest.approx(30, abs=0.01)
    assert p["nama"] == "Gedung Impor" and p["lantai"] == 3 and p["lineage"] == "zz_imp.building.lod1"
    ring = feats["ZZI_1"]["geometry"]["coordinates"][0]
    assert feats["ZZI_1"]["geometry"]["type"] == "Polygon" and min(x for x, _ in ring) == pytest.approx(116.7, abs=1e-6)

    r = Client().get("/api/v1/datasets/zz_imp/export?format=cityjson")
    cj = json.loads(r.content)
    assert r.status_code == 200 and cj["type"] == "CityJSON" and set(cj["CityObjects"]) >= {"ZZI_1", "ZZI_2"}

    # the export is a valid upload; same ids already exist, so every feature is skipped and the job fails without importing
    st = wait(upload({"type": "FeatureCollection", "features": list(feats.values())}).json()["jobId"])
    assert st["status"] == "failed" and len(st["report"]["skipped"]) == 2 and "already exists" in st["report"]["skipped"][0]["reason"]


def test_export_errors(client):
    assert client.get("/api/v1/datasets/does_not_exist/export").status_code == 404
    r = client.get("/api/v1/datasets/oikn/export?format=shp")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_parameter"
