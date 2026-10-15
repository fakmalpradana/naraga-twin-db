"""Bulk version of POST /api/v1/features: same checks and CityGML builder as the API, but ONE citydb-tool import,
ONE import_job and ONE tile rebuild for the whole batch (the API pays ~7 s of citydb-tool start-up per building).
Run inside the api container:  docker compose exec -T api python manage.py shell < scripts/bulk_create.py
Env: BODIES=/tmp/bodies.json (list of POST /features bodies, one layer), BULK_USER=<name>, BULK_SOURCE=<label>."""
import json, os, re, tempfile
from pathlib import Path
from ninja.errors import HttpError
from catalog import edit

bodies = json.loads(Path(os.environ.get("BODIES", "/tmp/bodies.json")).read_text())
user, source = os.environ.get("BULK_USER", "bulk"), os.environ.get("BULK_SOURCE", "bulk")
layers = {(b["dataset"], b.get("theme", "building")) for b in bodies}
assert len(layers) == 1, f"one layer per batch, got {layers}"
dataset, theme = layers.pop()
lineage, srid = f"{dataset}.{theme}.lod1", edit.db_srid()
edit.layer_row(dataset, theme, 1)

members, skipped = [], []
for b in bodies:
    oid = b["objectid"]
    try:
        if not edit.ID_RE.fullmatch(oid): raise HttpError(422, "bad objectid")
        if not 0 < b["height_m"] <= 1000: raise HttpError(422, "height_m must be between 0 and 1000")
        if edit.feature_row(oid): raise HttpError(409, "already exists")
        rings = edit.lonlat_to_model([b["footprint"], *b.get("holes", [])], srid)
        base = b["base_z"] if b.get("base_z") is not None else edit.mean_ground_z(lineage)
        gml = edit.building_gml(oid, {k: v for k, v in b.get("attributes", {}).items() if v is not None}, rings, base, b["height_m"], srid)
        members.append(re.search(r"<core:cityObjectMember>.*</core:cityObjectMember>", gml, re.S).group(0))
    except HttpError as e:
        skipped.append({"objectid": oid, "reason": str(e)})
assert members, f"nothing to import; skipped: {skipped}"
head = gml[:gml.index("<core:cityObjectMember>")]
doc = head + "".join(members) + "</core:CityModel>"


def work(cur, layer):
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "bulk.gml"; f.write_text(doc)
        edit._citydb(["import", "citygml"], ["--lineage", lineage, "--reason-for-update", f"api:bulk:{source}", "--updating-person", user,
                                             "-m", "import_all", "--compute-extent", str(f)], timeout=3600)
    return {"objectid": None, "action": "bulk_created", "job": ("import_all", source, doc, {"Building": len(members)})}


res = edit.operation(user, dataset, theme, 1, work, rebuild=True)
print(json.dumps({"imported": len(members), "skipped": skipped, **res}))
